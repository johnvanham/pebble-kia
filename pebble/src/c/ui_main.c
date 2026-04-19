#include "ui_main.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
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

static void format_ago(time_t when, char *out, size_t out_len) {
  if (when == 0) {
    snprintf(out, out_len, "--");
    return;
  }
  time_t now = time(NULL);
  int secs = (int)(now - when);
  if (secs < 0) secs = 0;
  if (secs < 60) {
    snprintf(out, out_len, "%ds ago", secs);
  } else if (secs < 3600) {
    snprintf(out, out_len, "%dm ago", secs / 60);
  } else {
    snprintf(out, out_len, "%dh ago", secs / 3600);
  }
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
  graphics_draw_rect(ctx, r);

  GRect nub = GRect(r.origin.x + r.size.w, r.origin.y + r.size.h / 4, 3,
                    r.size.h / 2);
  graphics_fill_rect(ctx, nub, 0, GCornerNone);

  int inner_w = r.size.w - 4;
  int fill_w = (inner_w * soc_pct) / 100;
  GRect fill_r = GRect(r.origin.x + 2, r.origin.y + 2, fill_w, r.size.h - 4);
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
  GFont tf = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
  GFont sf = fonts_get_system_font(FONT_KEY_GOTHIC_18);
  int y = b.size.h / 2 - 30;
  GRect tr = GRect(8, y, b.size.w - 16, 28);
  graphics_draw_text(ctx, title, tf, tr, GTextOverflowModeWordWrap,
                     GTextAlignmentCenter, NULL);
  if (sub && sub[0]) {
    GRect sr = GRect(8, y + 30, b.size.w - 16, b.size.h - y - 34);
    graphics_draw_text(ctx, sub, sf, sr, GTextOverflowModeWordWrap,
                       GTextAlignmentCenter, NULL);
  }
}

static void draw_indicator(GContext *ctx, GRect b) {
  const char *error = app_state_error();
  if (error) {
    GFont ind_font = fonts_get_system_font(FONT_KEY_GOTHIC_14);
#ifdef PBL_COLOR
    graphics_context_set_text_color(ctx, GColorFolly);
#endif
    GRect ind_rect = GRect(b.size.w - 40, 4, 36, 16);
    graphics_draw_text(ctx, "ERR", ind_font, ind_rect,
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentRight,
                       NULL);
    graphics_context_set_text_color(ctx, GColorWhite);
  } else if (app_state_is_busy()) {
    draw_spinner(ctx, GRect(b.size.w - 20, 4, 14, 14));
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
  GFont name_font = fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
  GRect name_rect = GRect(0, 2, b.size.w, 22);
  graphics_draw_text(ctx, v->nickname, name_font, name_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  draw_indicator(ctx, b);

  if (!v->have_status) {
    draw_centered_message(ctx, b, v->nickname, error ? error : "Loading...");
    return;
  }

  // --- Big SoC number ---
  GFont soc_font = fonts_get_system_font(FONT_KEY_LECO_42_NUMBERS);
  char soc_buf[8];
  snprintf(soc_buf, sizeof(soc_buf), "%d", v->soc_pct);
  int soc_y = 24;
  GRect soc_rect = GRect(0, soc_y, b.size.w - 24, 48);
  graphics_draw_text(ctx, soc_buf, soc_font, soc_rect,
                     GTextOverflowModeWordWrap, GTextAlignmentRight, NULL);

  GFont pct_font = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
  GRect pct_rect = GRect(b.size.w - 22, soc_y + 18, 22, 28);
  graphics_draw_text(ctx, "%", pct_font, pct_rect, GTextOverflowModeWordWrap,
                     GTextAlignmentLeft, NULL);

  // --- Battery bar ---
  int bar_w = b.size.w - 40;
  GRect bar = GRect(20, soc_y + 52, bar_w - 4, 10);
  draw_battery(ctx, bar, v->soc_pct, v->is_charging);

  // --- Range ---
  GFont range_font = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
  char range_buf[16];
  format_distance_km(v->range_km, range_buf, sizeof(range_buf));
  GRect range_rect = GRect(0, soc_y + 66, b.size.w, 26);
  graphics_draw_text(ctx, range_buf, range_font, range_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  // --- Status row (bottom). Error text replaces the normal line so the
  // user can actually read what went wrong without digging through logs.
  GFont status_font = fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD);
  char status_buf[APP_ERROR_LEN];
  if (error) {
#ifdef PBL_COLOR
    graphics_context_set_text_color(ctx, GColorFolly);
#endif
    strncpy(status_buf, error, sizeof(status_buf) - 1);
    status_buf[sizeof(status_buf) - 1] = 0;
  } else {
    char ago_buf[16];
    format_ago(v->updated_at, ago_buf, sizeof(ago_buf));
    snprintf(status_buf, sizeof(status_buf), "%s  %s  %s", plug_label(v->plug),
             v->doors_locked ? "LOCK" : "OPEN", ago_buf);
  }
  GRect status_rect = GRect(0, b.size.h - 20, b.size.w, 18);
  graphics_draw_text(ctx, status_buf, status_font, status_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
}

static void on_state_changed(void) {
  if (s_canvas) layer_mark_dirty(s_canvas);
  if (app_state_is_busy() && !app_state_error()) spinner_start();
  else spinner_stop();
}

// Fires once a minute so the "Xm ago" text stays fresh between data
// updates. Cheap — one mark_dirty per minute.
static void minute_tick(struct tm *t, TimeUnits units) {
  if (s_canvas) layer_mark_dirty(s_canvas);
}

static void maybe_request_current_status(void) {
  const Vehicle *v = app_state_current_vehicle();
  if (v && !v->have_status) ipc_request_status(v->id, false);
}

static void up_click(ClickRecognizerRef ref, void *ctx) {
  app_state_prev_vehicle();
  maybe_request_current_status();
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  app_state_next_vehicle();
  maybe_request_current_status();
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
  if (app_state_current_vehicle()) ui_detail_push();
}

static void select_long_click(ClickRecognizerRef ref, void *ctx) {
  const Vehicle *v = app_state_current_vehicle();
  if (!v) {
    ipc_request_list();
    return;
  }
  vibes_short_pulse();
  ipc_request_status(v->id, true);
}

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

  app_state_subscribe(on_state_changed);
  tick_timer_service_subscribe(MINUTE_UNIT, minute_tick);
}

static void window_unload(Window *window) {
  tick_timer_service_unsubscribe();
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
