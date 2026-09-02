# IDEAS.md

Polish / embellishment candidates. Nothing here is committed work —
this is a parking lot. Anything that actually lands should move into
`DESIGN.md`'s phased plan.

## Warning: bloat risk

Polish bloats fast. Every font variant, every image resource, every
secondary window eats into the app memory budget and slows build +
iteration loops. Emery has 128 KB and is the primary target, but basalt
and diorite have half that, so they set the ceiling. Before picking
anything off this list:

- Sanity-check the memory report (`pebble build` footer) after each
  change — it prints the real per-platform figure, which is worth
  trusting over any number written down here. If free heap on basalt
  drops below a quarter of the total, stop and reassess.
- Prefer drawing primitives over bitmap resources where it costs one
  update proc instead of a PNG atlas.
- Don't chase features the user hasn't asked to see; pick the one or
  two that solve a real glance-problem and leave the rest.
- A feature that only works on color platforms (chalk / basalt / emery)
  needs a `#ifdef PBL_COLOR` fallback that still makes sense on
  diorite.

## Main screen

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
  companion to have timeline tokens; Rebble docs cover the API. (The
  proxy no longer watches for transitions, so the companion would have
  to notice the ETA itself while the app is open.)

## Configuration

- **Refresh-interval preference** — Clay field for how often the
  companion polls while the app is open (hard-coded at 15s today), for
  anyone who wants a slower or faster wrist update than the default.
- **Charging cadence from the phone** — a Clay field for the
  charging-refresh window, so it can be tuned without editing the
  proxy's `.env`. Would mean the companion sending a hint the proxy
  honours per request rather than a proxy-wide setting — which would
  also confine the charging wakes to the watch instead of every
  polling client.

## Reliability / UX

Both entries that lived here — persisting last-known state to watch
storage, and vibrating on an error that changes state — shipped in phase
5. Nothing outstanding.
