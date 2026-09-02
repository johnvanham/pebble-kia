#include "ui_charge_limit.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"

// Kia writes the AC and DC targets as one pair, so this screen always
// sends both — there is no way to change one and leave the other as the
// car has it. The proxy rejects anything off this grid rather than
// rounding it, so the grid here is the contract.
#define LIMIT_MIN 50
#define LIMIT_MAX 100
#define LIMIT_STEP 10

enum { ROW_AC = 0, ROW_DC, ROW_SEND, ROW_COUNT };

static Window *s_window;
static Layer *s_canvas;
static int s_cursor;
static uint8_t s_ac;
static uint8_t s_dc;

// 0 means "not known", the same encoding the status carries for a limit
// the car never reported. It must not be seeded with a plausible-looking
// guess: Kia writes the AC and DC targets together, so a made-up 80%
// sitting under the AC label would be written to a car whose real limit
// was something else the moment the user came here to change only DC.
// Anything the car did report is snapped onto the grid, because the
// value shown has to be one this screen can actually send.
static uint8_t seed(uint8_t reported) {
  if (reported < LIMIT_MIN || reported > LIMIT_MAX) return 0;
  return reported - (reported % LIMIT_STEP);
}

static bool ready_to_send(void) { return s_ac != 0 && s_dc != 0; }

static void format_value(uint8_t pct, char *buf, size_t sz) {
  if (pct == 0) snprintf(buf, sz, "--");
  else snprintf(buf, sz, "%d%%", pct);
}

static void draw_row(GContext *ctx, GRect b, int16_t y, int row,
                     const char *label, const char *value) {
  GRect rect = layout_row(b, y, LAYOUT_H_ROW);
  bool selected = row == s_cursor;
  if (selected) {
    graphics_context_set_fill_color(ctx, GColorWhite);
    graphics_fill_rect(ctx, rect, 0, GCornerNone);
  }
  graphics_context_set_text_color(ctx, selected ? GColorBlack : GColorWhite);
  graphics_draw_text(ctx, label, fonts_get_system_font(LAYOUT_FONT_ROW_LABEL),
                     rect, GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  if (value) {
    graphics_draw_text(ctx, value,
                       fonts_get_system_font(LAYOUT_FONT_ROW_VALUE), rect,
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentRight,
                       NULL);
  }
  graphics_context_set_text_color(ctx, GColorWhite);
}

static void canvas_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);

  int16_t y = b.origin.y + LAYOUT_PAD_V;
  graphics_draw_text(ctx, "Charge limit",
                     fonts_get_system_font(LAYOUT_FONT_TITLE),
                     layout_row(b, y, LAYOUT_H_TITLE),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
  y += LAYOUT_H_TITLE + LAYOUT_GAP;

  char buf[8];
  format_value(s_ac, buf, sizeof(buf));
  draw_row(ctx, b, y, ROW_AC, "AC", buf);
  y += LAYOUT_H_ROW;

  format_value(s_dc, buf, sizeof(buf));
  draw_row(ctx, b, y, ROW_DC, "DC", buf);
  y += LAYOUT_H_ROW;

  draw_row(ctx, b, y, ROW_SEND, "Set limits", NULL);

  // All three strings are kept short enough for the chord chalk gives
  // the footer, which is about thirteen characters wide.
  const char *hint;
  if (s_cursor != ROW_SEND) hint = "Up/Down sets";
  else if (ready_to_send()) hint = "Select sends";
  else hint = "Both needed";
  graphics_draw_text(ctx, hint, fonts_get_system_font(LAYOUT_FONT_STATUS),
                     layout_row(b, b.origin.y + b.size.h - LAYOUT_PAD_V -
                                       LAYOUT_H_STATUS,
                                LAYOUT_H_STATUS),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
}

static void adjust(int delta) {
  uint8_t *value = s_cursor == ROW_AC ? &s_ac
                 : s_cursor == ROW_DC ? &s_dc
                                      : NULL;
  if (!value) return;
  // An unknown value has no position on the grid to step from, so the
  // first press lands on whichever end the direction implies. That is a
  // choice the user made rather than one the watch invented.
  int next = *value == 0 ? (delta > 0 ? LIMIT_MIN : LIMIT_MAX)
                         : *value + delta * LIMIT_STEP;
  if (next < LIMIT_MIN || next > LIMIT_MAX) return;
  *value = (uint8_t)next;
  layer_mark_dirty(s_canvas);
}

// Up doubles as the way back off the send row: Select only ever moves
// forwards, so without this an overshoot would mean leaving the screen
// and starting again. It returns to the top rather than one row, which
// is what keeps every row reachable — landing on DC would strand an
// unset AC, since Up on a value row adjusts the value instead of
// moving.
static void up_click(ClickRecognizerRef ref, void *ctx) {
  if (s_cursor == ROW_SEND) {
    s_cursor = ROW_AC;
    layer_mark_dirty(s_canvas);
    return;
  }
  adjust(+1);
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
  adjust(-1);
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
  if (s_cursor != ROW_SEND) {
    s_cursor++;
    layer_mark_dirty(s_canvas);
    return;
  }
  // Kia takes the pair or nothing, so a half-filled screen has nothing
  // safe to send: the missing half would go up as a guess.
  if (!ready_to_send()) return;
  const Vehicle *v = app_state_current_vehicle();
  if (v) ipc_request_charge_limit(v->id, s_ac, s_dc);
  // Back to the actions menu, whose status line reports how the send
  // goes — the same path the confirm window takes.
  window_stack_pop(true);
}

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
}

static void window_appear(Window *window) {
  const Vehicle *v = app_state_current_vehicle();
  s_cursor = ROW_AC;
  s_ac = seed(v ? v->charge_limit_ac : 0);
  s_dc = seed(v ? v->charge_limit_dc : 0);
  layer_mark_dirty(s_canvas);
}

static void window_unload(Window *window) {
  if (s_canvas) {
    layer_destroy(s_canvas);
    s_canvas = NULL;
  }
}

// The system touch bridge stays on, as on the other menu-shaped
// windows: it turns a tap into Select and a right swipe into Back,
// which is exactly what this screen wants.
void ui_charge_limit_push(void) {
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

void ui_charge_limit_deinit(void) {
  if (s_window) {
    window_destroy(s_window);
    s_window = NULL;
  }
}
