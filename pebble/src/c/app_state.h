#pragma once

#include <pebble.h>

#define MAX_VEHICLES 4
// Kia vehicle ids are 36-char UUIDs. The demo source used short ids
// ("pv5-demo"), so a smaller buffer silently truncated the id and the
// watch then asked the proxy for a vehicle that doesn't exist.
#define VEHICLE_ID_LEN 40
#define VEHICLE_NICK_LEN 16
#define APP_ERROR_LEN 96

typedef enum {
  PLUG_NONE = 0,
  PLUG_AC = 1,
  PLUG_DC = 2,
} PlugState;

// The companion sends -128 for a battery temperature the proxy did not
// report; 0 is a reading a real battery can give, so it cannot be the
// sentinel the way it is for the other "not reported" fields.
#define BATT_TEMP_NONE (-128)

typedef struct {
  char id[VEHICLE_ID_LEN];
  char nickname[VEHICLE_NICK_LEN];
  bool have_status;
  uint8_t soc_pct;
  uint16_t range_km;
  bool is_charging;
  uint16_t charge_kw_x10;
  uint16_t charge_eta_min;
  PlugState plug;
  bool doors_locked;
  int8_t outside_temp_c;
  uint32_t odo_km;
  bool is_climate_on;
  uint8_t aux_battery_pct;
  uint8_t charge_limit_ac;
  uint8_t charge_limit_dc;
  uint8_t doors_open;
  uint8_t windows_open;
  bool trunk_open;
  bool hood_open;
  bool sunroof_open;
  uint16_t eff_kmpkwh_x10;
  int8_t batt_temp_c;
  time_t updated_at;
} Vehicle;

typedef struct {
  uint8_t soc_pct;
  uint16_t range_km;
  bool is_charging;
  uint16_t charge_kw_x10;
  uint16_t charge_eta_min;
  PlugState plug;
  bool doors_locked;
  int8_t outside_temp_c;
  uint32_t odo_km;
  bool is_climate_on;
  uint8_t aux_battery_pct;
  uint8_t charge_limit_ac;
  uint8_t charge_limit_dc;
  uint8_t doors_open;
  uint8_t windows_open;
  bool trunk_open;
  bool hood_open;
  bool sunroof_open;
  uint16_t eff_kmpkwh_x10;
  int8_t batt_temp_c;
  time_t updated_at;
} VehicleStatus;

typedef enum {
  APP_PHASE_LOADING_LIST,
  // Vehicles came back from flash, so the screen paints immediately, but
  // this session has not heard from the companion yet.
  APP_PHASE_RESTORED,
  APP_PHASE_READY,
} AppPhase;

void app_state_init(void);
void app_state_deinit(void);

AppPhase app_state_phase(void);
int app_state_vehicle_count(void);
int app_state_current_index(void);
const Vehicle *app_state_current_vehicle(void);
const Vehicle *app_state_vehicle_at(int idx);
const char *app_state_error(void);
bool app_state_is_busy(void);
bool app_state_unit_miles(void);
void app_state_set_unit_miles(bool v);

void app_state_next_vehicle(void);
void app_state_prev_vehicle(void);

void app_state_apply_vehicle_list(const char (*ids)[VEHICLE_ID_LEN],
                                  const char (*nicks)[VEHICLE_NICK_LEN],
                                  int count);
void app_state_apply_status(const char *id, const VehicleStatus *status);
void app_state_set_error(const char *msg);
void app_state_clear_error(void);
void app_state_set_busy(bool busy);

// Whether the most recent vehicle action was acknowledged. Transient:
// firing another action clears it, and the actions menu clears it when
// it closes, so "Sent" never outlives the screen it was earned on.
bool app_state_action_ok(void);
void app_state_set_action_ok(bool ok);

typedef void (*AppStateListener)(void);
void app_state_subscribe(AppStateListener listener);
void app_state_notify(void);
