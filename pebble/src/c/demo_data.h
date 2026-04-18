#pragma once

#include <pebble.h>

typedef enum {
  PLUG_NONE = 0,
  PLUG_AC = 1,
  PLUG_DC = 2,
} PlugState;

typedef struct {
  const char *id;
  const char *name;
  const char *model;
  uint8_t soc_pct;
  uint16_t range_km;
  bool is_charging;
  uint16_t charge_kw_x10;
  uint16_t charge_eta_min;
  PlugState plug;
  bool doors_locked;
  int8_t cabin_temp_c;
  uint32_t odo_km;
  time_t updated_at;
} Vehicle;

void demo_data_load(void);
int demo_data_count(void);
const Vehicle *demo_data_get(int idx);
void demo_data_simulate_refresh(int idx);
