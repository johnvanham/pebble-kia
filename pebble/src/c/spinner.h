#pragma once

#include <pebble.h>

// One busy spinner shared by every screen. A window's state-change
// listener calls spinner_sync with its canvas so the animation timer
// runs while a request is in flight and repaints whichever screen is
// current; the canvas's update proc calls spinner_draw to render the
// arc. Detach before destroying the canvas so the timer never marks a
// dead layer.
void spinner_sync(Layer *canvas);
void spinner_detach(Layer *canvas);
void spinner_draw(GContext *ctx, GRect box);
