# Pebble Kia Watch App — Design

Status: all eight phases built. Live Kia data flows watch -> companion ->
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
   Owns the Kia session, caches vehicle state, coalesces concurrent
   refreshes into one wake, exposes a tiny JSON API for the companion.
   Deployed to a small VPS or home server in the EU region.

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
  it, so anything that has to happen while the app is closed needs a
  server anyway.

Given the Pi + Caddy infra is already there and the proxy is reused by
other clients, direct mode is explicitly **rejected for this project**.
It's documented here so a forker without home-server infra can make an
informed choice to strip the proxy out.

## Components

### Proxy (`proxy/`)

- Python 3.13, FastAPI, `hyundai_kia_connect_api` as a dependency.
- Endpoints:
  - `GET /vehicles` — list vehicles on the account (id, VIN, nickname, model).
  - `GET /vehicles/{id}/status` — the proxy's copy while it is younger
    than `LIVE_REFRESH_MIN_SECONDS`, else Kia's copy. `?fresh=1` skips
    that window for one ordinary read — still never a wake; the
    companion sends it once after a remote command. `?force=1` wakes
    the vehicle. While the car is charging, a plain read of an entry
    older than `LIVE_CHARGING_REFRESH_SECONDS` becomes a wake on its
    own. When both flags are sent, force wins. See "Refresh model".
  - `POST /vehicles/{id}/refresh` — same as `force=1`.
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
specific: `LIVE_REFRESH_MIN_SECONDS` (default 600) bounds how often an
ordinary read reaches Kia on live; `DEMO_REFRESH_MIN_SECONDS` (default
5) keeps scenario progression visible to polling clients. Clients
(watch, HA, dashboard) are unaware of which source is serving them.

#### Refresh model

There are two kinds of upstream read. The policy around them was
rewritten in phase 8 — the phased plan records what it replaced.

- An ordinary read asks Kia for the state the vehicle last reported.
  It costs an API call and nothing else — the car stays asleep. The
  proxy serves its own copy for `LIVE_REFRESH_MIN_SECONDS` before
  asking again; the watch's 15 s poll is what drives these. `fresh=1`
  skips that window for one ordinary read, and is the one read that
  never becomes a wake, charging or not. The companion sends it once,
  eight seconds after a remote command: telling the car to stop
  charging must not be followed by waking it to ask about it.
- A forced read (`?force=1`, `POST /refresh`) wakes the telematics
  unit for genuinely current data. There is no floor: every pull-down
  and long-press on the watch wakes the car, and so does every launch.
  For a CCS2 car the library triggers the wake, sleeps a fixed 25 s,
  then reads Kia's snapshot, so a forced read takes about half a
  minute. The companion's HTTP timeout is 60 s to match — at the 15 s
  it used to be, the watch reported a timeout on every real force and
  the fresh numbers only arrived with the next poll. A wake can still
  overrun 60 s (the library retries the whole thing, sleep included, if
  Kia rejects the device id), and the proxy finishes and caches the
  result regardless, so the watch shows a timeout and the next poll
  shows the data.

What bounds wakes is coalescing rather than a floor. `StatusCache`
runs at most one wake per vehicle at a time: a request arriving during
one waits for it and shares its answer, and so does an ordinary read
arriving during any fetch. A force arriving during an ordinary read is
the exception — it starts its own fetch and takes the in-flight slot,
so anything later joins the wake rather than the cheaper read it
supersedes. Two impatient pulls therefore cost one wake. Because the
two can be in flight together, the cache keeps whichever result was
started last rather than whichever landed last, so a slow ordinary
read cannot overwrite a wake's answer.

The one time the proxy wakes the car without being asked is a charging
session. While the last-known state says `is_charging`, an ordinary
read arriving `LIVE_CHARGING_REFRESH_SECONDS` (default 60) after the
last wake is upgraded to a wake, so the watch's poll shows the charge
rate and ETA moving without anyone pulling. The charger holds the 12V
battery up for the whole session, so this is the wake that costs
least. It switches itself off: the first read showing charging has
stopped puts the vehicle back on the ordinary window. It is
client-driven, not a timer — with nobody asking, nothing happens — but
"nobody" means no client at all, not just no watch: a Home Assistant
sensor polling the same endpoint drives the wakes too.

The window is paced from the last wake, not from the age of the cached
entry, because an ordinary read in between rewrites that entry. Pacing
on entry age meant a deployment with `LIVE_REFRESH_MIN_SECONDS` below
the charging window never woke the car at all — the exact symptom the
upgrade exists to fix. What the wrist sees is longer than the window:
60 s of waiting, plus the ~30 s the wake itself takes, plus up to one
15 s poll interval, so the numbers move about every minute and a half.

Launch: the watch paints its flash copy, asks for an ordinary status
(the proxy's copy, back within a second), then answers that reply with
a forced refresh. Cached numbers at once, the car's own about thirty
seconds later, the spinner running in between. If that first read was
itself upgraded to a wake — a charging car, which is the launch most
likely to matter — the reply says so via `forced`, which the companion
forwards to the watch, and the watch skips its own wake rather than
spending another half minute learning nothing. The trigger lives in
the watchapp rather than the companion because the watchapp restarts
on every launch and the companion's JS session need not.

`forced` in the response says the proxy asked Kia to wake the car. It
is not proof the car answered: the library discards the wake trigger's
response and sleeps a fixed interval, so a car with no signal yields
the previous snapshot with `forced: true` and no error. The reading's
own `updated_at` is the honest signal, which is why both watch screens
carry the age line. The proxy logs a warning when a wake does not
advance it.

An assumption worth revisiting against the car: that a charging PV5
does not report to Kia on its own, so a wake is the only way to see
the rate move. If it turns out to push unprompted, an ordinary read
would do the same job for a fraction of the cost.

#### Remote commands

Commands were out of scope until the owner explicitly asked for them —
exactly the trigger the original scope note anticipated. They are
deliberately narrow and off by default.

- `POST /vehicles/{id}/actions/{action}`, eleven actions: `lock`,
  `unlock`, `start_charge`, `stop_charge`, `start_climate`,
  `start_defrost`, `stop_climate`, `open_charge_port`,
  `close_charge_port`, `hazard_lights`, `set_charge_limit`.
- `start_defrost` is the same climate preset as `start_climate` with
  the de-icing surfaces lit: the CCS2 payload the library builds
  already carries `windshieldFrontDefogState`, `strgWhlHeating` and
  `sideRearMirrorHeating` (which `heating` in 1/2/4 selects, and which
  drives the rear window with the mirrors), so this needed no new
  endpoint — only more of `ClimateRequestOptions`.
- `hazard_lights` has no matching "off": the car flashes for thirty
  seconds and stops by itself. An earlier note here said hazards were
  unavailable in the EU. That was true of `ApiImpl`'s base method and
  is now stale — `KiaUvoApiEU` extends `ApiImplType1`, which posts to
  the CCS2 `/ccs2/control/light` path.
- `set_charge_limit` is the one action carrying parameters, `?ac=` and
  `?dc=`, each a multiple of ten between 10 and 100. Both are
  required: Kia writes the pair in one call, so sending one alone
  would overwrite the other with whatever the caller omitted. Values
  off that grid are refused rather than rounded — a silently corrected
  85 would leave the watch showing a limit the car never got.
- `set_charge_limit` is the one command not yet confirmed against the
  real PV5. The library posts it to the pre-CCS2 `/charge/target` path
  — the same shape of evidence that condemned valet mode above —
  though unlike valet it does pass CCS2-aware headers, and the
  adjacent V2L discharge-limit call does use a `/ccs2/` route. If Kia
  rejects it the watch surfaces the error like any other failure;
  nothing else depends on it. Confirm before treating it as working.
- Valet mode was dropped in phase 9. It was never a PV5 feature: it is
  an infotainment privacy lockdown on older head units, unrelated to
  utility mode (which keeps the sockets and HVAC alive with the car in
  Park, is set in the car, and has no API at all). Three things
  independently said the command was dead weight — `supports_valet_mode`
  is a hard-coded per-region constant on `ApiImplType1` rather than
  anything the car reported, `valet_mode_active` is declared in the
  library and never assigned anywhere, and the endpoint is the
  pre-CCS2 `/control/valet` path while every control route the PV5
  actually uses moved to `/ccs2/control/...`.
- Off unless `ENABLE_COMMANDS=1`. The bearer token has only ever
  granted reads, so its blast radius was "someone can watch the car's
  state". A leaked token must not silently gain unlock; turning
  mutation on is a deliberate operator decision on the proxy side,
  never a default. On `live` it also requires `KIA_PIN`, checked at
  startup: reads never need the PIN, so an empty one leaves a proxy
  that looks healthy until the first action fails inside Kia.
- `COMMAND_MIN_SECONDS` floors the interval between commands. A
  command inside the window is refused with a 429 rather than queued —
  silently dropping a lock request would be worse than an error. The
  slot is claimed before the command is sent, not stamped after it: a
  send can sit behind a wake in progress for half a minute, and a gate
  that only stamped on the way out would let everything that arrived
  meanwhile through. A send Kia rejects hands the slot back.
- Watch-initiated only. No proxy code path — no timer, nothing — ever
  sends a command on its own; every command traces back to a press or
  tap on the wrist.
- Risky actions (unlock, stop charge) confirm on the watch before the
  request is sent. Charge limits do not: the picker's own "Set limits"
  row is the confirming press, and a limit changes nothing a thief
  could use.
- A command can be slow to leave the proxy. `LiveDataSource`
  serialises every upstream call on one lock, and a wake holds it for
  its whole half minute, so a command tapped during one waits for it —
  as does a second vehicle's read on a multi-car account, which can
  then overrun the companion's 60 s timeout. The watch's actions menu
  tracks its own request rather than the general busy flag, so
  "Sending…" means this command and not the wake it is queued behind.

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

None, deliberately. Until phase 8 the proxy ran a transition detector
that pushed charge, plug, lock and climate events through a
self-hosted ntfy; the phone's ntfy app turned them into OS
notifications, which the Pebble mobile app bridged to the wrist. It
was removed because the official Kia app already sends the same events
as phone notifications, and the Pebble app bridges those exactly as it
bridged ntfy's — so the second pipeline produced duplicates and an
extra container to run, and nothing else. A forker without the Kia
app on their phone can find the detector, the notifier and the compose
service in the history before phase 8. Don't reintroduce them without
a reason the Kia app doesn't cover.

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
    last-updated timestamp. Paints the flash copy at launch, then the
    proxy's, then the car's own (see "Refresh model").
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
| `ACTION_AC` | uint8  | `set_charge_limit` only — AC target, percent          |
| `ACTION_DC` | uint8  | `set_charge_limit` only — DC target, percent          |

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
| `DEFROST_ON`    | bool    | Front windscreen defog                               |
| `REAR_DEFROST_ON`| bool   | Rear window heater                                   |
| `WHEEL_HEAT_ON` | bool    | Steering-wheel heater                                |
| `BATT_COND`     | bool    | Pack being heated or cooled right now                |
| `V2L_LIMIT_PCT` | uint8   | SoC the car stops discharging to load at             |
| `V2L_KW_X10`    | uint16  | Discharge rate × 10; the negative half of the reading `CHARGE_KW_X10` truncates |
| `TGT_RANGE_AC_KM`| uint16 | Predicted range at the AC charge limit               |
| `TGT_RANGE_DC_KM`| uint16 | Predicted range at the DC charge limit               |
| `UPDATED_AT`    | uint32  | Unix epoch seconds; 0 means "never"                  |
| `FORCED`        | bool    | The car was woken for this reading. Lets launch skip its own wake, and tells the watch a poll reply is not the wake it awaits |
| `UNIT_MILES`    | bool    | Clay units toggle; rides every list/status response  |
| `ACTION`        | string  | `action_ok` response — echoes the command that ran   |
| `ERROR_MSG`     | string  | Populated on failure; watch surfaces it in the UI    |

Startup race: the companion emits `RESP_KIND=ready` when it comes
online, and the watch kicks off the initial `list` request in response
to any inbox message. This avoids the window where the watch would
otherwise send before pypkjs (or the mobile app's JS runtime) has
attached.

Three of those readings have no library accessor on a CCS2 car and come
straight off the raw payload or are derived in `live.py`:

- Battery conditioning is the union of three nodes —
  `Green.BatteryManagement.BatteryConditioning` (1 = on, the same 0/1/2
  convention the defog nodes use), `HeatingState` and `ChillerRPM`. No
  single one is trustworthy on its own, and together they catch both
  heating and cooling whichever the car populates.
- V2L discharge is `Green.Electric.SmartGrid.RealTimePower` when it
  goes negative. `VehicleToLoad.Mode` reads 1 on a parked, idle PV5, so
  its encoding is unknown and it is deliberately not mapped; the power
  reading says what is actually happening.
- Side-mirror heat has no node in the PV5's payload and is a `TODO` in
  the library, so there is no field for it. It could only ever report
  False.

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
   is one upstream call (the floor and the detector were both removed
   in phase 8). PV5 field mappings confirmed against a real payload
   dump (`proxy/tools/dump_vehicle.py`) rather than guessed — this
   absorbed what was originally a separate phase 5. **Done.**
4. **Watchapp on emery.** The Pebble Time 2 has a larger display than
   the Pebble Time the UI was laid out for, so geometry is derived from
   runtime layer bounds instead of basalt-sized pixel constants. Emery
   is the platform the design is tuned for; basalt, diorite and chalk
   still build. Verified by building all four platforms against the real
   proxy and screenshotting each. **Done.**
5. **Reliability and UX polish.** Last-known state per vehicle persisted
   to watch storage, so launch paints real numbers instead of
   "Connecting…". A 12V auxiliary-battery readout on the detail screen —
   the one number that shows whether the car-waking reads are costing
   the battery anything. Staleness stays visible alongside errors
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
   the proxy's freshness window without waking the car (phase 8 went
   further and wakes the car at launch). A Kia-mark menu icon shows in
   the watch launcher and phone locker, and `pebble/appstore.md` drafts
   a future store listing, since sideloaded apps cannot ship a
   description to the phone. **Done.**

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

8. **Refresh model rework; notifications removed.** The owner found
   the 12V-protecting floor misguided in practice: the official app
   shows the charge rate moving while charging, and the watch could
   not, because even a hard refresh inside the window came back
   cached. The floor went. Every pull-down, long-press and launch
   wakes the car; concurrent requests share one wake instead of
   causing two; and while the car is charging the watch's ordinary
   poll becomes a wake once a minute, dropping back to manual once a
   read shows charging has stopped. Launch paints the proxy's copy and
   then the car's own. The companion's HTTP timeout grew from 15 s to
   60 s to cover the half-minute a CCS2 wake takes — at 15 s the watch
   had been reporting a timeout on every real force. The ntfy
   detector, its notifier and its compose service were removed: the
   official Kia app's notifications reach the watch by the same
   bridge. **Done.**

9. **Wider command surface; winter and V2L readings.** A survey of
   what the CCS2 protocol and `hyundai_kia_connect_api` actually offer
   the PV5, prompted by the owner asking what else the car can do.
   Three new commands: `start_defrost` (the climate preset with the
   de-icing surfaces on — the payload always carried them, we were
   sending zeros), `hazard_lights`, and `set_charge_limit`, which is
   the first action to take parameters and gets a picker screen on the
   watch. Valet mode was removed; it was never a PV5 feature (see
   "Remote commands"). The detail screen gained which heaters are
   running, whether the pack is being conditioned, V2L discharge and
   its floor, and the range predicted at each charge limit. Removing
   two rows and adding four made the detail list long enough to scroll
   a row clean off a round screen, which exposed a latent fault:
   `layout_row` returns a zero-width chord out there and `draw_row`
   then built a negative-width rect from it. **Done.**

All nine phases are complete.

## Risks and open questions

- **Kia ToS.** Unofficial API use is not sanctioned. Risk of account
  lockout, especially with aggressive polling. Mitigation: every read
  is client-driven (launch, a pull, the 15 s poll while the app is
  open), and the proxy's copy answers the poll for ten minutes at a
  time, so an idle day is a handful of calls. The exception is a
  charging session being polled, which wakes the car roughly every
  minute and a half for as long as some client keeps asking. Each wake
  is five Kia calls and each ordinary read four, so an hour of watching
  a charge is a couple of hundred calls. Smartcar is a licensed
  fallback if this becomes untenable, but its data set is narrower and
  it's paid.
- **12V battery drain.** Every forced read wakes the telematics unit
  and draws on the 12V battery. The original design floored wakes at
  15 minutes; phase 8 removed the floor, because the owner only looks
  at the watch when a current reading is wanted and the official app
  wakes the car just as freely. What remains: wakes are user-driven or
  happen while the charger is holding the 12V up anyway, concurrent
  requests share one wake, and nothing wakes on a timer. The failure
  mode to watch for is a second client polling `/status` around the
  clock, which would extend the charging upgrade to sessions nobody is
  looking at. The 12V percentage on the detail screen is the reading to
  check if any of this ever looks wrong.
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
