#include "demo_data.h"

#define VEHICLE_COUNT 2

static Vehicle s_vehicles[VEHICLE_COUNT];

void demo_data_load(void) {
  time_t now = time(NULL);

  s_vehicles[0] = (Vehicle){
      .id = "demo-pv5",
      .name = "PV5 Passenger",
      .model = "PV5",
      .soc_pct = 72,
      .range_km = 284,
      .is_charging = true,
      .charge_kw_x10 = 73,
      .charge_eta_min = 95,
      .plug = PLUG_AC,
      .doors_locked = true,
      .cabin_temp_c = 18,
      .odo_km = 3421,
      .updated_at = now - 120,
  };

  s_vehicles[1] = (Vehicle){
      .id = "demo-ev9",
      .name = "EV9 GT-Line",
      .model = "EV9",
      .soc_pct = 41,
      .range_km = 212,
      .is_charging = false,
      .charge_kw_x10 = 0,
      .charge_eta_min = 0,
      .plug = PLUG_NONE,
      .doors_locked = false,
      .cabin_temp_c = 22,
      .odo_km = 18734,
      .updated_at = now - 600,
  };
}

int demo_data_count(void) { return VEHICLE_COUNT; }

const Vehicle *demo_data_get(int idx) {
  if (idx < 0 || idx >= VEHICLE_COUNT) return NULL;
  return &s_vehicles[idx];
}

void demo_data_simulate_refresh(int idx) {
  if (idx < 0 || idx >= VEHICLE_COUNT) return;
  Vehicle *v = &s_vehicles[idx];
  v->updated_at = time(NULL);

  if (v->is_charging) {
    if (v->soc_pct < 100) v->soc_pct++;
    v->range_km = (uint16_t)((uint32_t)v->range_km + 4);
    if (v->charge_eta_min > 3) v->charge_eta_min -= 3;
  } else {
    if (v->soc_pct > 0 && (v->updated_at % 2) == 0) v->soc_pct--;
    if (v->range_km > 2) v->range_km -= 2;
  }
}
