# Pebble Kia Watch App — Design

Status: all seven phases built. Live Kia data flows watch -> companion ->
proxy -> Kia Connect, and watch-initiated remote commands flow the same
path back to the car when the proxy opts in (`ENABLE_COMMANDS`).

Goal: view live stats for a Kia PV5 Passenger (and future vehicles on the same
account) from a Pebble smartwatch — state of charge, estimated range, charging
status, doors/locks, odometer, outside temperature, last-known location.
(The PV5 reports no measured cabin temperature — see "Display units".)

## Operating assumptions

This is a **single-user, self-hosted** project. Scope is intentionally
narrow:

- **One operator (the author).** Only one Kia account, one phone, one or two
  watches. The proxy is not multi-tenant; auth is a single shared bearer
  token, not per-user login.
- **Open source, not a service.** The code is public so others can fork and
  self-host for their own vehicles, but we do **not** run a hosted service
  for anyone else to connect their Kia account to. That eliminates a large
  class of concerns (GDPR data-controller obligations, abuse monitoring,
  account isolation, SLAs, Kia ToS exposure from third-party data handling).
- **Existing home infra is the deployment target.** A Raspberry Pi already
  runs Docker with a Caddy reverse proxy doing automatic TLS via Let's
  Encrypt. The proxy ships as a Docker image and slots in behind Caddy with
  a single `docker compose` service and a Caddyfile entry. No new hosting
  cost, no new TLS plumbing.
- **The proxy earns its keep beyond the watch.** Because Home Assistant and
  a future custom web dashboard will also consume the same vehicle data,
  the proxy is not overhead solely for the Pebble app — it's the shared
  backend for all Kia-data clients in the house. This tips the build-vs-buy
  argument decisively toward building the proxy even though a direct
  phone-to-Kia approach is technically feasible (see "Direct mode" below).

## Architecture

```
┌──────────────┐  BT / AppMessage   ┌──────────────────┐   HTTPS   ┌──────────────┐   HTTPS    ┌────────────────┐
│ Pebble watch │ ◀────────────────▶ │ Pebble mobile    │ ◀───────▶ │ Self-hosted  │ ◀────────▶ │ Kia Connect    │
│ (C watchapp) │                    │ app + PebbleKit  │           │ proxy (EU)   │            │ EU servers     │
└──────────────┘                    │ JS companion     │           │              │            │ (unofficial)   │
                                    └──────────────────┘           └──────────────┘            └────────────────┘
```

Three tiers, each with a specific job:

1. **Pebble watchapp (C).** Pure UI. Renders cached stats, requests refreshes
   on user action, draws a battery gauge / status glyphs. No networking — talks
   only to the JS companion via AppMessage.

2. **PebbleKit JS companion.** Runs inside the official Pebble mobile app.
   Receives AppMessage requests from the watch, forwards them to the proxy over
   HTTPS with a shared secret, pushes results back. Holds no Kia credentials.

3. **Self-hosted proxy (Python + FastAPI).** Wraps `hyundai_kia_connect_api`.
   Owns the Kia session, caches vehicle state, rate-limits refreshes to protect
   the 12V battery, exposes a tiny JSON API for the companion. Deployed to a
   small VPS or home server in the EU region.

### Why three tiers

- Pebble has tight memory/CPU limits and no direct HTTPS; pushing network work
  to the phone (PebbleKit JS) or a server is idiomatic.
- Kia credentials and refresh tokens must never live on the watch or in the
  companion JS sandbox. The proxy keeps the auth blast radius small and lets
  token rotation happen without touching the phone or watch builds.
- The proxy is also the right place to absorb API shape changes (Kia EU
  periodically reshapes responses) without republishing the watchapp.
- The proxy is a **shared backend** for this watchapp, Home Assistant, and a
  planned custom dashboard. Doing the auth and caching work once in one place
  is cheaper than reimplementing it in each client.

### Alternative considered: direct mode (phone → Kia, no proxy)

It is technically possible for the PebbleKit JS companion to call the Kia
EU API itself: do the one-time login inside a Clay configuration WebView
(which is a full browser and can handle the SSO redirect chain), stash the
refresh token in `localStorage`, and have the companion poll Kia directly
thereafter.

**Pros:** no server to run; one less hop; works even when the Pi is down.

**Cons, specific to this project:**

- Porting `hyundai_kia_connect_api`'s auth logic (Python) into the
  constrained PebbleKit JS sandbox is substantial work — crypto gaps
  (WebCrypto coverage is spotty), no DOM for fallback flows, and every
  time Kia changes the EU login the watchapp has to be rebuilt and
  reinstalled.
- Kia credentials/refresh token live unencrypted in `localStorage`.
- No central cache — Home Assistant and the dashboard would each have to
  reimplement auth, duplicating effort and multiplying 12V-drain risk on
  the vehicle.
- No background execution. PebbleKit JS only runs when the watch pokes
  it, so "notify me when charging completes" type features need a server
  anyway.

Given the Pi + Caddy infra is already there and the proxy is reused by
other clients, direct mode is explicitly **rejected for this project**.
It's documented here so a forker without home-server infra can make an
informed choice to strip the proxy out.

## Components

### Proxy (`proxy/`)

- Python 3.13, FastAPI, `hyundai_kia_connect_api` as a dependency.
- Endpoints:
  - `GET /vehicles` — list vehicles on the account (id, VIN, nickname, model).
  - `GET /vehicles/{id}/status` — cached state. `?fresh=1` skips the
    freshness window (`LIVE_REFRESH_MIN_SECONDS`) for one ordinary
    read — the state Kia's servers already hold, never a wake; the
    companion sends it on its first status request each session so
    launch always shows current data. `?force=1` wakes the vehicle
    (rate-limited, default ≥ 15 minutes between wakes). When both are
    sent, force wins.
  - `POST /vehicles/{id}/refresh` — explicit refresh, same rate limit.
  - `POST /vehicles/{id}/actions/{action}` — remote command, gated
    behind `ENABLE_COMMANDS` — see "Remote commands" below.
- Auth: single shared bearer token in an env var; the companion sends it on
  every call. No per-user login — this is a single-user system.
- Persistence: SQLite file for the refresh token and last-known vehicle state.
  State survives restarts so the watch sees data immediately on boot.
- Deployment: Docker image on the existing Raspberry Pi. Caddy (already
  running on the Pi with automatic Let's Encrypt TLS) is extended with a
  new site block that reverse-proxies a subdomain to the container. No new
  TLS machinery, no new host.
- Other clients: the same HTTP API is consumed by Home Assistant (via a
  custom sensor / REST integration) and by a future internal web dashboard.
  Those clients use their own bearer tokens with the same shared-secret
  scheme; we can upgrade to per-client tokens later if it ever matters.

#### Data-source abstraction

The proxy has a `DataSource` protocol (`proxy/app/sources/base.py`) with
two implementations selected by the `DATA_SOURCE` env var:

- `demo` — reads `DEMO_DATA_FILE` on every fetch. The file can be a
  static snapshot (hand-edited to freeze the UI at a particular state)
  or a time-evolving scenario (baseline + list of events, each with an
  `at_s` offset and a `patch` of status overrides). Scenario mode
  loops on `loop_seconds` so a demo never ends; see
  `proxy/scenarios/*.json` for shipped examples (rapid charge,
  AC charge, daily drive, preconditioning).
- `live` — calls `hyundai_kia_connect_api` against the owner's Kia
  account. Normalises the library's `Vehicle` to the same
  `VehicleStatus` the demo source emits, converting to km/°C at the
  boundary so the wire contract stays metric.

Both sources sit behind the same cache layer. Cache TTL is source-
specific: `LIVE_REFRESH_MIN_SECONDS` (default 600) protects the 12V
battery on live; `DEMO_REFRESH_MIN_SECONDS` (default 5) keeps scenario
progression visible to polling clients without a long-press refresh
every tick. Clients (watch, HA, dashboard) are unaware of which source
is serving them.

There are two distinct kinds of upstream read, and the difference
matters for the 12V battery:

- An ordinary read asks Kia for the state the vehicle last reported.
  It costs an API call and nothing else — the car stays asleep. This is
  what the watch's 15s poll and the detector both do, and
  `LIVE_REFRESH_MIN_SECONDS` bounds how often it reaches Kia. The
  launch-time `fresh=1` read is still an ordinary read — it skips that
  freshness window for one call and leaves the force floor untouched.
- A forced read (`?force=1`, `POST /refresh`, the watch's long-press)
  wakes the telematics unit for genuinely current data. That draws on
  the 12V battery, so it has its own harder floor,
  `LIVE_FORCE_MIN_SECONDS` (default 900). A forced read inside that
  window is **downgraded, not refused**: the client gets cached data
  and `forced: false` in the response, so a impatient long-press costs
  nothing and never surfaces an error. `forced` is the only way to tell
  a real wake from a downgrade from outside.

#### Remote commands

Commands were out of scope until the owner explicitly asked for them —
exactly the trigger the original scope note anticipated. They are
deliberately narrow and off by default.

- `POST /vehicles/{id}/actions/{action}`, ten actions: `lock`,
  `unlock`, `start_charge`, `stop_charge`, `start_climate`,
  `stop_climate`, `open_charge_port`, `close_charge_port`,
  `start_valet`, `stop_valet`. Hazard lights are deliberately absent:
  `hyundai_kia_connect_api` raises not-implemented for them in the EU
  region.
- Off unless `ENABLE_COMMANDS=1`. The bearer token has only ever
  granted reads, so its blast radius was "someone can watch the car's
  state". A leaked token must not silently gain unlock; turning
  mutation on is a deliberate operator decision on the proxy side,
  never a default.
- `COMMAND_MIN_SECONDS` floors the interval between commands. Unlike
  a downgraded force, a command inside the window is refused with a
  429 — silently dropping a lock request would be worse than an error.
- Watch-initiated only. No proxy code path — not the detector, not any
  timer — ever sends a command on its own; every command traces back
  to a press or tap on the wrist.
- Risky actions (unlock, stop charge, valet) confirm on the watch
  before the request is sent.

### Kia authentication

- EU login is plain username/password (`KIA_USERNAME` / `KIA_PASSWORD`).
  `hyundai_kia_connect_api` performs the OAuth2 exchange and the RSA
  password encryption internally.
- This supersedes the one-time Selenium/reCAPTCHA bootstrap this document
  originally budgeted for. That was necessary before library v4.12.0;
  it no longer is, and `proxy/bootstrap/` was never built as a result.
- The resulting refresh token is persisted to the proxy's SQLite store so
  a restart doesn't re-login. The password and PIN are stripped before
  the token is written — they are re-injected from settings at login
  time and have no reason to sit on disk.

### Notifications

Notifications are driven from the proxy, not the companion. An
asyncio background task (`proxy/app/detector.py`) polls each vehicle
on `DETECTOR_INTERVAL_SECONDS`, diffs against the last observation,
and pushes to ntfy on meaningful transitions (charge start/end,
plug/unplug, lock/unlock, climate on/off). The phone runs the ntfy
client app subscribed to the configured topic, the phone shows the
standard OS notification, and the Pebble mobile app bridges that OS
notification to the watch — so pushes reach the wrist whether the
Kia app is open, closed, or not even installed in the watch locker.

Why this layout rather than Rebble timeline pins or watch-scheduled
wakeups:

- **Works without the watchapp running.** The OS-notification bridge
  is Pebble's normal path; we just feed it the right notification via
  the same mechanism SMS or Slack use.
- **Phone gets the notification too**, which Rebble timeline pins
  don't do — useful when the watch isn't on the wrist.
- **Self-hostable.** ntfy runs in the same compose stack as the
  proxy, behind the same Caddy. No third-party push service, no
  per-vendor push keys (APNS/FCM), no risk of a service going away
  (the feared Pushbullet scenario).
- **Testable** without Rebble account / OAuth dance.

Deliberate scope limits:

- **Detection resolution equals the poll interval.** A transition that
  starts and ends inside one interval is missed. For vehicle events
  this is academic — nothing toggles that fast.
- **First observation for a vehicle id is silent.** No "charging
  started" spam when the proxy restarts mid-session.
- **The transition layer is stateless across restarts.** If the proxy
  crashes mid-session and comes back up, it re-establishes baseline
  silently and reports transitions forward from there. Phase 3+ can
  persist last-known state to sqlite if it matters.

### PebbleKit JS companion (`pebble/src/pkjs/`)

- Thin translator. Receives AppMessage keys from the watch, calls proxy,
  formats the response into AppMessage dictionary values, sends back.
- Configuration page (Clay, the appstore standard) to set proxy URL + bearer
  token; stored in `localStorage`.
- No Kia credentials — users never enter their Kia password on the phone.

### Watchapp (`pebble/src/c/`)

- Written in C against the Pebble SDK (Core Devices 4.33.1).
- Screens:
  - **Main** — big centred SoC percentage with a charging/idle bolt
    (filled while charging, outline while idle, so the state survives
    the 1-bit platforms), battery bar, range, plug and lock status,
    last-updated timestamp.
  - **Detail** — scrollable: door/lock state plus a summary of open
    doors, windows, trunk, hood and sunroof, outside temp, 12V SoC,
    AC/DC charge limits, charge rate (kW) and estimated
    charge-complete time while charging, efficiency, battery
    temperature, odometer. Shows the same in-flight spinner as main.
  - **Actions** — a menu of the ten remote commands (see "Remote
    commands"), opened from the detail screen. Risky entries confirm
    before sending.
- Controls, buttons (all platforms, and the only controls on basalt,
  diorite and chalk): Select = open detail on main, open the actions
  menu on detail; long-press Select (≥500ms) = force refresh with a
  short vibe; Up/Down = switch vehicle on main (a no-op unless the
  account has more than one) and scroll on detail — vehicle switching
  lives on main only; Back = return/exit.
- Controls, touch (emery only — the Pebble Time 2 is the only target
  with a touchscreen; recognizers are attached per window): drag down
  on main or detail = force refresh, swipe left on main = open detail,
  swipe left on detail = open the actions menu, swipe right on detail
  = back to main, swipe right on main = quit. Touch delivery is
  opt-in: `app_touch_navigation_enable(true)` at app init is required
  before a third-party app receives any touch input. The two canvas
  screens (main, detail) disable the system touch bridge so gestures
  reach the app's own recognizers instead of the built-in button
  emulation; the actions menu keeps the bridge, so the system's
  native touch scroll/tap/swipe-back drives it with no recognizers of
  its own.
- Persistent storage: last known state per vehicle, so the watch shows data
  instantly before the first fetch completes.

## Data model

AppMessage dictionary (watch ↔ JS), kept small to fit Pebble's outbox
size. Request and response keys share the same dictionary — the
`REQ_KIND` / `RESP_KIND` discriminator tells each side which fields are
meaningful.

Watch → companion (requests):

| Key         | Type   | Notes                                               |
|-------------|--------|-----------------------------------------------------|
| `REQ_KIND`  | string | `list` \| `status` \| `refresh` \| `action`         |
| `REQ_ID`    | string | Vehicle id (status/refresh/action)                  |
| `ACTION`    | string | Action name (action requests; see "Remote commands") |

Companion → watch (responses):

| Key             | Type    | Notes                                                |
|-----------------|---------|------------------------------------------------------|
| `RESP_KIND`     | string  | `ready` (startup nudge) \| `list` \| `status` \| `action_ok` \| `error` |
| `VEHICLE_COUNT` | uint8   | list response                                        |
| `VEHICLE_ID[N]` | string  | list response (N slots, `MAX_VEHICLES` = 4)          |
| `VEHICLE_NICK[N]`| string | list response (display name)                        |
| `STATUS_ID`     | string  | status response (which vehicle this is for)          |
| `SOC_PCT`       | uint8   | 0–100                                                |
| `RANGE_KM`      | uint16  | Estimated range (km — wire format stays metric)      |
| `IS_CHARGING`   | bool    |                                                      |
| `CHARGE_KW_X10` | uint16  | Charge rate × 10 to carry 1 decimal without floats   |
| `CHARGE_ETA_MIN`| uint16  | Minutes to target SoC                                |
| `CHARGE_LIM_AC` | uint8   | AC charge limit, percent                             |
| `CHARGE_LIM_DC` | uint8   | DC charge limit, percent                             |
| `PLUG`          | uint8   | 0=unplugged, 1=AC, 2=DC                              |
| `DOORS_LOCKED`  | bool    |                                                      |
| `DOORS_OPEN`    | uint8   | Count of open doors, 0–4                             |
| `WINDOWS_OPEN`  | uint8   | Count of open windows, 0–4                           |
| `TRUNK_OPEN`    | bool    |                                                      |
| `HOOD_OPEN`     | bool    |                                                      |
| `SUNROOF_OPEN`  | bool    |                                                      |
| `OUTSIDE_TEMP_C`| int8    | Ambient; the PV5 reports no cabin reading            |
| `BATT_TEMP_C`   | int8    | Traction-battery temperature                         |
| `EFF_KMPKWH_X10`| uint16  | Efficiency (km/kWh) × 10 — metric on the wire, like every distance |
| `ODO_KM`        | uint32  |                                                      |
| `AUX_BATTERY_PCT`| uint8  | 12V auxiliary battery, 0–100; 0 means not reported   |
| `IS_CLIMATE_ON` | bool    | Maps to `air_control_is_on` upstream                 |
| `UPDATED_AT`    | uint32  | Unix epoch seconds; 0 means "never"                  |
| `UNIT_MILES`    | bool    | Clay units toggle; rides every list/status response  |
| `ACTION`        | string  | `action_ok` response — echoes the command that ran   |
| `ERROR_MSG`     | string  | Populated on failure; watch surfaces it in the UI    |

Startup race: the companion emits `RESP_KIND=ready` when it comes
online, and the watch kicks off the initial `list` request in response
to any inbox message. This avoids the window where the watch would
otherwise send before pypkjs (or the mobile app's JS runtime) has
attached.

All distances stay in km end-to-end (Kia → proxy → companion → watch). Unit
conversion is a display-only concern on the watch (see "Display units"
below), so the wire format and proxy cache remain source-of-truth and a
second client (Home Assistant, dashboard) can pick its own presentation.

## Display units

UK deployment: the watch renders range and odometer in miles by default,
Celsius for outside temp, kW for charge rate. The owner drives in the UK and
Kia's head unit shows miles, so the watch matches.

Implementation: the miles/km choice is a Clay toggle on the settings
page (`UNIT_MILES`), sent by the companion with every list and status
response, applied at runtime and persisted alongside the vehicle data so
an offline launch restores it. `pebble/src/c/units.h` defines
`PBK_USE_MILES_DEFAULT` (1 — UK deployment), which only decides what a
fresh install shows before the toggle has been seen once, plus the
`format_distance_km()` helper every distance readout flows through.
The watch is fixed-point, so the helper uses integer math
(km × 1000 / 1609, rounded) rather than floats.

### Cabin temperature is not available

The PV5 reports no measured cabin temperature. The only cabin figure in
the CCS2 payload is `Cabin.HVAC.Row1.Driver.Temperature`, which is the
climate *setpoint*, and the library discards it when it reads `OFF` —
i.e. whenever climate is off. The watch shows
`Cabin.HVAC.OutsideTemperature` instead, which is always populated and
is what `outside_temp_c` carries. If a future model does report a cabin
reading, it wants a new field rather than a redefinition of this one.

## Phased plan

Each phase ends with something runnable and committable. Work so far has
run slightly out of the original ordering (watchapp with compiled demo
data came first because it unblocks UI iteration without waiting on any
server); the list below reflects the path actually taken.

1. **Watchapp with compiled demo data.** C scaffolding, package.json,
   on-device dummy data module, emulator build working. **Done.**
2. **Proxy skeleton + end-to-end wiring against demo.** FastAPI app with
   bearer auth, in-memory cache with rate limit, pluggable data-source
   layer (`demo` reads `demo-data.json`; `live` is a 501 stub).
   Dockerfile + compose + Caddyfile snippet. PebbleKit JS companion
   with a Clay configuration page (proxy URL + token) that calls the
   proxy. Watch fetches the vehicle list and per-vehicle status over
   AppMessage — no compiled fallback; loading and error states rendered
   in the UI. **Done.**
3. **Proxy wired to Kia.** `live` source on top of
   `hyundai_kia_connect_api`, SQLite persistence for the refresh token
   and last-known state, a hard floor on car-waking refreshes, and the
   transition detector routed through the shared cache so one interval
   is one upstream call. PV5 field mappings confirmed against a real
   payload dump (`proxy/tools/dump_vehicle.py`) rather than guessed —
   this absorbed what was originally a separate phase 5. **Done.**
4. **Watchapp on emery.** The Pebble Time 2 has a larger display than
   the Pebble Time the UI was laid out for, so geometry is derived from
   runtime layer bounds instead of basalt-sized pixel constants. Emery
   is the platform the design is tuned for; basalt, diorite and chalk
   still build. Verified by building all four platforms against the real
   proxy and screenshotting each. **Done.**
5. **Reliability and UX polish.** Last-known state per vehicle persisted
   to watch storage, so launch paints real numbers instead of
   "Connecting…". A 12V auxiliary-battery readout on the detail screen —
   the one number that shows whether the force-refresh floor is actually
   protecting the battery. Staleness stays visible alongside errors
   rather than being replaced by them, on both screens. A single short
   vibration on the OK→error edge, never repeated while the error
   persists. **Done.**

   Not built: a separate vehicle-picker screen. Up/Down on the main
   screen already cycles vehicles (phase 7 moved detail's Up/Down to
   scrolling), which is the same affordance a picker would provide; a
   list window for an account with one car would be dead code. Revisit
   if a second vehicle ever lands on the account.

6. **Touch controls, launch freshness, and packaging polish.** Touch
   gestures on the Pebble Time 2, the only target with a touchscreen:
   drag down on either screen force-refreshes, swipe left on main opens
   the detail screen, swipe right on detail returns, swipe right on
   main quits. Buttons keep working everywhere and remain the only
   controls on basalt, diorite and chalk; Up/Down became a no-op with a
   single vehicle instead of a phantom refresh. Launch always shows the
   newest state Kia's servers hold — the companion's first status
   request each session sends `fresh=1`, an ordinary read that skips
   the proxy's freshness window without waking the car. A Kia-mark menu
   icon shows in the watch launcher and phone locker, and
   `pebble/appstore.md` drafts a future store listing, since sideloaded
   apps cannot ship a description to the phone. **Done.**

7. **Remote commands and detail depth.** An actions menu — Select or
   swipe left on the detail screen — sends the ten remote commands
   through the companion to the proxy's actions endpoint, gated behind
   `ENABLE_COMMANDS` and throttled by `COMMAND_MIN_SECONDS`, with a
   confirm step on the risky ones (see "Remote commands"). The detail
   screen became scrollable — Up/Down scroll it, so vehicle switching
   now lives on the main screen only — and gained AC/DC charge limits,
   a summary of open doors, windows, trunk, hood and sunroof,
   efficiency and battery temperature; it also shows the in-flight
   spinner main already had. The main screen's SoC is centred.
   **Done.**

All seven planned phases are complete.

## Risks and open questions

- **Kia ToS.** Unofficial API use is not sanctioned. Risk of account lockout,
  especially with aggressive polling. Mitigation: default cache ≥ 10 min,
  user-triggered refresh only, exponential backoff on errors. Smartcar is a
  licensed fallback if this becomes untenable, but its data set is narrower
  and it's paid.
- **12V battery drain.** Only *forced* pulls wake the telematics unit.
  `LIVE_FORCE_MIN_SECONDS` bounds them per vehicle, the detector never
  forces, and ordinary reads take Kia's server-side state. The failure
  mode to watch for is a client that forces on a timer — nothing does
  today, and nothing should.
- **PV5 payload shape.** The PV5 is new enough that the community library
  may not normalise every field. `proxy/tools/dump_vehicle.py` dumps the
  real payload (VIN and coordinates redacted) so a mapping can be checked
  against the car rather than inferred; re-run it when Kia reshapes
  responses or a field starts reading wrong on the watch.
- **Auth fragility.** Kia EU periodically changes its login flow, and the
  library has had to follow it more than once. A break shows up as a 502
  with "Kia login failed" rather than silence; the fix is normally a
  library upgrade.
- **Pebble constraints.** 128 KB app memory on emery, half that on basalt
  and diorite, so the smaller platforms set the ceiling. (The 24 KB figure
  this file used to quote is aplite's, which the project doesn't target.)
  Keep fonts/images modest, avoid long strings in AppMessage.
- **Single-user assumption.** Proxy is intentionally not multi-tenant; if that
  changes, auth + storage need rework.

## Repo layout

```
proxy/            # FastAPI service + Dockerfile
  app/
    sources/      # demo | live data sources behind one protocol
    store.py      # SQLite: Kia refresh token + last-known state
  scenarios/      # time-evolving demo payloads
  tools/          # dump_vehicle.py — real payload capture, redacted
  tests/
pebble/           # Pebble watchapp
  src/c/          # watchapp C source
  src/pkjs/       # PebbleKit JS companion
  resources/      # menu icon (watch launcher + phone locker)
  tools/          # icon generator, emulator touch injector
  appstore.md     # draft store listing for a future submission
  package.json    # pebble project manifest
  wscript
DESIGN.md         # this file
README.md         # quickstart
```

The only bundled resource is the menu icon — the UI itself draws
everything with system fonts and graphics primitives, which is what
keeps it comfortable on the smaller platforms.

## Building and running

See `README.md` for the full build / emulator / install / configure
flow. This file intentionally does not duplicate it, so only one place
can drift out of date.

## Out of scope (for now)

- Multi-account / multi-user support.
- Apple Watch / Wear OS parity.
- Android Automotive in-vehicle app (different SDK, different problem).
