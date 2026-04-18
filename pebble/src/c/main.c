#include <pebble.h>

#include "app_state.h"
#include "demo_data.h"
#include "ui_detail.h"
#include "ui_main.h"

static void init(void) {
  demo_data_load();
  app_state_init();
  ui_main_push();
}

static void deinit(void) {
  ui_detail_deinit();
  ui_main_deinit();
  app_state_deinit();
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
