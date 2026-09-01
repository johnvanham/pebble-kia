#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "ui_actions.h"
#include "ui_detail.h"
#include "ui_main.h"

// Both screens end in an "Xm ago" line, so a passing minute is a state
// change like any other and every listener redraws for it. The
// subscription belongs to the app rather than to a window because the
// tick service keeps exactly one handler: per-window subscriptions
// would have to hand it back and forth on appear/disappear, and a
// window unsubscribing would take the other screen's clock with it.
static void minute_tick(struct tm *t, TimeUnits units) {
  app_state_notify();
}

static void init(void) {
  app_state_init();
#if PBL_API_EXISTS(app_touch_navigation_enable)
  // Third-party apps get no touch input at all until they opt in.
  // Opting in feeds the windows' own recognizers; each window also
  // disables the system touch bridge so gestures reach them instead
  // of being synthesized into button presses.
  app_touch_navigation_enable(true);
#endif
  ipc_init();
  tick_timer_service_subscribe(MINUTE_UNIT, minute_tick);
  ui_main_push();
  // Fetch kicks off when the companion sends its "ready" nudge. If the
  // phone is unreachable, the user retries with select-long-press (or
  // a pull-down on a touchscreen watch).
}

static void deinit(void) {
  ui_actions_deinit();
  ui_detail_deinit();
  ui_main_deinit();
  ipc_deinit();
  app_state_deinit();
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
