#pragma once

#include <pebble.h>

void ipc_init(void);
void ipc_deinit(void);

void ipc_request_list(void);
void ipc_request_status(const char *id, bool force);
// Asks the companion to run one remote command (lock, start_charge,
// ...). The reply is RESP_KIND=action_ok or the usual error path.
void ipc_request_action(const char *id, const char *action);
// Fetches whichever vehicle is selected now, or, if the link is busy,
// as soon as it frees up.
void ipc_request_current_status(void);
