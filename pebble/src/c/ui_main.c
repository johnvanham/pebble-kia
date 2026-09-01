#include "ui_main.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"
#include "spinner.h"
#include "ui_detail.h"
#include "units.h"

static Window *s_window;
static Layer *s_canvas;

static const char *plug_label(PlugState p) {
  switch (p) {
    case PLUG_AC: return "AC";
    case PLUG_DC: return "DC";
    default: return "--";
  }
}

#ifdef PBL_COLOR
static GColor battery_fill_colour(uint8_t soc_pct, bool charging) {
  if (charging)     return GColorIslamicGreen;
  if (soc_pct <= 10) return GColorFolly;
  if (soc_pct <= 20) return GColorOrange;
  if (soc_pct <= 50) return GColorChromeYellow;
  return GColorVividCerulean;
}
#endif

static void draw_battery(GContext *ctx, GRect r, uint8_t soc_pct,
                         bool charging) {
#ifdef PBL_COLOR
  graphics_context_set_fill_color(ctx, battery_fill_colour(soc_pct, charging));
#else
  graphics_context_set_fill_color(ctx, GColorWhite);
#endif
  graphics_context_set_stroke_color(ctx, GColorWhite);

  int16_t nub_w = r.size.h / 3;
  GRect body = GRect(r.origin.x, r.origin.y, r.size.w - nub_w, r.size.h);
  graphics_draw_rect(ctx, body);

  GRect nub = GRect(body.origin.x + body.size.w, r.origin.y + r.size.h / 4,
                    nub_w, r.size.h / 2);
  graphics_fill_rect(ctx, nub, 0, GCornerNone);

  int16_t inner_w = body.size.w - 4;
  int16_t fill_w = (inner_w * soc_pct) / 100;
  GRect fill_r = GRect(body.origin.x + 2, body.origin.y + 2, fill_w,
                       body.size.h - 4);
  graphics_fill_rect(ctx, fill_r, 0, GCornerNone);
}

// Charging state as a glyph beside the SoC number: a filled bolt while
// charging, the same bolt as an outline while idle. Filled-vs-hollow
// carries the distinction on the 1-bit platforms, where the green the
// battery bar uses isn't available. The path is sized off LAYOUT_H_SOC
// at compile time; 16 is the path's own unit height.
#define BOLT_H ((LAYOUT_H_SOC * 5) / 12)
#define BOLT_UNIT(u) ((u) * BOLT_H / 16)
// The waist (the band where the two crossbars overlap, unit y 6..10)
// is deliberately thick: at ~20px tall a slimmer bolt loses its middle
// to integer scaling and the filled glyph falls apart into two blobs.
static const GPathInfo BOLT_PATH_INFO = {
    .num_points = 6,
    .points = (GPoint[]){{BOLT_UNIT(7), 0},
                         {0, BOLT_UNIT(10)},
                         {BOLT_UNIT(4), BOLT_UNIT(10)},
                         {BOLT_UNIT(3), BOLT_H},
                         {BOLT_UNIT(10), BOLT_UNIT(6)},
                         {BOLT_UNIT(6), BOLT_UNIT(6)}},
};
static GPath *s_bolt;

static void draw_charge_bolt(GContext *ctx, GRect soc_row, int16_t digits_x,
                             bool charging) {
  if (!s_bolt) return;
  int16_t x = digits_x - BOLT_UNIT(10) - LAYOUT_GAP;
  // "100" on chalk's narrow chord can leave no room; drop the glyph
  // rather than overlap the digits.
  if (x < soc_row.origin.x) return;
  // The digits sit below the centre of their line box (top bearing),
  // so the glyph is nudged down to centre on the ink rather than the box.
  int16_t bolt_y = soc_row.origin.y + (LAYOUT_H_SOC - BOLT_H) / 2 +
                   BOLT_H / 8;
  gpath_move_to(s_bolt, GPoint(x, bolt_y));
  if (charging) {
#ifdef PBL_COLOR
    graphics_context_set_fill_color(ctx, GColorIslamicGreen);
    graphics_context_set_stroke_color(ctx, GColorIslamicGreen);
#else
    graphics_context_set_fill_color(ctx, GColorWhite);
    graphics_context_set_stroke_color(ctx, GColorWhite);
#endif
    // Fill plus outline: the scanline fill on its own eats the bolt's
    // thin tips at the smaller platforms' glyph size.
    gpath_draw_filled(ctx, s_bolt);
    graphics_context_set_stroke_width(ctx, BOLT_H >= 22 ? 2 : 1);
    gpath_draw_outline(ctx, s_bolt);
    graphics_context_set_stroke_width(ctx, 1);
    graphics_context_set_stroke_color(ctx, GColorWhite);
  } else {
#ifdef PBL_COLOR
    graphics_context_set_stroke_color(ctx, GColorLightGray);
#else
    graphics_context_set_stroke_color(ctx, GColorWhite);
#endif
    // A 2px stroke fills the glyph in at the smaller platforms' size,
    // making idle look bolder than charging.
    graphics_context_set_stroke_width(ctx, BOLT_H >= 22 ? 2 : 1);
    gpath_draw_outline(ctx, s_bolt);
    graphics_context_set_stroke_width(ctx, 1);
    graphics_context_set_stroke_color(ctx, GColorWhite);
  }
}

static void draw_centered_message(GContext *ctx, GRect b, const char *title,
                                  const char *sub) {
  GFont tf = fonts_get_system_font(LAYOUT_FONT_VALUE);
  GFont sf = fonts_get_system_font(LAYOUT_FONT_BODY);
  int16_t y = b.origin.y + b.size.h / 2 - LAYOUT_H_VALUE;
  graphics_draw_text(ctx, title, tf, layout_row(b, y, LAYOUT_H_VALUE),
                     GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
  if (sub && sub[0]) {
    int16_t sub_y = y + LAYOUT_H_VALUE + LAYOUT_GAP;
    int16_t sub_h = b.origin.y + b.size.h - LAYOUT_PAD_V - sub_y;
    graphics_draw_text(ctx, sub, sf, layout_row(b, sub_y, sub_h),
                       GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
  }
}

static void draw_indicator(GContext *ctx, GRect b) {
  GRect row = layout_row(b, b.origin.y + LAYOUT_PAD_V, LAYOUT_H_IND);
  int16_t right = row.origin.x + row.size.w;
  const char *error = app_state_error();
  if (error) {
    GFont ind_font = fonts_get_system_font(LAYOUT_FONT_IND);
#ifdef PBL_COLOR
    graphics_context_set_text_color(ctx, GColorFolly);
#endif
    GRect ind_rect = GRect(right - LAYOUT_W_IND, row.origin.y, LAYOUT_W_IND,
                           LAYOUT_H_IND);
    graphics_draw_text(ctx, "ERR", ind_font, ind_rect,
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentRight,
                       NULL);
    graphics_context_set_text_color(ctx, GColorWhite);
  } else if (app_state_is_busy()) {
    spinner_draw(ctx, GRect(right - LAYOUT_D_SPINNER, row.origin.y,
                            LAYOUT_D_SPINNER, LAYOUT_D_SPINNER));
  }
}

static void canvas_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);

  const char *error = app_state_error();

  if (app_state_phase() == APP_PHASE_LOADING_LIST) {
    draw_indicator(ctx, b);
    draw_centered_message(ctx, b, "Connecting...",
                          error ? error : "Fetching vehicle list");
    return;
  }

  if (app_state_vehicle_count() == 0) {
    draw_indicator(ctx, b);
    draw_centered_message(ctx, b, "No vehicles",
                          "Open the Pebble app to configure the proxy.");
    return;
  }

  const Vehicle *v = app_state_current_vehicle();
  if (!v) return;

  // --- Name (top) ---
  int16_t top = b.origin.y + LAYOUT_PAD_V;
  GFont name_font = fonts_get_system_font(LAYOUT_FONT_TITLE);
  graphics_draw_text(ctx, v->nickname, name_font,
                     layout_row(b, top, LAYOUT_H_TITLE),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  draw_indicator(ctx, b);

  if (!v->have_status) {
    draw_centered_message(ctx, b, v->nickname, error ? error : "Loading...");
    return;
  }

  // An error gets its own line under the status row rather than taking
  // the row over: the numbers above are exactly as old as the failure
  // makes them, so the age is the one thing that must not disappear
  // when the fetch stops working. The readout block below is centred in
  // whatever is left, so it gives up the height rather than colliding.
  int16_t status_h = error ? 2 * LAYOUT_H_STATUS : LAYOUT_H_STATUS;
  int16_t status_y = b.origin.y + b.size.h - LAYOUT_PAD_V - status_h;
  int16_t block_top = top + LAYOUT_H_TITLE;
  int16_t block_h = LAYOUT_H_SOC + LAYOUT_GAP + LAYOUT_H_BAR + LAYOUT_GAP +
                    LAYOUT_H_VALUE;
  int16_t y = block_top + (status_y - block_top - block_h) / 2;
  if (y < block_top) y = block_top;

  // --- Big SoC number, centred as a group (digits then "%") so one-,
  // two- and three-digit readings all sit in the middle of the row.
  // The bolt hangs off the left of the group rather than joining it:
  // charging toggling on and off should not shove the number sideways.
  GFont soc_font = fonts_get_system_font(LAYOUT_FONT_SOC);
  char soc_buf[8];
  snprintf(soc_buf, sizeof(soc_buf), "%d", v->soc_pct);
  GRect soc_row = layout_row(b, y, LAYOUT_H_SOC);
  GSize num_size = graphics_text_layout_get_content_size(
      soc_buf, soc_font, soc_row, GTextOverflowModeWordWrap,
      GTextAlignmentLeft);
  int16_t digits_x =
      soc_row.origin.x + (soc_row.size.w - num_size.w - LAYOUT_W_PCT) / 2;
  if (digits_x < soc_row.origin.x) digits_x = soc_row.origin.x;
  graphics_draw_text(ctx, soc_buf, soc_font,
                     GRect(digits_x, y, num_size.w, LAYOUT_H_SOC),
                     GTextOverflowModeWordWrap, GTextAlignmentLeft, NULL);

  GFont pct_font = fonts_get_system_font(LAYOUT_FONT_PCT);
  GRect pct_rect = GRect(digits_x + num_size.w,
                         y + LAYOUT_H_SOC - LAYOUT_H_PCT - 2, LAYOUT_W_PCT,
                         LAYOUT_H_PCT);
  graphics_draw_text(ctx, "%", pct_font, pct_rect, GTextOverflowModeWordWrap,
                     GTextAlignmentLeft, NULL);

  draw_charge_bolt(ctx, soc_row, digits_x, v->is_charging);

  // --- Battery bar ---
  y += LAYOUT_H_SOC + LAYOUT_GAP;
  GRect bar_row = layout_row(b, y, LAYOUT_H_BAR);
  draw_battery(ctx, grect_inset(bar_row, GEdgeInsets(0, bar_row.size.w / 8)),
               v->soc_pct, v->is_charging);

  // --- Range ---
  y += LAYOUT_H_BAR + LAYOUT_GAP;
  GFont range_font = fonts_get_system_font(LAYOUT_FONT_VALUE);
  char range_buf[16];
  format_distance_km(v->range_km, range_buf, sizeof(range_buf));
  graphics_draw_text(ctx, range_buf, range_font,
                     layout_row(b, y, LAYOUT_H_VALUE),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  // --- Status row (bottom), plus the error under it when there is one.
  GFont status_font = fonts_get_system_font(LAYOUT_FONT_STATUS);
  char ago_buf[16];
  format_age(v->updated_at, ago_buf, sizeof(ago_buf));
  char status_buf[32];
  snprintf(status_buf, sizeof(status_buf), "%s  %s  %s", plug_label(v->plug),
           v->doors_locked ? "LOCK" : "OPEN", ago_buf);
  graphics_draw_text(ctx, status_buf, status_font,
                     layout_row(b, status_y, LAYOUT_H_STATUS),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
  if (error) {
#ifdef PBL_COLOR
    graphics_context_set_text_color(ctx, GColorFolly);
#endif
    graphics_draw_text(ctx, error, status_font,
                       layout_row(b, status_y + LAYOUT_H_STATUS,
                                  LAYOUT_H_STATUS),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                       NULL);
    graphics_context_set_text_color(ctx, GColorWhite);
  }
}

static void on_state_changed(void) {
  if (!s_canvas) return;
  layer_mark_dirty(s_canvas);
  spinner_sync(s_canvas);
}

// With one vehicle, switching is a no-op but the status request would
// still fire and flash the spinner — a phantom refresh. So the buttons
// only do anything when there is something to switch between.
static void up_click(ClickRecognizerRef ref, void *ctx) {
  if (app_state_vehicle_count() < 2) return;
  app_state_prev_vehicle();
  ipc_request_current_status();
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  if (app_state_vehicle_count() < 2) return;
  app_state_next_vehicle();
  ipc_request_current_status();
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
  if (app_state_current_vehicle()) ui_detail_push();
}

static void force_refresh(void) {
  const Vehicle *v = app_state_current_vehicle();
  if (!v) {
    ipc_request_list();
    return;
  }
  vibes_short_pulse();
  ipc_request_status(v->id, true);
}

static void select_long_click(ClickRecognizerRef ref, void *ctx) {
  force_refresh();
}

// Touch gestures: pull down to force-refresh, swipe left for detail,
// swipe right to quit. Only emery's headers declare the real
// recognizer API — the other platforms stub the functions out as
// no-op macros, so this whole block compiles only where it can work.
#if PBL_API_EXISTS(window_attach_recognizer)
static void pan_event(const Recognizer *recognizer, RecognizerEvent event) {
  if (event != RecognizerEvent_Completed) return;
  GRect b = layer_get_bounds(window_get_root_layer(s_window));
  if (pan_recognizer_get_total_delta(recognizer).y < b.size.h / 4) return;
  force_refresh();
}

static void swipe_event(const Recognizer *recognizer, RecognizerEvent event) {
  if (event != RecognizerEvent_Completed) return;
  switch (swipe_recognizer_get_direction(recognizer)) {
    case SwipeDirection_Left:
      if (app_state_current_vehicle()) ui_detail_push();
      break;
    case SwipeDirection_Right:
      // Pop everything so app_event_loop returns: swipe-right quits.
      window_stack_pop_all(true);
      break;
    default:
      break;
  }
}
#endif

static void click_config(void *context) {
  window_single_click_subscribe(BUTTON_ID_UP, up_click);
  window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
  window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
  window_long_click_subscribe(BUTTON_ID_SELECT, 500, select_long_click, NULL);
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);
  s_canvas = layer_create(bounds);
  layer_set_update_proc(s_canvas, canvas_update);
  layer_add_child(root, s_canvas);

  if (!s_bolt) s_bolt = gpath_create(&BOLT_PATH_INFO);

#if PBL_API_EXISTS(window_attach_recognizer)
  // The window owns these recognizers and destroys them on unload, so
  // each load attaches fresh ones — not a leak. Disabling the touch
  // bridge opts out of the system recognizer set so ours get the
  // touch stream.
  window_set_touch_bridge_disabled(window, true);
  window_attach_recognizer(
      window, pan_recognizer_create(pan_event, NULL, PanAxis_Vertical));
  window_attach_recognizer(
      window, swipe_recognizer_create(
                  swipe_event, NULL,
                  SwipeDirection_Left | SwipeDirection_Right));
#endif

  app_state_subscribe(on_state_changed);
}

static void window_appear(Window *window) {
  // Take the spinner back from the window that just popped; without
  // this the arc freezes until the next state change.
  on_state_changed();
}

static void window_unload(Window *window) {
  if (s_canvas) {
    spinner_detach(s_canvas);
    layer_destroy(s_canvas);
    s_canvas = NULL;
  }
}

void ui_main_push(void) {
  if (!s_window) {
    s_window = window_create();
    window_set_background_color(s_window, GColorBlack);
    window_set_click_config_provider(s_window, click_config);
    window_set_window_handlers(s_window, (WindowHandlers){
                                             .load = window_load,
                                             .appear = window_appear,
                                             .unload = window_unload,
                                         });
  }
  window_stack_push(s_window, true);
}

void ui_main_deinit(void) {
  if (s_bolt) {
    gpath_destroy(s_bolt);
    s_bolt = NULL;
  }
  if (s_window) {
    window_destroy(s_window);
    s_window = NULL;
  }
}
