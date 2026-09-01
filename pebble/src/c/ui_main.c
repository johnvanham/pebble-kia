#include "ui_main.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"
#include "ui_detail.h"
#include "units.h"

static Window *s_window;
static Layer *s_canvas;
static AppTimer *s_spinner_timer = NULL;
static int32_t s_spinner_angle = 0;

// Tick every 100 ms while busy — fast enough to look smooth, slow
// enough not to wake the CPU hard. Each step rotates the arc by 1/12
// of a full turn, i.e. ~100 deg/s.
#define SPINNER_TICK_MS 100
#define SPINNER_STEP (TRIG_MAX_ANGLE / 12)

static void spinner_tick(void *ctx);

static void spinner_start(void) {
  if (s_spinner_timer) return;
  s_spinner_timer = app_timer_register(SPINNER_TICK_MS, spinner_tick, NULL);
}

static void spinner_stop(void) {
  if (s_spinner_timer) {
    app_timer_cancel(s_spinner_timer);
    s_spinner_timer = NULL;
  }
}

static void spinner_tick(void *ctx) {
  s_spinner_timer = NULL;
  s_spinner_angle = (s_spinner_angle + SPINNER_STEP) % TRIG_MAX_ANGLE;
  if (s_canvas) layer_mark_dirty(s_canvas);
  if (app_state_is_busy() && !app_state_error()) spinner_start();
}

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

static void draw_spinner(GContext *ctx, GRect box) {
#ifdef PBL_COLOR
  graphics_context_set_stroke_color(ctx, GColorChromeYellow);
#else
  graphics_context_set_stroke_color(ctx, GColorWhite);
#endif
  graphics_context_set_stroke_width(ctx, 2);
  graphics_draw_arc(ctx, box, GOvalScaleModeFitCircle,
                    s_spinner_angle,
                    s_spinner_angle + (TRIG_MAX_ANGLE * 3 / 4));
  graphics_context_set_stroke_width(ctx, 1);
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
    draw_spinner(ctx, GRect(right - LAYOUT_D_SPINNER, row.origin.y,
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

  // --- Big SoC number ---
  GFont soc_font = fonts_get_system_font(LAYOUT_FONT_SOC);
  char soc_buf[8];
  snprintf(soc_buf, sizeof(soc_buf), "%d", v->soc_pct);
  GRect soc_row = layout_row(b, y, LAYOUT_H_SOC);
  GRect soc_rect = GRect(soc_row.origin.x, y, soc_row.size.w - LAYOUT_W_PCT,
                         LAYOUT_H_SOC);
  graphics_draw_text(ctx, soc_buf, soc_font, soc_rect,
                     GTextOverflowModeWordWrap, GTextAlignmentRight, NULL);

  GFont pct_font = fonts_get_system_font(LAYOUT_FONT_PCT);
  GRect pct_rect = GRect(soc_row.origin.x + soc_row.size.w - LAYOUT_W_PCT,
                         y + LAYOUT_H_SOC - LAYOUT_H_PCT - 2, LAYOUT_W_PCT,
                         LAYOUT_H_PCT);
  graphics_draw_text(ctx, "%", pct_font, pct_rect, GTextOverflowModeWordWrap,
                     GTextAlignmentLeft, NULL);

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
  if (s_canvas) layer_mark_dirty(s_canvas);
  if (app_state_is_busy() && !app_state_error()) spinner_start();
  else spinner_stop();
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

static void window_unload(Window *window) {
  spinner_stop();
  if (s_canvas) {
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
                                             .unload = window_unload,
                                         });
  }
  window_stack_push(s_window, true);
}

void ui_main_deinit(void) {
  if (s_window) {
    window_destroy(s_window);
    s_window = NULL;
  }
}
