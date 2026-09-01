#include "app_state.h"

#include <string.h>

#include "units.h"

#define MAX_LISTENERS 4

// Persisted so the watch paints last-known data at launch instead of
// "Connecting...". One key per vehicle because PERSIST_DATA_MAX_LENGTH
// is 256 bytes per key and four Vehicles do not fit in one. Bump the
// schema whenever the record's layout changes — reading a stale blob
// into a changed struct is how the screen fills with garbage.
#define PERSIST_SCHEMA_VERSION 2
#define PERSIST_KEY_SCHEMA 1
#define PERSIST_KEY_COUNT 2
#define PERSIST_KEY_UNIT_MILES 3
#define PERSIST_KEY_VEHICLE_0 16

// How far a vehicle's timestamp may run ahead of the one already on
// flash before the blob is rewritten for the sake of the timestamp
// alone — see app_state_apply_status.
#define PERSIST_STAMP_DRIFT_S (5 * 60)

static Vehicle s_vehicles[MAX_VEHICLES];
static int s_vehicle_count = 0;
static int s_current_index = 0;
static AppPhase s_phase = APP_PHASE_LOADING_LIST;
static bool s_busy = false;
static bool s_unit_miles = PBK_USE_MILES_DEFAULT;  // companion's UNIT_MILES overrides
static char s_error[APP_ERROR_LEN] = {0};
// Latched for the whole run of failures so the buzz fires on the
// OK -> error edge only. It can't be derived from s_error being empty:
// a retry clears the text optimistically, and the companion re-sends
// the same failure every poll.
static bool s_error_buzzed = false;

static AppStateListener s_listeners[MAX_LISTENERS];
static int s_listener_count = 0;

// What was on flash for each vehicle when its blob was last written,
// so the drift check in app_state_apply_status measures against the
// record an offline launch would restore rather than against the
// previous poll.
static time_t s_persisted_at[MAX_VEHICLES];

static bool persist_vehicle(int idx) {
  if (persist_write_data(PERSIST_KEY_VEHICLE_0 + idx, &s_vehicles[idx],
                         sizeof(Vehicle)) != (int)sizeof(Vehicle)) {
    return false;
  }
  s_persisted_at[idx] = s_vehicles[idx].updated_at;
  return true;
}

// The schema key is what makes the whole record readable, so it is
// deleted before anything else moves and written back only once every
// key it vouches for is down. Writing it last is not on its own enough:
// at an unchanged schema version the old value is already on flash, so
// a tear partway through would leave it vouching for a mix of new and
// stale vehicles. With the key gone, restore() discards the lot.
static void persist_all(void) {
  persist_delete(PERSIST_KEY_SCHEMA);
  bool ok = persist_write_int(PERSIST_KEY_COUNT, s_vehicle_count) > 0;
  ok = persist_write_bool(PERSIST_KEY_UNIT_MILES, s_unit_miles) > 0 && ok;
  for (int i = 0; i < s_vehicle_count; i++) ok = persist_vehicle(i) && ok;
  if (ok) persist_write_int(PERSIST_KEY_SCHEMA, PERSIST_SCHEMA_VERSION);
}

static void restore(void) {
  if (persist_read_int(PERSIST_KEY_SCHEMA) != PERSIST_SCHEMA_VERSION) return;
  int count = (int)persist_read_int(PERSIST_KEY_COUNT);
  if (count <= 0 || count > MAX_VEHICLES) return;
  for (int i = 0; i < count; i++) {
    if (persist_read_data(PERSIST_KEY_VEHICLE_0 + i, &s_vehicles[i],
                          sizeof(Vehicle)) != (int)sizeof(Vehicle)) {
      memset(s_vehicles, 0, sizeof(s_vehicles));
      return;
    }
    s_persisted_at[i] = s_vehicles[i].updated_at;
  }
  s_vehicle_count = count;
  // The unit choice has to come back with the distances it applies to:
  // an offline launch never hears the companion's UNIT_MILES, and
  // rendering restored kilometres as miles is worse than showing
  // nothing at all.
  s_unit_miles = persist_read_bool(PERSIST_KEY_UNIT_MILES);
  s_phase = APP_PHASE_RESTORED;
}

void app_state_init(void) {
  s_vehicle_count = 0;
  s_current_index = 0;
  s_phase = APP_PHASE_LOADING_LIST;
  s_busy = false;
  s_error[0] = 0;
  s_error_buzzed = false;
  s_listener_count = 0;
  restore();
}

void app_state_deinit(void) {
  s_listener_count = 0;
}

AppPhase app_state_phase(void) { return s_phase; }
int app_state_vehicle_count(void) { return s_vehicle_count; }
int app_state_current_index(void) { return s_current_index; }

const Vehicle *app_state_current_vehicle(void) {
  if (s_vehicle_count == 0) return NULL;
  return &s_vehicles[s_current_index];
}

const Vehicle *app_state_vehicle_at(int idx) {
  if (idx < 0 || idx >= s_vehicle_count) return NULL;
  return &s_vehicles[idx];
}

const char *app_state_error(void) {
  return s_error[0] ? s_error : NULL;
}

bool app_state_is_busy(void) { return s_busy; }

bool app_state_unit_miles(void) { return s_unit_miles; }

void app_state_set_unit_miles(bool v) {
  if (s_unit_miles == v) return;
  s_unit_miles = v;
  persist_write_bool(PERSIST_KEY_UNIT_MILES, v);
  app_state_notify();
}

void app_state_next_vehicle(void) {
  if (s_vehicle_count <= 0) return;
  s_current_index = (s_current_index + 1) % s_vehicle_count;
  app_state_notify();
}

void app_state_prev_vehicle(void) {
  if (s_vehicle_count <= 0) return;
  s_current_index = (s_current_index - 1 + s_vehicle_count) % s_vehicle_count;
  app_state_notify();
}

void app_state_apply_vehicle_list(const char (*ids)[VEHICLE_ID_LEN],
                                  const char (*nicks)[VEHICLE_NICK_LEN],
                                  int count) {
  if (count < 0) count = 0;
  if (count > MAX_VEHICLES) count = MAX_VEHICLES;

  // Preserve any cached status for IDs that still exist in the new list.
  Vehicle prev[MAX_VEHICLES];
  int prev_count = s_vehicle_count;
  memcpy(prev, s_vehicles, sizeof(prev));

  memset(s_vehicles, 0, sizeof(s_vehicles));
  for (int i = 0; i < count; i++) {
    strncpy(s_vehicles[i].id, ids[i], VEHICLE_ID_LEN - 1);
    strncpy(s_vehicles[i].nickname, nicks[i], VEHICLE_NICK_LEN - 1);
    for (int j = 0; j < prev_count; j++) {
      if (strcmp(prev[j].id, s_vehicles[i].id) == 0 && prev[j].have_status) {
        s_vehicles[i].have_status = true;
        s_vehicles[i].soc_pct = prev[j].soc_pct;
        s_vehicles[i].range_km = prev[j].range_km;
        s_vehicles[i].is_charging = prev[j].is_charging;
        s_vehicles[i].charge_kw_x10 = prev[j].charge_kw_x10;
        s_vehicles[i].charge_eta_min = prev[j].charge_eta_min;
        s_vehicles[i].plug = prev[j].plug;
        s_vehicles[i].doors_locked = prev[j].doors_locked;
        s_vehicles[i].outside_temp_c = prev[j].outside_temp_c;
        s_vehicles[i].odo_km = prev[j].odo_km;
        s_vehicles[i].is_climate_on = prev[j].is_climate_on;
        s_vehicles[i].aux_battery_pct = prev[j].aux_battery_pct;
        s_vehicles[i].updated_at = prev[j].updated_at;
        break;
      }
    }
  }
  s_vehicle_count = count;
  if (s_current_index >= s_vehicle_count) s_current_index = 0;
  s_phase = APP_PHASE_READY;
  s_error_buzzed = false;
  persist_all();
  app_state_notify();
}

void app_state_apply_status(const char *id, const VehicleStatus *status) {
  for (int i = 0; i < s_vehicle_count; i++) {
    if (strcmp(s_vehicles[i].id, id) == 0) {
      Vehicle *v = &s_vehicles[i];
      Vehicle before;
      memcpy(&before, v, sizeof(before));
      v->have_status = true;
      v->soc_pct = status->soc_pct;
      v->range_km = status->range_km;
      v->is_charging = status->is_charging;
      v->charge_kw_x10 = status->charge_kw_x10;
      v->charge_eta_min = status->charge_eta_min;
      v->plug = status->plug;
      v->doors_locked = status->doors_locked;
      v->outside_temp_c = status->outside_temp_c;
      v->odo_km = status->odo_km;
      v->is_climate_on = status->is_climate_on;
      v->aux_battery_pct = status->aux_battery_pct;
      v->updated_at = status->updated_at;
      s_error_buzzed = false;
      // Only write when a reading the user can see has moved. The
      // timestamp is kept out of that comparison: a source that
      // re-stamps it on every read (the demo one does) would otherwise
      // write to flash every fifteen seconds. Leaving it out entirely
      // is not safe either — the stamp would then never be persisted
      // while the readings sit still, and an offline launch would
      // restore an age wildly older than the data really is — so a
      // stamp that has run far enough ahead of the one on flash earns a
      // write on its own.
      bool stamp_drifted =
          v->updated_at - s_persisted_at[i] > PERSIST_STAMP_DRIFT_S;
      before.updated_at = v->updated_at;
      if (stamp_drifted || memcmp(&before, v, sizeof(Vehicle)) != 0) {
        persist_vehicle(i);
      }
      app_state_notify();
      return;
    }
  }
  // Status arrived for an id we don't know about. Silently drop.
}

void app_state_set_error(const char *msg) {
  if (msg == NULL) msg = "";
  strncpy(s_error, msg, sizeof(s_error) - 1);
  s_error[sizeof(s_error) - 1] = 0;
  if (s_error[0] && !s_error_buzzed) {
    s_error_buzzed = true;
    vibes_short_pulse();
  }
  app_state_notify();
}

void app_state_clear_error(void) {
  s_error[0] = 0;
  app_state_notify();
}

void app_state_set_busy(bool busy) {
  if (s_busy == busy) return;
  s_busy = busy;
  app_state_notify();
}

void app_state_subscribe(AppStateListener listener) {
  for (int i = 0; i < s_listener_count; i++) {
    if (s_listeners[i] == listener) return;
  }
  if (s_listener_count >= MAX_LISTENERS) return;
  s_listeners[s_listener_count++] = listener;
}

void app_state_notify(void) {
  for (int i = 0; i < s_listener_count; i++) {
    if (s_listeners[i]) s_listeners[i]();
  }
}
