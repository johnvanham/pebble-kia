#include "spinner.h"

#include "app_state.h"

// Tick every 100 ms while busy — fast enough to look smooth, slow
// enough not to wake the CPU hard. Each step rotates the arc by 1/12
// of a full turn, i.e. ~100 deg/s.
#define SPINNER_TICK_MS 100
#define SPINNER_STEP (TRIG_MAX_ANGLE / 12)

static AppTimer *s_timer;
static int32_t s_angle;
static Layer *s_layer;

static bool should_spin(void) {
  return app_state_is_busy() && !app_state_error();
}

static void tick(void *ctx) {
  s_timer = NULL;
  s_angle = (s_angle + SPINNER_STEP) % TRIG_MAX_ANGLE;
  if (s_layer) layer_mark_dirty(s_layer);
  if (should_spin()) s_timer = app_timer_register(SPINNER_TICK_MS, tick, NULL);
}

void spinner_sync(Layer *canvas) {
  s_layer = canvas;
  if (should_spin()) {
    if (!s_timer) s_timer = app_timer_register(SPINNER_TICK_MS, tick, NULL);
  } else if (s_timer) {
    app_timer_cancel(s_timer);
    s_timer = NULL;
  }
}

void spinner_detach(Layer *canvas) {
  if (s_layer == canvas) s_layer = NULL;
}

void spinner_draw(GContext *ctx, GRect box) {
#ifdef PBL_COLOR
  graphics_context_set_stroke_color(ctx, GColorChromeYellow);
#else
  graphics_context_set_stroke_color(ctx, GColorWhite);
#endif
  graphics_context_set_stroke_width(ctx, 2);
  graphics_draw_arc(ctx, box, GOvalScaleModeFitCircle, s_angle,
                    s_angle + (TRIG_MAX_ANGLE * 3 / 4));
  graphics_context_set_stroke_width(ctx, 1);
}
