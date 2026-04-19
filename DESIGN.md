# Pebble Kia Watch App — Design

Status: draft / planning. No code yet.

Goal: view live stats for a Kia PV5 Passenger (and future vehicles on the same
account) from a Pebble smartwatch — state of charge, estimated range, charging
status, doors/locks, odometer, cabin temperature, last-known location.

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

- Python 3.12, FastAPI, `hyundai_kia_connect_api` as a dependency.
- Endpoints:
  - `GET /vehicles` — list vehicles on the account (id, VIN, nickname, model).
  - `GET /vehicles/{id}/status` — cached state; `?force=1` triggers a live
    refresh (rate-limited, default ≥ 10 minutes between live pulls).
  - `POST /vehicles/{id}/refresh` — explicit refresh, same rate limit.
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

- `demo` — reads `demo-data.json` on every fetch. The file is intended
  to be hand-edited so the owner can simulate vehicle state changes
  (e.g. start charging, deplete the battery) without a real car. Used
  while phases 2–4 are under development and for offline iteration
  later.
- `live` — will call `hyundai_kia_connect_api`. Currently a stub that
  returns 501 on every call; implemented in phase 3.

Both sources sit behind the same cache layer and rate-limit, so swapping
between them is a config change with no wire-format impact. Clients
(watch, HA, dashboard) are unaware of which source is serving them.

### Token bootstrap (`proxy/bootstrap/`)

- Kia EU login is a browser-based SSO flow that can't be automated purely from
  Python. One-time desktop step using the community Selenium-based capture
  script (tracked in hyundai_kia_connect_api issue #1098, which fixes the
  timing + German-locale bugs in the older `HyundaiFetchApiTokensSelenium.py`).
- Output: a refresh token written into the proxy's SQLite store. The proxy
  then runs headlessly and only re-bootstraps on token expiry or password
  change.

### PebbleKit JS companion (`pebble/src/pkjs/`)

- Thin translator. Receives AppMessage keys from the watch, calls proxy,
  formats the response into AppMessage dictionary values, sends back.
- Configuration page (Rebble appstore standard) to set proxy URL + bearer
  token; stored in `localStorage`.
- No Kia credentials — users never enter their Kia password on the phone.

### Watchapp (`pebble/src/c/`)

- Written in C against the Pebble SDK (Rebble 4.9.x).
- Screens:
  - **Main** — big SoC percentage, range, plug status, odometer, last-updated
    timestamp.
  - **Detail** — door/lock state, cabin temp, 12V SoC, charge rate (kW) if
    charging, estimated charge-complete time.
  - **Vehicle picker** — only shown when the account has >1 vehicle.
- Controls: Select = refresh now, Up/Down = switch vehicle (if applicable),
  Back = exit.
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
| `REQ_KIND`  | string | `list` \| `status` \| `refresh`                     |
| `REQ_ID`    | string | Vehicle id (status/refresh only)                    |

Companion → watch (responses):

| Key             | Type    | Notes                                                |
|-----------------|---------|------------------------------------------------------|
| `RESP_KIND`     | string  | `ready` (startup nudge) \| `list` \| `status` \| `error` |
| `VEHICLE_COUNT` | uint8   | list response                                        |
| `VEHICLE_ID[N]` | string  | list response (N slots, `MAX_VEHICLES` = 4)          |
| `VEHICLE_NICK[N]`| string | list response (display name)                        |
| `STATUS_ID`     | string  | status response (which vehicle this is for)          |
| `SOC_PCT`       | uint8   | 0–100                                                |
| `RANGE_KM`      | uint16  | Estimated range (km — wire format stays metric)      |
| `IS_CHARGING`   | bool    |                                                      |
| `CHARGE_KW_X10` | uint16  | Charge rate × 10 to carry 1 decimal without floats   |
| `CHARGE_ETA_MIN`| uint16  | Minutes to target SoC                                |
| `PLUG`          | uint8   | 0=unplugged, 1=AC, 2=DC                              |
| `DOORS_LOCKED`  | bool    |                                                      |
| `CABIN_TEMP_C`  | int8    |                                                      |
| `ODO_KM`        | uint32  |                                                      |
| `UPDATED_AT`    | uint32  | Unix epoch seconds; 0 means "never"                  |
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
Celsius for cabin temp, kW for charge rate. The owner drives in the UK and
Kia's head unit shows miles, so the watch matches.

Implementation: `pebble/src/c/units.h` defines `PBK_USE_MILES` (default 1)
and a `format_distance_km()` helper. All km-to-display conversion flows
through that helper; flipping the macro to 0 switches every distance
readout back to km without touching call sites. Both Pebble platforms are
fixed-point, so the helper uses integer math (km × 1000 / 1609, rounded)
rather than floats. When the configuration UI lands in phase 5 this macro
can become a Clay setting persisted to `localStorage`; for now it's a
compile-time constant because the deployment is single-user.

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
3. **Proxy wired to Kia.** Implement the `live` source on top of
   `hyundai_kia_connect_api`, document the one-time Selenium bootstrap,
   add SQLite persistence so cached state survives restarts.
4. **Detail + picker screens polish, configuration UX improvements**
   (status-line error detail, last-update indicator, unit toggle in
   Clay, persist last-known state on the watch for instant boot).
5. **PV5-specific validation.** Once a PV5 is on the account, inspect
   the real `ccs2/carstatus/latest` payload, patch the proxy's vehicle
   adapter, confirm field mappings (the PV5 is new enough that the
   community library may not yet normalise every field correctly).

Phases 1–4 can be built against an EV6/EV9 today; phase 5 is the
PV5-specific pass once the vehicle is delivered.

## Risks and open questions

- **Kia ToS.** Unofficial API use is not sanctioned. Risk of account lockout,
  especially with aggressive polling. Mitigation: default cache ≥ 10 min,
  user-triggered refresh only, exponential backoff on errors. Smartcar is a
  licensed fallback if this becomes untenable, but its data set is narrower
  and it's paid.
- **12V battery drain.** Frequent live pulls wake the telematics unit and
  drain the 12V. The proxy must rate-limit and prefer cached data.
- **PV5 payload shape.** Unknown until hardware is available; likely uses the
  newer CCS2 endpoint (`/api/v1/spa/vehicles/{id}/ccs2/carstatus/latest`).
  Budget for a patch to the library's PV5 vehicle adapter.
- **Auth fragility.** Kia EU SSO changes periodically; the Selenium bootstrap
  may need updates. Track upstream issue #1098 and successors.
- **Pebble constraints.** ~24–96 KB app memory depending on platform; keep
  fonts/images modest, avoid long strings in AppMessage.
- **Single-user assumption.** Proxy is intentionally not multi-tenant; if that
  changes, auth + storage need rework.

## Repo layout (planned)

```
proxy/            # FastAPI service + Dockerfile
  app/
  bootstrap/      # one-time Selenium token capture helper
  tests/
pebble/           # Pebble watchapp
  src/c/          # watchapp C source
  src/pkjs/       # PebbleKit JS companion
  resources/      # images, fonts
  package.json    # pebble project manifest
  wscript
DESIGN.md         # this file
README.md         # quickstart
```

## Building and running

See `README.md` for the full build / emulator / install / configure
flow. This file intentionally does not duplicate it, so only one place
can drift out of date.

## Out of scope (for now)

- Remote commands (lock/unlock, climate pre-conditioning, start charging).
  Read-only first; commands are a separate risk surface.
- Multi-account / multi-user support.
- Apple Watch / Wear OS parity.
- Android Automotive in-vehicle app (different SDK, different problem).
