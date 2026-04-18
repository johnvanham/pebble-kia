#include "ui_main.h"

#include <pebble.h>

#include "app_state.h"
#include "demo_data.h"
#include "ui_detail.h"

static Window *s_window;
static Layer *s_canvas;

static void format_ago(time_t when, char *out, size_t out_len) {
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

static void draw_battery(GContext *ctx, GRect r, uint8_t soc_pct,
                         bool charging) {
#ifdef PBL_COLOR
  GColor fill = charging    ? GColorIslamicGreen
                : soc_pct < 20 ? GColorFolly
                               : GColorVividCerulean;
  graphics_context_set_fill_color(ctx, fill);
#else
  graphics_context_set_fill_color(ctx, GColorWhite);
#endif
  graphics_context_set_stroke_color(ctx, GColorWhite);

  graphics_draw_rect(ctx, r);

  GRect nub = GRect(r.origin.x + r.size.w, r.origin.y + r.size.h / 4,
                    3, r.size.h / 2);
  graphics_fill_rect(ctx, nub, 0, GCornerNone);

  int inner_w = r.size.w - 4;
  int fill_w = (inner_w * soc_pct) / 100;
  GRect fill_r = GRect(r.origin.x + 2, r.origin.y + 2, fill_w, r.size.h - 4);
  graphics_fill_rect(ctx, fill_r, 0, GCornerNone);
}

static void canvas_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  const Vehicle *v = app_state_current_vehicle();
  if (!v) return;

  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);

  // --- Name (top) ---
  GFont name_font = fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
  GRect name_rect = GRect(0, 2, b.size.w, 22);
  graphics_draw_text(ctx, v->name, name_font, name_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  // --- DEMO badge (top-right) ---
  GFont demo_font = fonts_get_system_font(FONT_KEY_GOTHIC_14);
#ifdef PBL_COLOR
  graphics_context_set_text_color(ctx, GColorChromeYellow);
#endif
  GRect demo_rect = GRect(b.size.w - 44, 4, 40, 16);
  graphics_draw_text(ctx, "DEMO", demo_font, demo_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentRight,
                     NULL);
  graphics_context_set_text_color(ctx, GColorWhite);

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
  snprintf(range_buf, sizeof(range_buf), "%d km", v->range_km);
  GRect range_rect = GRect(0, soc_y + 66, b.size.w, 26);
  graphics_draw_text(ctx, range_buf, range_font, range_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  // --- Status row (bottom) ---
  GFont status_font = fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD);
  char status_buf[48];
  char ago_buf[16];
  format_ago(v->updated_at, ago_buf, sizeof(ago_buf));
  snprintf(status_buf, sizeof(status_buf), "%s  %s  %s", plug_label(v->plug),
           v->doors_locked ? "LOCK" : "OPEN", ago_buf);
  GRect status_rect = GRect(0, b.size.h - 20, b.size.w, 18);
  graphics_draw_text(ctx, status_buf, status_font, status_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
}

static void on_state_changed(void) {
  if (s_canvas) layer_mark_dirty(s_canvas);
}

static void up_click(ClickRecognizerRef ref, void *ctx) {
  app_state_prev_vehicle();
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  app_state_next_vehicle();
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
  ui_detail_push();
}

static void select_long_click(ClickRecognizerRef ref, void *ctx) {
  demo_data_simulate_refresh(app_state_current_index());
  vibes_short_pulse();
  app_state_notify();
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
}

static void window_unload(Window *window) {
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
