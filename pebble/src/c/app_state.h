#pragma once

#include <pebble.h>
#include "demo_data.h"

void app_state_init(void);
void app_state_deinit(void);

int app_state_current_index(void);
const Vehicle *app_state_current_vehicle(void);
void app_state_next_vehicle(void);
void app_state_prev_vehicle(void);

typedef void (*AppStateListener)(void);
void app_state_subscribe(AppStateListener listener);
void app_state_notify(void);
