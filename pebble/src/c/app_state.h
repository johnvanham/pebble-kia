#pragma once

#include <pebble.h>

#define MAX_VEHICLES 4
#define VEHICLE_ID_LEN 24
#define VEHICLE_NICK_LEN 16
#define APP_ERROR_LEN 96

typedef enum {
  PLUG_NONE = 0,
  PLUG_AC = 1,
  PLUG_DC = 2,
} PlugState;

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
  int8_t cabin_temp_c;
  uint32_t odo_km;
  bool is_climate_on;
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
  int8_t cabin_temp_c;
  uint32_t odo_km;
  bool is_climate_on;
  time_t updated_at;
} VehicleStatus;

typedef enum {
  APP_PHASE_LOADING_LIST,
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

typedef void (*AppStateListener)(void);
void app_state_subscribe(AppStateListener listener);
void app_state_notify(void);
