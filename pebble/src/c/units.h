#pragma once

#include <stddef.h>
#include <stdint.h>

// Single-user deployment default: UK, miles. Flip to 0 to render kilometres
// everywhere. Kia's API reports distances in km; conversion is cosmetic and
// happens only at render time so the data model and wire format stay in km.
#define PBK_USE_MILES 1

// Formats a km reading into the configured display units, e.g. "284 km" or
// "176 mi". Returns the count snprintf would have written.
int format_distance_km(uint32_t km, char *buf, size_t sz);
