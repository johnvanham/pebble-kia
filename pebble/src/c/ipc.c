#include "ipc.h"

#include <string.h>

#include "app_state.h"

static void send_request(const char *kind, const char *id,
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
    return;
  }
  dict_write_cstring(out, MESSAGE_KEY_REQ_KIND, kind);
  if (id != NULL) dict_write_cstring(out, MESSAGE_KEY_REQ_ID, id);
  if (action != NULL) dict_write_cstring(out, MESSAGE_KEY_ACTION, action);
  r = app_message_outbox_send();
  if (r != APP_MSG_OK) {
    APP_LOG(APP_LOG_LEVEL_WARNING, "outbox_send failed: %d", (int)r);
    app_state_set_error("Phone link busy");
    return;
  }
  app_state_set_busy(true);
}

void ipc_request_list(void) {
  send_request("list", NULL, NULL);
}

void ipc_request_status(const char *id, bool force) {
  if (id == NULL || id[0] == 0) return;
  send_request(force ? "refresh" : "status", id, NULL);
}

void ipc_request_action(const char *id, const char *action) {
  if (id == NULL || id[0] == 0) return;
  app_state_set_action_ok(false);
  send_request("action", id, action);
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
  // status came out of flash and is stale, and the companion's poll
  // loop only starts once it has been told which vehicle is current.
  ipc_request_current_status();
}

static void handle_status(DictionaryIterator *in) {
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
  if ((t = dict_find(in, MESSAGE_KEY_UPDATED_AT)))      s.updated_at = (time_t)t->value->uint32;
  app_state_clear_error();
  app_state_apply_status(id_t->value->cstring, &s);
}

static void inbox_received(DictionaryIterator *in, void *ctx) {
  app_state_set_busy(false);

  // Unit preference piggybacks on list + status messages; apply first
  // so a config flip is visible immediately, even alongside an error
  // reply that does nothing else useful.
  Tuple *unit_t = dict_find(in, MESSAGE_KEY_UNIT_MILES);
  if (unit_t) app_state_set_unit_miles(unit_t->value->uint8 != 0);

  Tuple *err_t = dict_find(in, MESSAGE_KEY_ERROR_MSG);
  Tuple *kind_t = dict_find(in, MESSAGE_KEY_RESP_KIND);
  const char *kind = kind_t ? kind_t->value->cstring : "";
  if (err_t) {
    app_state_set_error(err_t->value->cstring);
  } else if (strcmp(kind, "list") == 0) {
    handle_list(in);
  } else if (strcmp(kind, "status") == 0) {
    handle_status(in);
  } else if (strcmp(kind, "action_ok") == 0) {
    app_state_clear_error();
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
  app_state_set_busy(false);
  app_state_set_error("Reply dropped");
}

static void outbox_failed(DictionaryIterator *it, AppMessageResult reason,
                          void *ctx) {
  APP_LOG(APP_LOG_LEVEL_WARNING, "outbox failed: %d", (int)reason);
  app_state_set_busy(false);
  app_state_set_error("Phone unreachable");
}

static void outbox_sent(DictionaryIterator *it, void *ctx) {
  // Leave busy=true; inbox_received will clear it.
}

void ipc_init(void) {
  app_message_register_inbox_received(inbox_received);
  app_message_register_inbox_dropped(inbox_dropped);
  app_message_register_outbox_failed(outbox_failed);
  app_message_register_outbox_sent(outbox_sent);
  app_message_open(512, 256);
}

void ipc_deinit(void) {
  app_message_deregister_callbacks();
}
