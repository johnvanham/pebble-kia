#include "ui_detail.h"

#include <pebble.h>
#include <string.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"
#include "spinner.h"
#include "ui_actions.h"
#include "units.h"

static Window *s_window;
static Layer *s_canvas;

// Fourteen rows (fifteen while charging) no longer fit any of the
// screens, so
// the row region scrolls between a pinned title and a pinned footer.
// The offset is clamped against s_scroll_max, which canvas_update
// recomputes from the real geometry every paint — the row count changes
// with the charging state.
static int16_t s_scroll;
static int16_t s_scroll_max;

static void draw_row(GContext *ctx, GRect row, const char *label,
                     const char *value) {
  // A row scrolled far off a round screen gets no chord to sit in, and
  // the value rect below would then come out negative-width, which
  // faults the app. Now that the list is long enough to scroll a row
  // right past the edge, that is reachable on chalk.
  if (row.size.w <= 0) return;
  GFont label_font = fonts_get_system_font(LAYOUT_FONT_ROW_LABEL);
  GFont value_font = fonts_get_system_font(LAYOUT_FONT_ROW_VALUE);
  // The value gets every pixel the label doesn't need: a fixed split
  // wide enough for "Odometer" starved "AC 80  DC 100" of the room it
  // needs next to a label as short as "Limit".
  GSize label_size = graphics_text_layout_get_content_size(
      label, label_font, row, GTextOverflowModeTrailingEllipsis,
      GTextAlignmentLeft);
  int16_t label_w = label_size.w + LAYOUT_GAP;
  if (label_w > row.size.w) label_w = row.size.w;
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

static void format_open(const Vehicle *v, char *buf, size_t sz) {
  const struct {
    bool on;
    int count;
    const char *word;
  } parts[] = {
      {v->doors_open > 0, v->doors_open, "door"},
      {v->windows_open > 0, v->windows_open, "win"},
      {v->trunk_open, 0, "trunk"},
      {v->hood_open, 0, "hood"},
      {v->sunroof_open, 0, "roof"},
  };
  buf[0] = 0;
  size_t n = 0;
  for (size_t i = 0; i < ARRAY_LENGTH(parts) && n < sz; i++) {
    if (!parts[i].on) continue;
    if (parts[i].count > 0) {
      n += snprintf(buf + n, sz - n, "%s%d %s", n ? ", " : "",
                    parts[i].count, parts[i].word);
    } else {
      n += snprintf(buf + n, sz - n, "%s%s", n ? ", " : "", parts[i].word);
    }
  }
  if (!buf[0]) snprintf(buf, sz, "Closed");
}

static void format_heaters(const Vehicle *v, char *buf, size_t sz) {
  const struct {
    bool on;
    const char *word;
  } parts[] = {
      {v->defrost_on, "front"},
      {v->rear_defrost_on, "rear"},
      {v->wheel_heat_on, "wheel"},
  };
  buf[0] = 0;
  size_t n = 0;
  for (size_t i = 0; i < ARRAY_LENGTH(parts) && n < sz; i++) {
    if (!parts[i].on) continue;
    n += snprintf(buf + n, sz - n, "%s%s", n ? ", " : "", parts[i].word);
  }
  if (!buf[0]) snprintf(buf, sz, "Off");
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
  const char *error = app_state_error();

  if (!v->have_status) {
    s_scroll_max = 0;
    graphics_draw_text(ctx, v->nickname, title_font,
                       layout_row(b, top, LAYOUT_H_TITLE),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                       NULL);
    GFont body = fonts_get_system_font(LAYOUT_FONT_BODY);
    int16_t y = b.origin.y + b.size.h / 2 - LAYOUT_H_VALUE / 2;
    graphics_draw_text(ctx, "No data yet.", body,
                       layout_row(b, y, LAYOUT_H_VALUE),
                       GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
    return;
  }

  int16_t content_top = top + LAYOUT_H_TITLE + LAYOUT_GAP;
  // How old these readings are belongs on this screen more than on the
  // main one: the 12V percentage below is what shows whether all the
  // car-waking reads are costing the battery anything, and a stale one
  // looks exactly like a live one. It gets a footer line of its own,
  // with the error state beside it rather than in place of it.
  int16_t foot_y = b.origin.y + b.size.h - LAYOUT_PAD_V - LAYOUT_H_STATUS;

  int rows = v->is_charging ? 15 : 14;
  int16_t content_h = rows * LAYOUT_H_ROW;
  int16_t viewport_h = foot_y - content_top;
  s_scroll_max = content_h > viewport_h ? content_h - viewport_h : 0;
  if (s_scroll > s_scroll_max) s_scroll = s_scroll_max;

  char buf[24];
  int16_t y = content_top - s_scroll;

  format_distance_km(v->odo_km, buf, sizeof(buf));
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Odometer", buf);
  y += LAYOUT_H_ROW;

  snprintf(buf, sizeof(buf), "%d C", v->outside_temp_c);
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Outside", buf);
  y += LAYOUT_H_ROW;

  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Climate",
           v->is_climate_on ? "On" : "Off");
  y += LAYOUT_H_ROW;

  format_heaters(v, buf, sizeof(buf));
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Heaters", buf);
  y += LAYOUT_H_ROW;

  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Doors",
           v->doors_locked ? "Locked" : "Unlocked");
  y += LAYOUT_H_ROW;

  // Zero is not a reading a live 12V battery can give, so it means the
  // proxy had nothing to report.
  if (v->aux_battery_pct > 0) {
    snprintf(buf, sizeof(buf), "%d%%", v->aux_battery_pct);
  } else {
    snprintf(buf, sizeof(buf), "--");
  }
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "12V", buf);
  y += LAYOUT_H_ROW;

  if (v->is_charging) {
    snprintf(buf, sizeof(buf), "%d.%d kW", v->charge_kw_x10 / 10,
             v->charge_kw_x10 % 10);
    draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Charging", buf);
    y += LAYOUT_H_ROW;

    int h = v->charge_eta_min / 60;
    int m = v->charge_eta_min % 60;
    if (h > 0) {
      snprintf(buf, sizeof(buf), "%dh %02dm", h, m);
    } else {
      snprintf(buf, sizeof(buf), "%d min", m);
    }
    draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "ETA", buf);
    y += LAYOUT_H_ROW;
  } else {
    draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Charging", "Idle");
    y += LAYOUT_H_ROW;
  }

  if (v->charge_limit_ac == 0 && v->charge_limit_dc == 0) {
    snprintf(buf, sizeof(buf), "--");
  } else {
    snprintf(buf, sizeof(buf), "AC %d  DC %d", v->charge_limit_ac,
             v->charge_limit_dc);
  }
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Limit", buf);
  y += LAYOUT_H_ROW;

  // What the car expects to have in it once each limit is reached —
  // the number that makes an 80% cap mean something.
  if (v->target_range_ac_km == 0 && v->target_range_dc_km == 0) {
    snprintf(buf, sizeof(buf), "--");
  } else {
    char ac[12];
    char dc[12];
    format_distance_km(v->target_range_ac_km, ac, sizeof(ac));
    format_distance_km(v->target_range_dc_km, dc, sizeof(dc));
    // Only the second reading needs its unit spelled out.
    char *space = strchr(ac, ' ');
    if (space) *space = 0;
    snprintf(buf, sizeof(buf), "%s/%s", ac, dc);
  }
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "At limit", buf);
  y += LAYOUT_H_ROW;

  // The rate while something is actually plugged into the socket,
  // otherwise the floor it will discharge down to.
  if (v->v2l_kw_x10 > 0) {
    snprintf(buf, sizeof(buf), "%d.%d kW", v->v2l_kw_x10 / 10,
             v->v2l_kw_x10 % 10);
  } else if (v->v2l_limit_pct > 0) {
    snprintf(buf, sizeof(buf), "to %d%%", v->v2l_limit_pct);
  } else {
    snprintf(buf, sizeof(buf), "--");
  }
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "V2L", buf);
  y += LAYOUT_H_ROW;

  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Cond",
           v->batt_conditioning ? "On" : "Off");
  y += LAYOUT_H_ROW;

  format_open(v, buf, sizeof(buf));
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Open", buf);
  y += LAYOUT_H_ROW;

  format_efficiency(v->eff_kmpkwh_x10, buf, sizeof(buf));
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Effcy", buf);
  y += LAYOUT_H_ROW;

  if (v->batt_temp_c == BATT_TEMP_NONE) {
    snprintf(buf, sizeof(buf), "--");
  } else {
    snprintf(buf, sizeof(buf), "%d C", v->batt_temp_c);
  }
  draw_row(ctx, layout_row(b, y, LAYOUT_H_ROW), "Batt temp", buf);

  // Pinned title and footer last, each over a black band, so the rows
  // slide beneath them instead of through them.
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, GRect(b.origin.x, b.origin.y, b.size.w,
                                content_top - b.origin.y),
                     0, GCornerNone);
  graphics_fill_rect(ctx, GRect(b.origin.x, foot_y, b.size.w,
                                b.origin.y + b.size.h - foot_y),
                     0, GCornerNone);

  graphics_draw_text(ctx, v->nickname, title_font,
                     layout_row(b, top, LAYOUT_H_TITLE),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
  if (app_state_is_busy() && !error) {
    GRect ind = layout_row(b, top, LAYOUT_H_IND);
    spinner_draw(ctx, GRect(ind.origin.x + ind.size.w - LAYOUT_D_SPINNER,
                            ind.origin.y, LAYOUT_D_SPINNER,
                            LAYOUT_D_SPINNER));
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
  if (!s_canvas) return;
  layer_mark_dirty(s_canvas);
  spinner_sync(s_canvas);
}

static void scroll_by(int16_t dy) {
  int16_t next = s_scroll + dy;
  if (next > s_scroll_max) next = s_scroll_max;
  if (next < 0) next = 0;
  if (next == s_scroll) return;
  s_scroll = next;
  if (s_canvas) layer_mark_dirty(s_canvas);
}

// Up/Down scroll the rows here; vehicle switching stays on the main
// screen, which is also where it makes sense to be when picking.
static void up_click(ClickRecognizerRef ref, void *ctx) {
  scroll_by(-2 * LAYOUT_H_ROW);
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  scroll_by(2 * LAYOUT_H_ROW);
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
  if (app_state_current_vehicle()) ui_actions_push();
}

// Touch gestures: the vertical pan does double duty, swipe right goes
// back, swipe left opens the actions menu. Only emery's headers declare
// the real recognizer API — the other platforms stub the functions out
// as no-op macros, so this whole block compiles only where it can work.
#if PBL_API_EXISTS(window_attach_recognizer)
static bool s_pan_refresh;

// A downward drag that starts with the rows at the top is a
// pull-to-refresh, matching the main screen; any other drag
// live-scrolls. The choice is made once, when the pan starts, so a
// scroll that happens to end at the top can never turn into an
// accidental car wake.
static void pan_event(const Recognizer *recognizer, RecognizerEvent event) {
  switch (event) {
    case RecognizerEvent_Started:
      s_pan_refresh = s_scroll == 0 &&
                      pan_recognizer_get_total_delta(recognizer).y > 0;
      break;
    case RecognizerEvent_Updated:
      if (!s_pan_refresh) {
        scroll_by(-pan_recognizer_get_delta_since_prev(recognizer).y);
      }
      break;
    case RecognizerEvent_Completed: {
      if (!s_pan_refresh) break;
      GRect b = layer_get_bounds(window_get_root_layer(s_window));
      if (pan_recognizer_get_total_delta(recognizer).y < b.size.h / 4) break;
      const Vehicle *v = app_state_current_vehicle();
      if (!v) break;
      vibes_short_pulse();
      ipc_request_status(v->id, true);
      break;
    }
    default:
      break;
  }
}

static void swipe_event(const Recognizer *recognizer, RecognizerEvent event) {
  if (event != RecognizerEvent_Completed) return;
  switch (swipe_recognizer_get_direction(recognizer)) {
    case SwipeDirection_Left:
      if (app_state_current_vehicle()) ui_actions_push();
      break;
    case SwipeDirection_Right:
      window_stack_pop(true);
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
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  s_canvas = layer_create(layer_get_bounds(root));
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

static void window_appear(Window *window) {
  s_scroll = 0;
  on_state_changed();
}

static void window_unload(Window *window) {
  if (s_canvas) {
    spinner_detach(s_canvas);
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
                                             .appear = window_appear,
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
