# IDEAS.md

Polish / embellishment candidates to consider before (or during) phase 3.
Nothing here is committed work — this is a parking lot. Anything that
actually lands should move into `DESIGN.md`'s phased plan.

## Warning: bloat risk

Polish bloats fast. Every font variant, every image resource, every
secondary window eats into the basalt platform's 24 KB heap and the
256 KB resource budget, and slows build + iteration loops. Before
picking anything off this list:

- Sanity-check the memory report (`pebble build` footer) after each
  change. If free heap drops below ~40 KB on basalt, stop and reassess.
- Prefer drawing primitives over bitmap resources where it costs one
  update proc instead of a PNG atlas.
- Don't chase features the user hasn't asked to see; pick the one or
  two that solve a real glance-problem and leave the rest.
- A feature that only works on color platforms (chalk / basalt / emery)
  needs a `#ifdef PBL_COLOR` fallback that still makes sense on
  diorite.

## Main screen

- **Loading spinner (rotating arc)** replacing the current `...` glyph,
  so slow refreshes don't look frozen. One animation timer, no assets.
- **Battery colour ramp** at 10 / 20 / 50 % thresholds instead of the
  current two-step red/blue. Cheap — only the colour constants change.
- **Live-ticking "ago" text.** Today the status line only redraws when
  data changes, so "2m ago" can sit stale for minutes. Subscribe to a
  `tick_timer_service` minute tick and re-mark the canvas dirty. Cost:
  a handful of extra redraws per minute.
- **Per-vehicle icon** (car / van silhouette picked from a body-type
  field returned by the proxy). Needs a small bitmap atlas and a
  proxy-side body_type field in the vehicle list. Starts cheap if it's
  2–3 SVG-traced icons; gets expensive fast if it sprawls.
- **Charge rate animation** — small lightning bolt next to the kW
  reading that pulses while `IS_CHARGING`. Drawing-primitive only, no
  new resources. Subtle enough not to be distracting on a glance.

## Integration points

- **App glance** — Pebble's locker card can show a short line (e.g.
  "72 %   176 mi") without opening the app. The glance slice is set
  from the companion on each status response, so most of the plumbing
  already exists.
- **Watchface variant.** Ship a read-only watchface that shows SoC and
  range using the same companion → proxy pipeline, for users who want
  always-on visibility. Non-trivial: a watchface has its own UUID,
  different lifecycle, and its own JS companion slot — arguably a
  separate project that shares the proxy.
- **Timeline pins** for `CHARGE_ETA_MIN` — when charging starts with a
  known ETA, push a pin via the Rebble timeline service so the watch
  shows "charge complete ~ 14:30" in its calendar view. Requires the
  companion to have timeline tokens; Rebble docs cover the API.
- **Background / push notifications.** Today's notifications only fire
  while the watchapp is open because PebbleKit JS can't run in the
  background. To get "charge complete" alerts with the app closed, the
  proxy needs to push — either via Rebble timeline pins (same
  infrastructure as the bullet above) or a mobile-app-level webhook.
  Natural follow-up once the live source lands in phase 3.

## Configuration

- **Units toggle in Clay** — exposes `PBK_USE_MILES` as a user setting
  instead of a compile-time macro. Deferred per current DESIGN.md
  "Display units" section; trivial once the Clay schema grows a
  checkbox.
- **Refresh-interval preference** — Clay field for how often the watch
  auto-polls on top of user-triggered refreshes. Would need a timer on
  the watch and coordination with the proxy's rate limit so the client
  doesn't just get cached responses forever.

## Reliability / UX

- **Persist last-known state to watch storage.** Currently the watch
  shows "Connecting…" on every launch until the companion replies.
  Writing the current `Vehicle` array into `persist_*` at notify time
  and restoring it in `app_state_init` would give an instant first
  paint with stale-but-useful data. Already on the phased-plan list as
  part of later UX polish.
- **Vibrate on errors that change state** (e.g. proxy goes from OK to
  `Can't reach proxy` on a refresh). Avoids silent failure — user
  notices the missed update without staring at the watch.
