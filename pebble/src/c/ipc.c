#include "ipc.h"

#include <string.h>

#include "app_state.h"

static void send_request(const char *kind, const char *id) {
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
  r = app_message_outbox_send();
  if (r != APP_MSG_OK) {
    APP_LOG(APP_LOG_LEVEL_WARNING, "outbox_send failed: %d", (int)r);
    app_state_set_error("Phone link busy");
    return;
  }
  app_state_set_busy(true);
}

void ipc_request_list(void) {
  send_request("list", NULL);
}

void ipc_request_status(const char *id, bool force) {
  if (id == NULL || id[0] == 0) return;
  send_request(force ? "refresh" : "status", id);
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

  const Vehicle *cur = app_state_current_vehicle();
  if (cur && !cur->have_status) ipc_request_status(cur->id, false);
}

static void handle_status(DictionaryIterator *in) {
  Tuple *id_t = dict_find(in, MESSAGE_KEY_STATUS_ID);
  if (!id_t) return;
  VehicleStatus s = {0};
  Tuple *t;
  if ((t = dict_find(in, MESSAGE_KEY_SOC_PCT)))        s.soc_pct = t->value->uint8;
  if ((t = dict_find(in, MESSAGE_KEY_RANGE_KM)))       s.range_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_IS_CHARGING)))    s.is_charging = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_KW_X10)))  s.charge_kw_x10 = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_CHARGE_ETA_MIN))) s.charge_eta_min = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_PLUG)))           s.plug = plug_from_wire(t->value->int32);
  if ((t = dict_find(in, MESSAGE_KEY_DOORS_LOCKED)))   s.doors_locked = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_CABIN_TEMP_C)))   s.cabin_temp_c = t->value->int8;
  if ((t = dict_find(in, MESSAGE_KEY_ODO_KM)))         s.odo_km = t->value->uint32;
  if ((t = dict_find(in, MESSAGE_KEY_IS_CLIMATE_ON)))  s.is_climate_on = t->value->uint8 != 0;
  if ((t = dict_find(in, MESSAGE_KEY_UPDATED_AT)))     s.updated_at = (time_t)t->value->uint32;
  app_state_clear_error();
  app_state_apply_status(id_t->value->cstring, &s);
}

static void inbox_received(DictionaryIterator *in, void *ctx) {
  app_state_set_busy(false);

  Tuple *err_t = dict_find(in, MESSAGE_KEY_ERROR_MSG);
  if (err_t) {
    app_state_set_error(err_t->value->cstring);
    return;
  }
  Tuple *kind_t = dict_find(in, MESSAGE_KEY_RESP_KIND);
  if (!kind_t) return;
  const char *kind = kind_t->value->cstring;
  if (strcmp(kind, "list") == 0) handle_list(in);
  else if (strcmp(kind, "status") == 0) handle_status(in);
  else if (strcmp(kind, "ready") == 0) {
    // Companion connected; trigger the deferred initial fetch.
    if (app_state_phase() == APP_PHASE_LOADING_LIST) ipc_request_list();
  } else if (strcmp(kind, "error") == 0) {
    if (err_t) app_state_set_error(err_t->value->cstring);
    else app_state_set_error("phone error");
  }
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
