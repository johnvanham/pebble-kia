#pragma once

#include <stddef.h>
#include <stdint.h>
#include <time.h>

// Compile-time default for the miles/km choice, used only until the
// companion's Clay toggle has been seen for the first time. After that
// the runtime value in app_state wins, and it is persisted alongside
// the vehicle data so an offline launch restores the user's choice
// rather than falling back here. UK deployment, so default to miles.
#define PBK_USE_MILES_DEFAULT 1

// Formats a km reading into the configured display units, e.g.
// "284 km" or "176 mi". Returns the count snprintf would have
// written. Units follow app_state_unit_miles() at call time.
int format_distance_km(uint32_t km, char *buf, size_t sz);

// Formats how long ago a reading was taken, e.g. "42s ago" or "3d
// ago", or "--" for a timestamp we never got. Both screens show this,
// so it lives here rather than in either one.
void format_age(time_t when, char *buf, size_t sz);
