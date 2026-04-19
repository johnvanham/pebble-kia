#pragma once

#include <stddef.h>
#include <stdint.h>

// Compile-time default for the miles/km choice. The runtime value is
// held in app_state (set from the companion's Clay toggle) and takes
// precedence once the watch has seen a message; this macro only
// affects the first render before any status arrives. UK deployment
// so default to miles.
#define PBK_USE_MILES_DEFAULT 1

// Formats a km reading into the configured display units, e.g.
// "284 km" or "176 mi". Returns the count snprintf would have
// written. Units follow app_state_unit_miles() at call time.
int format_distance_km(uint32_t km, char *buf, size_t sz);
