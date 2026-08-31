#include "layout.h"

#ifdef PBL_ROUND
static int16_t abs16(int16_t v) { return v < 0 ? -v : v; }

// Integer sqrt by linear search: the argument is at most radius^2
// (8100 on chalk) so this is a few dozen iterations a handful of times
// per redraw, and it avoids pulling the float library in for sqrtf.
static int16_t isqrt(int32_t v) {
  int32_t r = 0;
  while ((r + 1) * (r + 1) <= v) r++;
  return (int16_t)r;
}
#endif

GRect layout_row(GRect bounds, int16_t y, int16_t h) {
#ifdef PBL_ROUND
  int16_t radius = bounds.size.w / 2;
  int16_t cx = bounds.origin.x + radius;
  int16_t cy = bounds.origin.y + bounds.size.h / 2;

  // Whichever end of the row sits further from the centre line decides
  // how wide the row can be without a corner falling off the circle.
  int16_t top_dy = abs16(y - cy);
  int16_t bottom_dy = abs16((int16_t)(y + h) - cy);
  int16_t dy = top_dy > bottom_dy ? top_dy : bottom_dy;
  if (dy > radius) dy = radius;

  int16_t half = isqrt((int32_t)radius * radius - (int32_t)dy * dy);
  if (half <= LAYOUT_PAD_H) return GRect(cx, y, 0, h);
  return GRect(cx - half + LAYOUT_PAD_H, y, 2 * (half - LAYOUT_PAD_H), h);
#else
  return GRect(bounds.origin.x + LAYOUT_PAD_H, y,
               bounds.size.w - 2 * LAYOUT_PAD_H, h);
#endif
}
