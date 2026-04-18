#include "app_state.h"

#define MAX_LISTENERS 4

static int s_current_index = 0;
static AppStateListener s_listeners[MAX_LISTENERS];
static int s_listener_count = 0;

void app_state_init(void) {
  s_current_index = 0;
  s_listener_count = 0;
}

void app_state_deinit(void) {
  s_listener_count = 0;
}

int app_state_current_index(void) { return s_current_index; }

const Vehicle *app_state_current_vehicle(void) {
  return demo_data_get(s_current_index);
}

void app_state_next_vehicle(void) {
  int n = demo_data_count();
  if (n <= 0) return;
  s_current_index = (s_current_index + 1) % n;
  app_state_notify();
}

void app_state_prev_vehicle(void) {
  int n = demo_data_count();
  if (n <= 0) return;
  s_current_index = (s_current_index - 1 + n) % n;
  app_state_notify();
}

void app_state_subscribe(AppStateListener listener) {
  for (int i = 0; i < s_listener_count; i++) {
    if (s_listeners[i] == listener) return;
  }
  if (s_listener_count >= MAX_LISTENERS) return;
  s_listeners[s_listener_count++] = listener;
}

void app_state_notify(void) {
  for (int i = 0; i < s_listener_count; i++) {
    if (s_listeners[i]) s_listeners[i]();
  }
}
