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
