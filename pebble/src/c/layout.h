#pragma once

#include <pebble.h>

// Emery (Pebble Time 2) is 200x228 at 202 ppi against basalt's 144x168
// at 182, so the same font keys render physically smaller there while
// leaving a third of the screen empty. System font keys are identical
// on every platform — the system only enlarges its own UI — so the big
// screen gets its own set of sizes.
#if defined(PBL_PLATFORM_EMERY)

#define LAYOUT_FONT_TITLE FONT_KEY_GOTHIC_24_BOLD
#define LAYOUT_H_TITLE 28
// Largest numeric system font there is; LECO tops out at 42.
#define LAYOUT_FONT_SOC FONT_KEY_ROBOTO_BOLD_SUBSET_49
#define LAYOUT_H_SOC 54
#define LAYOUT_FONT_PCT FONT_KEY_GOTHIC_28_BOLD
#define LAYOUT_H_PCT 32
#define LAYOUT_W_PCT 26
#define LAYOUT_H_BAR 14
#define LAYOUT_FONT_VALUE FONT_KEY_GOTHIC_28_BOLD
#define LAYOUT_H_VALUE 32
#define LAYOUT_FONT_BODY FONT_KEY_GOTHIC_24
#define LAYOUT_FONT_STATUS FONT_KEY_GOTHIC_18_BOLD
#define LAYOUT_H_STATUS 22
#define LAYOUT_FONT_IND FONT_KEY_GOTHIC_18
#define LAYOUT_H_IND 20
#define LAYOUT_W_IND 44
#define LAYOUT_D_SPINNER 18
#define LAYOUT_FONT_ROW_LABEL FONT_KEY_GOTHIC_18
#define LAYOUT_FONT_ROW_VALUE FONT_KEY_GOTHIC_24_BOLD
#define LAYOUT_GAP 6

#else

#define LAYOUT_FONT_TITLE FONT_KEY_GOTHIC_18_BOLD
#define LAYOUT_H_TITLE 22
#define LAYOUT_FONT_SOC FONT_KEY_LECO_42_NUMBERS
#define LAYOUT_H_SOC 48
#define LAYOUT_FONT_PCT FONT_KEY_GOTHIC_24_BOLD
#define LAYOUT_H_PCT 28
#define LAYOUT_W_PCT 22
#define LAYOUT_H_BAR 10
#define LAYOUT_FONT_VALUE FONT_KEY_GOTHIC_24_BOLD
#define LAYOUT_H_VALUE 26
#define LAYOUT_FONT_BODY FONT_KEY_GOTHIC_18
#define LAYOUT_FONT_STATUS FONT_KEY_GOTHIC_14_BOLD
#define LAYOUT_H_STATUS 18
#define LAYOUT_FONT_IND FONT_KEY_GOTHIC_14
#define LAYOUT_H_IND 16
#define LAYOUT_W_IND 36
#define LAYOUT_D_SPINNER 14
#define LAYOUT_FONT_ROW_LABEL FONT_KEY_GOTHIC_14
#define LAYOUT_FONT_ROW_VALUE FONT_KEY_GOTHIC_18_BOLD
#define LAYOUT_GAP 4

#endif

// Detail rows are a fixed height and the row region scrolls, so the
// height is chosen for the value font's legibility rather than for how
// many rows fit — the padding decides where the scrolling viewport
// starts and ends.
#if defined(PBL_ROUND)
#define LAYOUT_PAD_V 16
#define LAYOUT_PAD_H 4
#define LAYOUT_H_ROW 20
#elif defined(PBL_PLATFORM_EMERY)
#define LAYOUT_PAD_V 5
#define LAYOUT_PAD_H 8
#define LAYOUT_H_ROW 30
#else
#define LAYOUT_PAD_V 2
#define LAYOUT_PAD_H 4
#define LAYOUT_H_ROW 22
#endif

// Horizontal band of the screen at [y, y + h), padded and — on a round
// display — narrowed to the chord that keeps both of its ends on
// screen. Everything on both screens is positioned through this, so
// the layout follows the actual display size rather than basalt's.
GRect layout_row(GRect bounds, int16_t y, int16_t h);
