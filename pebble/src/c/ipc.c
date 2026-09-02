#include "ipc.h"

#include <string.h>

#include "app_state.h"

// Launch paints what the watch remembered, then what the proxy holds,
// then asks the car itself: the first status reply of this run is
// answered with a forced refresh. The flag lives here and not in the
// companion because the watchapp restarts on every launch while the
// companion's JS session can outlive it (it does in the emulator). An
// error reply leaves it armed, so a launch through a connectivity blip
// still gets its wake once a status does come through.
static bool s_launch_refresh_pending;

// Whether a wake this watch asked for is still outstanding. A wake
// takes about half a minute, the companion keeps polling meanwhile, and
// every reply used to clear the busy flag — so the spinner vanished
// seconds into a pull and the pull looked like it had done nothing.
static bool s_awaiting_forced;
static AppTimer *s_forced_timer;
// Just past the companion's own 60s HTTP timeout: a reply that never
// arrives at all must not leave the spinner turning forever.
#define FORCED_WAIT_MS 65000

static void forced_wait_expired(void *ctx) {
  s_forced_timer = NULL;
  s_awaiting_forced = false;
  app_state_set_busy(false);
}

static void clear_forced_wait(void) {
  if (s_forced_timer) {
    app_timer_cancel(s_forced_timer);
    s_forced_timer = NULL;
  }
  s_awaiting_forced = false;
}

// Charge limits are the only command that carries values. They travel
// as their own keys rather than being packed into the action string so
// the companion can hand them straight to the proxy's query string.
static bool s_send_limits;
static uint8_t s_limit_ac;
static uint8_t s_limit_dc;

static bool send_request(const char *kind, const char *id,
                         const char *action) {
  // Clear any previous error optimistically — if this send (or the reply)
  // fails, the error will be re-set. Without this, a stale error hides
  // the busy indicator on retry and the user can't tell the retry fired.
  app_state_clear_error();

  DictionaryIterator *out;
  AppMessageResult r = app_message_outbox_begin(&out);
  if (r != APP_MSG_OK) {
    APP_LOG(APP_LOG_LEVEL_WARNING, "outbox_begin failed: %d", (int)r);
    app_state_set_error("Phone link busy");
    return false;
  }
  dict_write_cstring(out, MESSAGE_KEY_REQ_KIND, kind);
  if (id != NULL) dict_write_cstring(out, MESSAGE_KEY_REQ_ID, id);
  if (action != NULL) dict_write_cstring(out, MESSAGE_KEY_ACTION, action);
  if (s_send_limits) {
    dict_write_uint8(out, MESSAGE_KEY_ACTION_AC, s_limit_ac);
    dict_write_uint8(out, MESSAGE_KEY_ACTION_DC, s_limit_dc);
    s_send_limits = false;
  }
  r = app_message_outbox_send();
  if (r != APP_MSG_OK) {
    APP_LOG(APP_LOG_LEVEL_WARNING, "outbox_send failed: %d", (int)r);
    app_state_set_error("Phone link busy");
    return false;
  }
  app_state_set_busy(true);
  return true;
}

void ipc_request_list(void) {
  send_request("list", NULL, NULL);
}

void ipc_request_status(const char *id, bool force) {
  if (id == NULL || id[0] == 0) return;
  if (force) {
    // A pull discharges the launch obligation: the wake it starts is
    // the one the launch wanted, and leaving both armed would answer
    // this wake's own reply with a second wake.
    s_launch_refresh_pending = false;
  }
  if (!send_request(force ? "refresh" : "status", id, NULL)) return;
  if (force) {
    s_awaiting_forced = true;
    if (s_forced_timer) {
      app_timer_reschedule(s_forced_timer, FORCED_WAIT_MS);
    } else {
      s_forced_timer =
          app_timer_register(FORCED_WAIT_MS, forced_wait_expired, NULL);
    }
  }
}

void ipc_request_action(const char *id, const char *action) {
  if (id == NULL || id[0] == 0) return;
  app_state_set_action_ok(false);
  app_state_set_action_pending(true);
  if (!send_request("action", id, action)) app_state_set_action_pending(false);
}

void ipc_request_charge_limit(const char *id, uint8_t ac, uint8_t dc) {
  if (id == NULL || id[0] == 0) return;
  s_send_limits = true;
  s_limit_ac = ac;
  s_limit_dc = dc;
  ipc_request_action(id, "set_charge_limit");
  // A send that never got as far as the outbox leaves the flag armed,
  // which would attach these limits to whatever request went next.
  s_send_limits = false;
}

// The outbox carries one message at a time, so a vehicle picked while a
// request is in flight can't be asked for yet. Dropping the ask would
// strand it: the companion only ever polls the vehicle it last heard
// about, so nothing would come back for this one until the user
// happened to press again. Remember it and send when the link frees up.
static bool s_status_deferred = false;

void ipc_request_current_status(void) {
  const Vehicle *v = app_state_current_vehicle();
  if (!v) return;
  if (app_state_is_busy()) {
    s_status_deferred = true;
    return;
  }
  s_status_deferred = false;
  ipc_request_status(v->id, false);
}

static PlugState plug_from_wire(int v) {
  if (v == 1) return PLUG_AC;
  if (v == 2) return PLUG_DC;
  return PLUG_NONE;
}

static void handle_list(DictionaryIterator *in) {
  Tuple *count_t = dict_find(in, MESSAGE_KEY_VEHICLE_COUNT);
  int count = count_t ? (int)count_t->value->int32 : 0;
  if (count < 0) count = 0;
  if (count > MAX_VEHICLES) count = MAX_VEHICLES;

  char ids[MAX_VEHICLES][VEHICLE_ID_LEN] = {{0}};
  char nicks[MAX_VEHICLES][VEHICLE_NICK_LEN] = {{0}};
  for (int i = 0; i < count; i++) {
    Tuple *id_t = dict_find(in, MESSAGE_KEY_VEHICLE_ID + i);
    Tuple *nick_t = dict_find(in, MESSAGE_KEY_VEHICLE_NICK + i);
    if (id_t) strncpy(ids[i], id_t->value->cstring, VEHICLE_ID_LEN - 1);
    if (nick_t) strncpy(nicks[i], nick_t->value->cstring, VEHICLE_NICK_LEN - 1);
  }
  app_state_clear_error();
  app_state_apply_vehicle_list(ids, nicks, count);

  // Ask even when the vehicle already carries status: at launch that
  // status came out of flash and is stale, the companion's poll loop
  // only starts once it has been told which vehicle is current, and
  // the reply is what triggers the launch wake (see handle_status).
  ipc_request_current_status();
}

static void handle_status(DictionaryIterator *in, bool from_car) {
  Tuple *id_t = dict_find(in, MESSAGE_KEY_STATUS_ID);
  if (!id_t) return;
  VehicleStatus s = {0};
  // A missing key must read as "not reported", and for the battery
  // temperature 0 is a real reading, so the sentinel has to be seeded
  // before the unpack rather than relied on from the zero-init.
  s.batt_temp_c = BATT_TEMP_NONE;
  Tuple *t;
  if ((t = dict_find(in, MESSAGE_KEY_SOC_PCT)))         s.soc_pct = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_RANGE_KM)))        s.range_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_IS_CHARGING)))     s.is_charging = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_KW_X10)))   s.charge_kw_x10 = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_ETA_MIN)))  s.charge_eta_min = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_PLUG)))            s.plug = plug_from_wire(t->value->int32);
  if ((t = dict_find(in, MESSAGE_KEY_DOORS_LOCKED)))    s.doors_locked = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_OUTSIDE_TEMP_C)))  s.outside_temp_c = t->value->int8;
  if ((t = dict_find(in, MESSAGE_KEY_ODO_KM)))          s.odo_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_IS_CLIMATE_ON)))   s.is_climate_on = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_AUX_BATTERY_PCT))) s.aux_battery_pct = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_LIM_AC)))   s.charge_limit_ac = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_LIM_DC)))   s.charge_limit_dc = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_DOORS_OPEN)))      s.doors_open = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_WINDOWS_OPEN)))    s.windows_open = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_TRUNK_OPEN)))      s.trunk_open = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_HOOD_OPEN)))       s.hood_open = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_SUNROOF_OPEN)))    s.sunroof_open = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_EFF_KMPKWH_X10)))  s.eff_kmpkwh_x10 = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_BATT_TEMP_C)))     s.batt_temp_c = t->value->int8;
  if ((t = dict_find(in, MESSAGE_KEY_DEFROST_ON)))      s.defrost_on = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_REAR_DEFROST_ON))) s.rear_defrost_on = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_WHEEL_HEAT_ON)))   s.wheel_heat_on = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_BATT_COND)))       s.batt_conditioning = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_V2L_LIMIT_PCT)))   s.v2l_limit_pct = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_V2L_KW_X10)))      s.v2l_kw_x10 = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_TGT_RANGE_AC_KM))) s.target_range_ac_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_TGT_RANGE_DC_KM))) s.target_range_dc_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_UPDATED_AT)))      s.updated_at = (time_t)t->value->uint32;
  app_state_clear_error();
  app_state_apply_status(id_t->value->cstring, &s);

  if (!s_launch_refresh_pending) return;
  const Vehicle *v = app_state_current_vehicle();
  if (!v || strcmp(v->id, id_t->value->cstring) != 0) return;
  s_launch_refresh_pending = false;
  // Unless this reading already came from the car. The proxy wakes a
  // charging vehicle on an ordinary read of its own accord, and that is
  // the launch most likely to matter, so a second wake would cost
  // another half minute to learn nothing.
  if (!from_car) ipc_request_status(v->id, true);
}

static void inbox_received(DictionaryIterator *in, void *ctx) {
  Tuple *err_t = dict_find(in, MESSAGE_KEY_ERROR_MSG);
  Tuple *kind_t = dict_find(in, MESSAGE_KEY_RESP_KIND);
  const char *kind = kind_t ? kind_t->value->cstring : "";
  Tuple *forced_t = dict_find(in, MESSAGE_KEY_FORCED);
  bool from_car = forced_t && forced_t->value->uint8 != 0;

  // The companion polls every 15s, so an ordinary status reply can land
  // in the middle of a wake this watch is still waiting on. Leave the
  // busy flag alone for those: taking the spinner down mid-wake is what
  // makes a pull look like it did nothing.
  bool interim_poll = !err_t && s_awaiting_forced && !from_car &&
                      strcmp(kind, "status") == 0;
  if (!interim_poll) {
    clear_forced_wait();
    app_state_set_busy(false);
  }

  // Unit preference piggybacks on list + status messages; apply first
  // so a config flip is visible immediately, even alongside an error
  // reply that does nothing else useful.
  Tuple *unit_t = dict_find(in, MESSAGE_KEY_UNIT_MILES);
  if (unit_t) app_state_set_unit_miles(unit_t->value->uint8 != 0);

  if (err_t) {
    app_state_set_action_pending(false);
    app_state_set_error(err_t->value->cstring);
  } else if (strcmp(kind, "list") == 0) {
    handle_list(in);
  } else if (strcmp(kind, "status") == 0) {
    handle_status(in, from_car);
  } else if (strcmp(kind, "action_ok") == 0) {
    app_state_clear_error();
    app_state_set_action_pending(false);
    app_state_set_action_ok(true);
  } else if (strcmp(kind, "ready") == 0) {
    // Companion (re)connected. The first ready triggers the deferred
    // initial fetch — restored vehicles still need it, flash only saved
    // us the blank screen. A ready arriving after the list is in hand
    // means the companion restarted and forgot which vehicle its poll
    // loop should watch, so re-seed it with an ordinary status request;
    // otherwise polling stays dead until the user forces a refresh.
    if (app_state_phase() != APP_PHASE_READY) ipc_request_list();
    else ipc_request_current_status();
  } else if (strcmp(kind, "error") == 0) {
    app_state_set_error("phone error");
  }

  // The link is free again unless the handling above claimed it, so a
  // vehicle selected mid-flight gets its turn here.
  if (s_status_deferred && !app_state_is_busy()) ipc_request_current_status();
}

static void inbox_dropped(AppMessageResult reason, void *ctx) {
  APP_LOG(APP_LOG_LEVEL_WARNING, "inbox dropped: %d", (int)reason);
  clear_forced_wait();
  app_state_set_busy(false);
  app_state_set_action_pending(false);
  app_state_set_error("Reply dropped");
}

static void outbox_failed(DictionaryIterator *it, AppMessageResult reason,
                          void *ctx) {
  APP_LOG(APP_LOG_LEVEL_WARNING, "outbox failed: %d", (int)reason);
  clear_forced_wait();
  app_state_set_busy(false);
  app_state_set_action_pending(false);
  app_state_set_error("Phone unreachable");
}

static void outbox_sent(DictionaryIterator *it, void *ctx) {
  // Leave busy=true; inbox_received will clear it.
}

void ipc_init(void) {
  s_launch_refresh_pending = true;
  s_awaiting_forced = false;
  app_message_register_inbox_received(inbox_received);
  app_message_register_inbox_dropped(inbox_dropped);
  app_message_register_outbox_failed(outbox_failed);
  app_message_register_outbox_sent(outbox_sent);
  app_message_open(512, 256);
}

void ipc_deinit(void) {
  clear_forced_wait();
  app_message_deregister_callbacks();
}
