# CLAUDE.md

Context for future Claude sessions picking up this repo.

## What this is

A personal Pebble smartwatch app (C + PebbleKit JS) for viewing Kia
vehicle stats, backed by a self-hosted FastAPI proxy that wraps the
community `hyundai_kia_connect_api` library. Single user (the repo
owner), open source so others can fork, but not run as a hosted
service.

For architecture and the phased plan, read `DESIGN.md` before changing
anything. The proxy-vs-direct-mode decision is recorded there and was
deliberate — don't re-litigate it without being asked.

## Current phase

All eight phases done: the `live` source talks to the owner's real Kia
account via `hyundai_kia_connect_api`, with SQLite persistence for the
refresh token and last-known state. `DATA_SOURCE=live` in `proxy/.env`
switches it on; `demo` remains for offline iteration and still drives
the scenario replayer. Phase 6 added touch controls on emery and the
launcher menu icon (`pebble/resources/`). Phase 7 added opt-in remote
commands (`ENABLE_COMMANDS`, watch actions menu) and a scrollable
detail screen. Phase 8 (2026-09-02) reworked the refresh model at the
owner's request — no force floor, wakes coalesce, charging sessions
refresh on their own, launch wakes the car — and removed the ntfy
notification pipeline because the official Kia app already covers it.
DESIGN.md "Refresh model" and "Notifications" are the decision
records; don't quietly reintroduce either.

The owner's watch is now a **Pebble Time 2 (`emery`)**, which has a
larger display than the Pebble Time the UI was originally laid out for,
plus a touchscreen — the only target with one. Emery is the primary
target; basalt, diorite and chalk still build.

Status table at the top of `README.md` reflects current state; update it
as phases land.

## Repo layout

- `DESIGN.md` — architecture, operating assumptions, phased plan,
  decisions. Authoritative.
- `README.md` — user-facing quickstart (build, emulator, sideload).
- `pebble/` — watchapp. `src/c/` is the C source, `src/pkjs/index.js`
  is the companion stub.
- `proxy/` — FastAPI service. `app/sources/` holds the data-source
  layer; `demo-data.json` is the editable sample payload. Dockerfile +
  `docker-compose.yml` + `Caddyfile.example` cover deployment behind
  the user's existing Raspberry Pi Caddy (automatic TLS via Let's
  Encrypt).

## Working on the proxy

Python 3.13 managed by `uv`. Iterate locally with:

```sh
cd proxy
cp .env.example .env   # fill in PROXY_BEARER_TOKEN (+ KIA_* for live)
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
```

`.env` holds the owner's real Kia password. It is gitignored — never
read it back into a transcript or echo it in command output.

Guardrails:

- Mutating endpoints exist now, at the owner's explicit request, behind
  `ENABLE_COMMANDS` (default off): `POST /vehicles/{id}/actions/{action}`.
  Never trigger a command from a timer or anything but an explicit
  watch request. The action list must stay in sync across proxy,
  companion and watch, and risky actions (unlock, stop charge, valet)
  keep their confirm step on the watch. Threat model: DESIGN.md
  "Remote commands".
- The `demo` and `live` sources must expose exactly the same shape.
  When adding a field, extend `app/models.py`, update the demo JSON,
  and leave a clear TODO on the live side if it's not yet
  implementable.
- Cache policy (TTL, in-flight coalescing, the charging upgrade) lives
  in `app/cache.py`, not in individual sources. `demo` uses the same
  cache so behaviour is testable without a car.
- Two kinds of upstream read, and the distinction is load-bearing. An
  ordinary read takes the state Kia already holds and leaves the car
  asleep; `LIVE_REFRESH_MIN_SECONDS` is how long the proxy's copy
  answers for it. A *forced* read wakes the telematics unit. There is
  **no floor** on forced reads — the owner removed it in phase 8;
  every pull, long-press and launch wakes the car. What keeps wakes
  bounded is coalescing: `StatusCache` runs at most one wake per
  vehicle at a time, and a request arriving during a wake — or an
  ordinary request arriving during any fetch — waits for it and shares
  the answer. A force arriving during an *ordinary* fetch is the
  exception: it starts its own and takes the in-flight slot, so later
  arrivals join the wake. That is also why the store keeps the
  latest-*started* result rather than the last to land. The only
  unprompted wake is the charging upgrade: an ordinary read arriving
  `LIVE_CHARGING_REFRESH_SECONDS` after the last wake, while the
  cached status says charging, becomes a wake. Paced from the last
  wake, not the entry's age — entry age made a short
  `LIVE_REFRESH_MIN_SECONDS` starve the upgrade completely. Still
  client-driven, and *any* polling client drives it — never add a timer
  that reads or wakes the car.
- A CCS2 wake takes ~30 s: the library's
  `_force_refresh_vehicle_state_ccs2` triggers the wake, `sleep(25)`,
  then reads `/latest`. The companion's HTTP timeout is 60 s for that
  reason — at the old 15 s every real force reported a timeout on the
  watch. Don't lower it. The live source holds its lock for the whole
  wake, so actions and list calls queue behind one.
- `forced: true` means the proxy asked Kia to wake the car, not that
  the car answered: the library throws away the trigger's response and
  sleeps a fixed 25 s, so a car with no signal yields the old snapshot
  and no error. `live.py` logs a warning when a wake doesn't advance
  `last_updated_at`. The age line on the watch is the honest reading.
- `fresh=1` on GET status bypasses `LIVE_REFRESH_MIN_SECONDS` for one
  ordinary read — the companion sends it eight seconds after a remote
  command. It is the one read the charging upgrade must never touch:
  the read after `stop_charge` would otherwise wake the car to ask
  about the charge it just stopped. Keep the `not bypass_fresh` guard.
- The launch wake is watch-driven: `ipc.c` answers the first status
  reply of a run with a `refresh` request, unless that reply already
  carried `FORCED` (the proxy woke a charging car for it), in which
  case launch is already current and a second wake buys nothing. It
  lives there and not in the companion because the watchapp restarts
  per launch and the companion's JS session need not.
- There is no background poller and no notification path. The ntfy
  detector was removed in phase 8 (the Kia app's own notifications
  reach the watch through the Pebble app's bridge). Nothing in the
  proxy runs on a timer.
- Never let Kia credentials reach disk beyond `.env`: `store.save_token`
  strips `password` and `pin` before writing, and `tools/dump_vehicle.py`
  redacts VIN and coordinates.
- The dev machine is Arch (Omarchy) running real Docker from the
  `docker` package — not the `podman-docker` shim an earlier note here
  claimed. Prefer `pacman` for anything system-level. The Pi that
  actually hosts the proxy is a separate box.

## Installing the Pebble SDK

Pebble is a going concern again — Core Devices sells the hardware and
maintains the SDK, and the modern tool runs on Python 3, so none of the
old Python-2.7 contortions apply. Official docs: developer.repebble.com/sdk.

```sh
uv tool install pebble-tool --python 3.13   # 3.14 is not supported yet
pebble sdk install latest                   # ~240 MB, includes the ARM toolchain
pebble sdk list                             # confirm one is marked (active)
cd pebble && npm install                    # pebble-clay, needed for the JS bundle
pebble build                                # builds all four targetPlatforms
```

Verified on this Arch box with pebble-tool 5.0.40 and SDK 4.33.1. Every
emulator runtime dependency (sdl2, glib2, pixman, zlib, libpng, sndio)
was already installed; pacman was not needed at all.

## Testing end-to-end in the emulator

Interactive path: `pebble emu-app-config` opens the Clay config page in
the host browser. Fill in URL + token, Save, and the emulator's
localStorage picks up the values.

Scripted path (for headless testing — no browser): pypkjs stores
`localStorage` as a `dbm.dumb` database, one directory per platform, at
`~/.local/share/pebble-sdk/<sdk-version>/<platform>/localstorage/<uuid>`.
That is **not** the legacy `~/.pebble-sdk` path — the modern tool only
uses that if it already exists. The directory may not exist until that
platform has been run, so create it rather than globbing for it:

```sh
PLATFORM=emery
mkdir -p ~/.local/share/pebble-sdk/*/$PLATFORM/localstorage
python3 - "$PLATFORM" <<'PY'
import dbm.dumb, glob, json, os, pathlib, sys
plat = sys.argv[1]
uuid = '5b7e9a12-3c4d-4e8f-9a1b-2c3d4e5f6a7b'
ls = glob.glob(os.path.expanduser(
    '~/.local/share/pebble-sdk/*/' + plat + '/localstorage'))[0]
db = dbm.dumb.open(str(pathlib.Path(ls) / uuid), 'c')
db['clay-settings'] = json.dumps({
    'PROXY_URL': 'http://localhost:8000',
    'PROXY_TOKEN': 'testtoken123',
    'UNIT_MILES': True,
})
db.close()
PY
pebble install --emulator $PLATFORM   # reinstall to reload localStorage
pebble screenshot --emulator $PLATFORM --no-open shot.png
```

Start the proxy with a matching `PROXY_BEARER_TOKEN` and watch `pebble
logs --emulator $PLATFORM`. The companion prints `[kia] req …` lines and
errors surface as `ERR` top-right plus a readable message at the bottom
of the main screen.

Emulator button presses from the CLI:

```sh
pebble emu-button click select              # tap
pebble emu-button click select --duration 700   # long press (≥500ms)
pebble emu-button click down
```

`pebble emu-button --help` and `pebble emu-button` (no args) crash with a
Python-3.13 argparse regression (`ValueError: empty group ...`). The
command itself works; only the usage-printing path is broken. Pass a
valid `action` plus at least one button and it's fine. Valid actions are
`click`, `push` (hold), `release`.

What the buttons mean depends on the screen (phase 7 changed this):
Select on main opens detail, Select on detail opens the actions menu,
Up/Down switch vehicle on main but scroll on detail — vehicle switching
is main-screen only.

Emery's touch gestures (drag down, swipes) CAN be exercised headlessly,
but not via `pebble emu-button` — the path is the emulator's VNC server:

```sh
# from pebble/, like every other pebble command in this file
pebble install --emulator emery --vnc
python3 tools/vnc_touch.py 165 110 35 110 90    # swipe left
python3 tools/vnc_touch.py 100 50 100 170 300   # drag down
```

Gotchas that cost real debugging time, do not rediscover them:

- **Every pebble command in a `--vnc` session must also pass `--vnc`**
  (`logs`, `screenshot`, `emu-button`, …). The emulator manager kills
  and respawns QEMU whenever the flag disagrees with the running
  instance, which silently quits the app under test.
- The QEMU monitor's `mouse_move` is useless here: it emits relative
  events and the `pebble-touch` device only accepts absolute + button,
  so buttons arrive and positions never do. VNC pointer events are the
  working injection path, and only after a `SetEncodings` message —
  QEMU flips a client to absolute-pointer mode when it negotiates
  encodings, which `tools/vnc_touch.py` handles.
- `pebble logs` holds one pypkjs websocket; a later `pebble install`
  kicks it off. Attach logs after installing.
- pypkjs keeps one JS session for the emulator's lifetime, so
  companion state that is per-launch on real hardware
  (`currentVehicleId`, the poll loop) survives reinstalls in the
  emulator. `pebble kill` is the only way to reset it. Every running
  emulator's companion keeps polling the proxy, so kill the others
  before reading request logs. The launch wake is unaffected: it is
  armed in the watchapp, which does restart on reinstall.
- The owner often has a proxy of their own running on this box — a
  `docker compose` one on port 8000 against the live account, and
  sometimes a dev instance on another port. Check `ss -ltnp` before
  starting one for an emulator run, use a free port, and never point
  a test at the live one.

## Working on the watchapp

The SDK is installed (see above), so **build before claiming anything
compiles**. `pebble build` covers all four platforms in about a minute,
and `pebble install --emulator emery` plus `pebble screenshot` shows what
it actually looks like. Known gotchas:

- Never call `window_destroy` inside a window's `unload` handler — it
  recurses. Pattern: destroy child layers in unload, destroy the window
  itself from a top-level `*_deinit` function called out of `main.c`.
- `graphics_draw_text` takes 7 args (ctx, text, font, rect, overflow,
  alignment, attributes).
- Color constants like `GColorIslamicGreen` only exist on color
  platforms — guard with `#ifdef PBL_COLOR`. `GColorBlack` / `GColorWhite`
  are safe everywhere.
- Target platforms in `package.json`: `basalt`, `chalk`, `diorite`,
  `emery`. The owner's daily driver is now a Pebble Time 2 (`emery`),
  so that is what the layout is tuned for — but the app still has to
  build and stay legible on the others, and basalt is the tight one for
  memory. Derive geometry from `layer_get_bounds()` rather than
  hard-coding pixel constants; that is what keeps all four working.
- `app_state_subscribe` dedupes by function pointer, so it's safe to
  call on each window load.
- Adding a key to `messageKeys` in `package.json` does **not**
  regenerate `build/include/message_keys.auto.h` on an incremental
  build — the new `MESSAGE_KEY_*` comes back undeclared. Run
  `pebble clean` first.
- Two flags now gate what the UI says about in-flight work, and they
  are not interchangeable. `app_state_is_busy()` covers any request
  and is held for the ~30 s of a wake, which is what the spinner
  wants. `app_state_action_pending()` covers a remote command only,
  which is what the actions menu's "Sending…" wants — the general flag
  would put it there during a launch wake with nothing sent. `ipc.c`
  also keeps busy set through an ordinary poll reply that lands mid
  wake (`FORCED` distinguishes them), with an app timer just past the
  companion's own timeout as the backstop against a spinner that never
  stops.
- Touch is a three-part contract, and all three parts are load-bearing:
  `app_touch_navigation_enable(true)` once at init (third-party apps
  receive NO touch input at all without it — recognizers just sit
  silent), `window_set_touch_bridge_disabled(window, true)` per canvas
  window (else the system set consumes gestures first), and recognizers
  attached in `window_load` (the window owns and destroys them on
  unload, so re-attaching each load is correct). The actions menu is
  the deliberate exception: it keeps the system touch bridge so native
  touch scroll/tap/swipe-back drive the MenuLayer, and attaches no
  recognizers of its own. The recognizer API is
  real only on emery; the other platforms stub it as `(0)` no-op macros
  that don't compile against struct-returning calls, hence the
  `#if PBL_API_EXISTS(window_attach_recognizer)` guards.
- Real Kia vehicle ids are 36-char UUIDs. `VEHICLE_ID_LEN` must stay
  large enough for one; the demo source's short ids (`pv5-demo`) hid a
  truncation bug that only appeared against the live account.
- Measured free heap after phase 8: emery 117 KB of 128 KB,
  basalt/diorite/chalk ~52 KB of 64 KB. Plenty of room — the old
  "24 KB" figure in these docs was aplite's, and aplite is not a
  target. Re-read the `pebble build` footer rather than trusting these
  numbers; they move with every screen.

## Git and commits

- Branch freely. The whole project is developed by Claude, and the
  owner is happy for new sessions to create their own branches rather
  than all piling onto one. Reasonable defaults:
  - Phase-scoped or topic-scoped branches (`claude/<short-topic>`) for
    anything non-trivial, so the history reads phase by phase.
  - Direct commits to `main` are fine for small fixes, docs, and
    status-table updates.
  - Leave whichever branch the harness starts you on as the active
    branch unless there's a reason to cut a new one.
- Commit messages so far: imperative subject line, blank line, body
  explaining the *why* (not the what). No trailers, no emojis. Match
  that style.
- **Push after committing.** On this project the owner has granted
  standing permission to `git push` whenever a commit lands, so the
  default "don't push unless asked" guardrail does not apply here.
  Push to whichever branch you're on (normally `main`). Never force
  push to `main` — if a push is rejected, investigate why.
- Do **not** open pull requests unless explicitly asked.

## Display units

The owner is in the UK, so range and odometer render in **miles** by
default; outside temp in °C, charge rate in kW. The canonical data stays in
km (matching what the Kia API returns) and conversion happens only at
render time. The miles/km choice is a runtime Clay toggle (`UNIT_MILES`);
`PBK_USE_MILES_DEFAULT` in `pebble/src/c/units.h` only seeds a fresh
install before that toggle has been seen. When adding a new distance
readout, route it through `format_distance_km()` rather than hard-coding
"km". `DESIGN.md` → "Display units" has the longer rationale.

## Style conventions already in play

- Comments only when the *why* is non-obvious. No docstrings on
  short functions, no narration of what the code does.
- No speculative abstractions, no backwards-compatibility shims, no
  fallback error handling for impossible cases.
- Markdown docs: plain prose, hard-wrap around 78 chars, no emoji, no
  badge soup.

## Things to not do without checking

- Add a second user / multi-tenant anything — explicitly out of scope.
- Grow the remote-command surface — new actions, new triggers, or any
  path around `ENABLE_COMMANDS`. The ten watch-initiated actions are
  the agreed scope; see the proxy guardrails and DESIGN.md "Remote
  commands".
- Reintroduce a floor on forced reads, a background poller, or a
  notification pipeline. All three were removed deliberately in phase
  8; DESIGN.md "Refresh model" and "Notifications" say why.
- Replace the proxy with direct phone-to-Kia mode. The decision is in
  `DESIGN.md`; the proxy is reused by Home Assistant and a planned
  dashboard, so it earns its keep beyond the watchapp.
- Introduce CI, pre-commit hooks, or linters without asking — none
  exist and the project may be too small to need them. (pytest *does*
  exist now, under `proxy/tests/`, in the `dev` dependency group. Add
  tests there freely; don't add a second framework.)

## When picking up next

1. Read `DESIGN.md` "Phased plan" to find the next phase.
2. Ask the user to confirm before starting a new phase — they may
   want to iterate on the current one first.
3. Update the status table in `README.md` and the phased-plan checklist
   in `DESIGN.md` as you complete work.
