#include "ui_detail.h"

#include <pebble.h>

#include "app_state.h"
#include "demo_data.h"
#include "units.h"

static Window *s_window;
static Layer *s_canvas;

static void draw_row(GContext *ctx, GRect row, const char *label,
                     const char *value) {
  GFont label_font = fonts_get_system_font(FONT_KEY_GOTHIC_14);
  GFont value_font = fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
  GRect label_rect = GRect(row.origin.x, row.origin.y, row.size.w / 2, row.size.h);
  GRect value_rect = GRect(row.origin.x + row.size.w / 2, row.origin.y,
                           row.size.w / 2, row.size.h);
  graphics_draw_text(ctx, label, label_font, label_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft,
                     NULL);
  graphics_draw_text(ctx, value, value_font, value_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentRight,
                     NULL);
}

static void canvas_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  const Vehicle *v = app_state_current_vehicle();
  if (!v) return;

  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);

  // Title
  GFont title_font = fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
  GRect title_rect = GRect(0, 2, b.size.w, 22);
  graphics_draw_text(ctx, v->name, title_font, title_rect,
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  // Rows
  int y = 28;
  int row_h = 22;
  int padding_x = 10;
  int row_w = b.size.w - padding_x * 2;

  char buf[24];

  format_distance_km(v->odo_km, buf, sizeof(buf));
  draw_row(ctx, GRect(padding_x, y, row_w, row_h), "Odometer", buf);
  y += row_h;

  snprintf(buf, sizeof(buf), "%d C", v->cabin_temp_c);
  draw_row(ctx, GRect(padding_x, y, row_w, row_h), "Cabin", buf);
  y += row_h;

  draw_row(ctx, GRect(padding_x, y, row_w, row_h), "Doors",
           v->doors_locked ? "Locked" : "Unlocked");
  y += row_h;

  if (v->is_charging) {
    snprintf(buf, sizeof(buf), "%d.%d kW", v->charge_kw_x10 / 10,
             v->charge_kw_x10 % 10);
    draw_row(ctx, GRect(padding_x, y, row_w, row_h), "Charging", buf);
    y += row_h;

    int h = v->charge_eta_min / 60;
    int m = v->charge_eta_min % 60;
    if (h > 0) {
      snprintf(buf, sizeof(buf), "%dh %02dm", h, m);
    } else {
      snprintf(buf, sizeof(buf), "%d min", m);
    }
    draw_row(ctx, GRect(padding_x, y, row_w, row_h), "ETA", buf);
    y += row_h;
  } else {
    draw_row(ctx, GRect(padding_x, y, row_w, row_h), "Charging", "Idle");
    y += row_h;
  }
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

static void click_config(void *context) {
  window_single_click_subscribe(BUTTON_ID_UP, up_click);
  window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  s_canvas = layer_create(layer_get_bounds(root));
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

void ui_detail_push(void) {
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

void ui_detail_deinit(void) {
  if (s_window) {
    window_destroy(s_window);
    s_window = NULL;
  }
}
