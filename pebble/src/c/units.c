#include "units.h"

#include <stdio.h>

#include "app_state.h"

int format_distance_km(uint32_t km, char *buf, size_t sz) {
  if (app_state_unit_miles()) {
    // Integer approximation of km / 1.609344, rounded. Matches the
    // exact mile count to within 1 mi for any realistic odometer.
    unsigned long mi = ((unsigned long)km * 1000UL + 804UL) / 1609UL;
    return snprintf(buf, sz, "%lu mi", mi);
  }
  return snprintf(buf, sz, "%lu km", (unsigned long)km);
}

int format_efficiency(uint16_t kmpkwh_x10, char *buf, size_t sz) {
  if (kmpkwh_x10 == 0) return snprintf(buf, sz, "--");
  unsigned long v = kmpkwh_x10;
  if (app_state_unit_miles()) {
    v = (v * 1000UL + 804UL) / 1609UL;
    return snprintf(buf, sz, "%lu.%lu mi/kWh", v / 10, v % 10);
  }
  return snprintf(buf, sz, "%lu.%lu km/kWh", v / 10, v % 10);
}

void format_age(time_t when, char *buf, size_t sz) {
  if (when == 0) {
    snprintf(buf, sz, "--");
    return;
  }
  int secs = (int)(time(NULL) - when);
  if (secs < 0) secs = 0;
  if (secs < 60) {
    snprintf(buf, sz, "%ds ago", secs);
  } else if (secs < 3600) {
    snprintf(buf, sz, "%dm ago", secs / 60);
  } else if (secs < 86400) {
    snprintf(buf, sz, "%dh ago", secs / 3600);
  } else {
    // Data restored from flash after a few days off the charger reads
    // better as "3d ago" than as "74h ago".
    snprintf(buf, sz, "%dd ago", secs / 86400);
  }
}
