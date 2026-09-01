#include "ui_detail.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"
#include "units.h"

static Window *s_window;
static Layer *s_canvas;

static void draw_row(GContext *ctx, GRect row, const char *label,
                     const char *value) {
  GFont label_font = fonts_get_system_font(LAYOUT_FONT_ROW_LABEL);
  // Where the rows have been squeezed — a round screen fitting seven of
  // them plus the footer — the full-size value font no longer fits the
  // row it is drawn in, so take the smaller face rather than the clip.
  GFont value_font = fonts_get_system_font(row.size.h >= LAYOUT_H_ROW_MIN_VALUE
                                               ? LAYOUT_FONT_ROW_VALUE
                                               : LAYOUT_FONT_ROW_VALUE_SMALL);
  // The values are set in a heavier font than the labels, so they get
  // the larger share of the row.
  int16_t label_w = (row.size.w * 45) / 100;
  GRect label_rect = GRect(row.origin.x, row.origin.y, label_w, row.size.h);
  GRect value_rect = GRect(row.origin.x + label_w, row.origin.y,
                           row.size.w - label_w, row.size.h);
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

  int16_t top = b.origin.y + LAYOUT_PAD_V;
  GFont title_font = fonts_get_system_font(LAYOUT_FONT_TITLE);
  graphics_draw_text(ctx, v->nickname, title_font,
                     layout_row(b, top, LAYOUT_H_TITLE),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);

  if (!v->have_status) {
    GFont body = fonts_get_system_font(LAYOUT_FONT_BODY);
    int16_t y = b.origin.y + b.size.h / 2 - LAYOUT_H_VALUE / 2;
    graphics_draw_text(ctx, "No data yet.", body,
                       layout_row(b, y, LAYOUT_H_VALUE),
                       GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
    return;
  }

  int16_t y = top + LAYOUT_H_TITLE + LAYOUT_GAP;
  char buf[24];

  // How old these readings are belongs on this screen more than on the
  // main one: the 12V percentage below is the reading the whole
  // rate-limiting design exists to let the owner watch, and a stale one
  // looks exactly like a live one. It gets a footer line of its own,
  // with the error state beside it rather than in place of it.
  const char *error = app_state_error();
  int16_t foot_y = b.origin.y + b.size.h - LAYOUT_PAD_V - LAYOUT_H_STATUS;

  // The charging case needs a seventh row, which only fits inside
  // chalk's round margins if the rows tighten up, so the height comes
  // from the space actually left between the title and the footer.
  // LAYOUT_H_ROW is the comfortable maximum, not the answer.
  int rows = v->is_charging ? 7 : 6;
  int16_t row_h = (foot_y - y) / rows;
  if (row_h > LAYOUT_H_ROW) row_h = LAYOUT_H_ROW;

  format_distance_km(v->odo_km, buf, sizeof(buf));
  draw_row(ctx, layout_row(b, y, row_h), "Odometer", buf);
  y += row_h;

  snprintf(buf, sizeof(buf), "%d C", v->outside_temp_c);
  draw_row(ctx, layout_row(b, y, row_h), "Outside", buf);
  y += row_h;

  draw_row(ctx, layout_row(b, y, row_h), "Climate",
           v->is_climate_on ? "On" : "Off");
  y += row_h;

  draw_row(ctx, layout_row(b, y, row_h), "Doors",
           v->doors_locked ? "Locked" : "Unlocked");
  y += row_h;

  // Zero is not a reading a live 12V battery can give, so it means the
  // proxy had nothing to report.
  if (v->aux_battery_pct > 0) {
    snprintf(buf, sizeof(buf), "%d%%", v->aux_battery_pct);
  } else {
    snprintf(buf, sizeof(buf), "--");
  }
  draw_row(ctx, layout_row(b, y, row_h), "12V", buf);
  y += row_h;

  if (v->is_charging) {
    snprintf(buf, sizeof(buf), "%d.%d kW", v->charge_kw_x10 / 10,
             v->charge_kw_x10 % 10);
    draw_row(ctx, layout_row(b, y, row_h), "Charging", buf);
    y += row_h;

    int h = v->charge_eta_min / 60;
    int m = v->charge_eta_min % 60;
    if (h > 0) {
      snprintf(buf, sizeof(buf), "%dh %02dm", h, m);
    } else {
      snprintf(buf, sizeof(buf), "%d min", m);
    }
    draw_row(ctx, layout_row(b, y, row_h), "ETA", buf);
  } else {
    draw_row(ctx, layout_row(b, y, row_h), "Charging", "Idle");
  }

  char ago_buf[16];
  format_age(v->updated_at, ago_buf, sizeof(ago_buf));
  char err_buf[32];
  const char *foot = ago_buf;
  if (error) {
#ifdef PBL_COLOR
    graphics_context_set_text_color(ctx, GColorFolly);
#endif
    snprintf(err_buf, sizeof(err_buf), "ERR  %s", ago_buf);
    foot = err_buf;
  }
  graphics_draw_text(ctx, foot, fonts_get_system_font(LAYOUT_FONT_STATUS),
                     layout_row(b, foot_y, LAYOUT_H_STATUS),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
  graphics_context_set_text_color(ctx, GColorWhite);
}

static void on_state_changed(void) {
  if (s_canvas) layer_mark_dirty(s_canvas);
}

static void up_click(ClickRecognizerRef ref, void *ctx) {
  app_state_prev_vehicle();
  ipc_request_current_status();
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  app_state_next_vehicle();
  ipc_request_current_status();
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
