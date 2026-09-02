#include "ui_actions.h"

#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "layout.h"

typedef struct {
  const char *label;
  const char *action;  // wire name, identical watch -> companion -> proxy
  bool confirm;
} Action;

// Commands that undo a protection (unlock, valet off) or interrupt a
// charge get a confirming Select first; the rest fire immediately.
static const Action s_actions[] = {
    {"Lock", "lock", false},
    {"Unlock", "unlock", true},
    {"Start charge", "start_charge", false},
    {"Stop charge", "stop_charge", true},
    {"Climate on", "start_climate", false},
    {"Climate off", "stop_climate", false},
    {"Open charge port", "open_charge_port", false},
    {"Close charge port", "close_charge_port", false},
    {"Valet on", "start_valet", true},
    {"Valet off", "stop_valet", true},
};

static Window *s_menu_window;
static MenuLayer *s_menu;
static Layer *s_status_layer;
static Window *s_confirm_window;
static Layer *s_confirm_canvas;
static int s_pending = -1;
// The confirm window ignores Select until this arms, so the second
// click of an accidental double-press on a risky menu row lands on a
// dead button instead of firing the command the confirm exists to
// guard. A deliberate confirm takes longer than this to read and press.
static bool s_confirm_armed;
static AppTimer *s_arm_timer;
#define CONFIRM_ARM_MS 500

static void send_action(int idx) {
  const Vehicle *v = app_state_current_vehicle();
  if (!v) return;
  ipc_request_action(v->id, s_actions[idx].action);
}

// One line under the menu carries the whole request lifecycle:
// "Sending..." while the message is in flight, the error text if it
// failed, "Sent" once the companion acknowledges.
static void status_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  const char *error = app_state_error();
  const char *text = NULL;
  // The action's own flag, not the general busy one: a launch wake or a
  // pull-down holds that for about half a minute, which would put
  // "Sending..." under a menu from which nothing has been sent, and
  // then blank the line when the wake's reply arrived mid-command.
  if (app_state_action_pending()) text = "Sending...";
  else if (error) text = error;
  else if (app_state_action_ok()) text = "Sent";
  if (!text) return;
#ifdef PBL_COLOR
  graphics_context_set_text_color(
      ctx, text == error ? GColorFolly : GColorWhite);
#else
  graphics_context_set_text_color(ctx, GColorWhite);
#endif
  graphics_draw_text(ctx, text, fonts_get_system_font(LAYOUT_FONT_STATUS),
                     GRect(b.origin.x, b.origin.y, b.size.w, LAYOUT_H_STATUS),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter,
                     NULL);
}

static void on_state_changed(void) {
  if (s_status_layer) layer_mark_dirty(s_status_layer);
}

// --- confirm window ---

static void confirm_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);
  if (s_pending < 0) return;
  char title[24];
  snprintf(title, sizeof(title), "%s?", s_actions[s_pending].label);
  int16_t y = b.origin.y + b.size.h / 2 - LAYOUT_H_VALUE;
  graphics_draw_text(ctx, title, fonts_get_system_font(LAYOUT_FONT_VALUE),
                     layout_row(b, y, LAYOUT_H_VALUE),
                     GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
  graphics_draw_text(ctx, "Select to confirm",
                     fonts_get_system_font(LAYOUT_FONT_BODY),
                     layout_row(b, y + LAYOUT_H_VALUE + LAYOUT_GAP,
                                LAYOUT_H_VALUE),
                     GTextOverflowModeWordWrap, GTextAlignmentCenter, NULL);
}

static void confirm_select(ClickRecognizerRef ref, void *ctx) {
  if (!s_confirm_armed) return;
  if (s_pending >= 0) send_action(s_pending);
  s_pending = -1;
  // Back to the menu, whose status line reports how the send goes.
  window_stack_pop(true);
}

static void confirm_click_config(void *context) {
  window_single_click_subscribe(BUTTON_ID_SELECT, confirm_select);
}

static void arm_confirm(void *ctx) {
  s_arm_timer = NULL;
  s_confirm_armed = true;
}

static void confirm_window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  s_confirm_canvas = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_confirm_canvas, confirm_update);
  layer_add_child(root, s_confirm_canvas);
}

static void confirm_window_appear(Window *window) {
  s_confirm_armed = false;
  s_arm_timer = app_timer_register(CONFIRM_ARM_MS, arm_confirm, NULL);
}

static void confirm_window_unload(Window *window) {
  if (s_arm_timer) {
    app_timer_cancel(s_arm_timer);
    s_arm_timer = NULL;
  }
  if (s_confirm_canvas) {
    layer_destroy(s_confirm_canvas);
    s_confirm_canvas = NULL;
  }
}

// The touch bridge stays enabled here too: with no recognizers of our
// own attached, the system synthesizes a Select from a tap on the
// centre of the screen, so the plain click handler covers both input
// paths. (Verified in the emery emulator.)
static void confirm_push(void) {
  if (!s_confirm_window) {
    s_confirm_window = window_create();
    window_set_background_color(s_confirm_window, GColorBlack);
    window_set_click_config_provider(s_confirm_window, confirm_click_config);
    window_set_window_handlers(s_confirm_window,
                               (WindowHandlers){
                                   .load = confirm_window_load,
                                   .appear = confirm_window_appear,
                                   .unload = confirm_window_unload,
                               });
  }
  window_stack_push(s_confirm_window, true);
}

// --- actions menu ---

static uint16_t get_num_rows(MenuLayer *menu, uint16_t section, void *ctx) {
  return ARRAY_LENGTH(s_actions);
}

static void draw_menu_row(GContext *ctx, const Layer *cell, MenuIndex *idx,
                          void *data) {
  menu_cell_basic_draw(ctx, cell, s_actions[idx->row].label, NULL, NULL);
}

static void menu_select(MenuLayer *menu, MenuIndex *idx, void *data) {
  if (s_actions[idx->row].confirm) {
    s_pending = idx->row;
    confirm_push();
  } else {
    send_action(idx->row);
  }
}

static void menu_window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect b = layer_get_bounds(root);
  int16_t status_h = LAYOUT_H_STATUS + LAYOUT_PAD_V;

  // Also clear it here, not just on unload: an ack that lands after the
  // menu was popped (Back pressed while the action was in flight) sets
  // the flag with no menu on screen, and this keeps that "Sent" from
  // greeting the next, unrelated visit.
  app_state_set_action_ok(false);

  // No recognizers and no touch-bridge opt-out on this window, on
  // purpose: the system bridge already gives a MenuLayer touch
  // scrolling, tap-to-select and swipe-right-back, so recognizers of
  // our own would only fight it.
  s_menu = menu_layer_create(
      GRect(b.origin.x, b.origin.y, b.size.w, b.size.h - status_h));
  menu_layer_set_callbacks(s_menu, NULL,
                           (MenuLayerCallbacks){
                               .get_num_rows = get_num_rows,
                               .draw_row = draw_menu_row,
                               .select_click = menu_select,
                           });
  menu_layer_set_normal_colors(s_menu, GColorBlack, GColorWhite);
  menu_layer_set_highlight_colors(
      s_menu, PBL_IF_COLOR_ELSE(GColorVividCerulean, GColorWhite),
      PBL_IF_COLOR_ELSE(GColorWhite, GColorBlack));
  menu_layer_set_click_config_onto_window(s_menu, window);
  layer_add_child(root, menu_layer_get_layer(s_menu));

  s_status_layer = layer_create(GRect(
      b.origin.x, b.origin.y + b.size.h - status_h, b.size.w, status_h));
  layer_set_update_proc(s_status_layer, status_update);
  layer_add_child(root, s_status_layer);

  app_state_subscribe(on_state_changed);
}

static void menu_window_unload(Window *window) {
  // "Sent" is about the action fired from this visit to the menu;
  // don't let it greet the next one.
  app_state_set_action_ok(false);
  if (s_menu) {
    menu_layer_destroy(s_menu);
    s_menu = NULL;
  }
  if (s_status_layer) {
    layer_destroy(s_status_layer);
    s_status_layer = NULL;
  }
}

void ui_actions_push(void) {
  if (!s_menu_window) {
    s_menu_window = window_create();
    window_set_background_color(s_menu_window, GColorBlack);
    window_set_window_handlers(s_menu_window,
                               (WindowHandlers){
                                   .load = menu_window_load,
                                   .unload = menu_window_unload,
                               });
  }
  window_stack_push(s_menu_window, true);
}

void ui_actions_deinit(void) {
  if (s_confirm_window) {
    window_destroy(s_confirm_window);
    s_confirm_window = NULL;
  }
  if (s_menu_window) {
    window_destroy(s_menu_window);
    s_menu_window = NULL;
  }
}
