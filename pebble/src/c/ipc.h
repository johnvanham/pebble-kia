#pragma once

#include <pebble.h>

void ipc_init(void);
void ipc_deinit(void);

void ipc_request_list(void);
void ipc_request_status(const char *id, bool force);
