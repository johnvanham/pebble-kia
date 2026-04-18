#include "units.h"

#include <stdio.h>

int format_distance_km(uint32_t km, char *buf, size_t sz) {
#if PBK_USE_MILES
  // Integer approximation of km / 1.609344, rounded. Matches the exact
  // mile count to within 1 mi for any realistic odometer reading.
  unsigned long mi = ((unsigned long)km * 1000UL + 804UL) / 1609UL;
  return snprintf(buf, sz, "%lu mi", mi);
#else
  return snprintf(buf, sz, "%lu km", (unsigned long)km);
#endif
}
