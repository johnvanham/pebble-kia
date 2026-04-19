#include <pebble.h>

#include "app_state.h"
#include "ipc.h"
#include "ui_detail.h"
#include "ui_main.h"

static void init(void) {
  app_state_init();
  ipc_init();
  ui_main_push();
  // Fetch kicks off when the companion sends its "ready" nudge. If the
  // phone is unreachable, the user hits select-long-press to retry.
}

static void deinit(void) {
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
