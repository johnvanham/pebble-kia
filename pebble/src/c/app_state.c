#include "app_state.h"

#include <string.h>

#define MAX_LISTENERS 4

static Vehicle s_vehicles[MAX_VEHICLES];
static int s_vehicle_count = 0;
static int s_current_index = 0;
static AppPhase s_phase = APP_PHASE_LOADING_LIST;
static bool s_busy = false;
static char s_error[APP_ERROR_LEN] = {0};

static AppStateListener s_listeners[MAX_LISTENERS];
static int s_listener_count = 0;

void app_state_init(void) {
  s_vehicle_count = 0;
  s_current_index = 0;
  s_phase = APP_PHASE_LOADING_LIST;
  s_busy = false;
  s_error[0] = 0;
  s_listener_count = 0;
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
        s_vehicles[i].cabin_temp_c = prev[j].cabin_temp_c;
        s_vehicles[i].odo_km = prev[j].odo_km;
        s_vehicles[i].updated_at = prev[j].updated_at;
        break;
      }
    }
  }
  s_vehicle_count = count;
  if (s_current_index >= s_vehicle_count) s_current_index = 0;
  s_phase = APP_PHASE_READY;
  app_state_notify();
}

void app_state_apply_status(const char *id, const VehicleStatus *status) {
  for (int i = 0; i < s_vehicle_count; i++) {
    if (strcmp(s_vehicles[i].id, id) == 0) {
      Vehicle *v = &s_vehicles[i];
      v->have_status = true;
      v->soc_pct = status->soc_pct;
      v->range_km = status->range_km;
      v->is_charging = status->is_charging;
      v->charge_kw_x10 = status->charge_kw_x10;
      v->charge_eta_min = status->charge_eta_min;
      v->plug = status->plug;
      v->doors_locked = status->doors_locked;
      v->cabin_temp_c = status->cabin_temp_c;
      v->odo_km = status->odo_km;
      v->updated_at = status->updated_at;
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
