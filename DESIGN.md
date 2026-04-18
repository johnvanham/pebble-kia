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

AppMessage dictionary (watch ↔ JS), kept small to fit Pebble's outbox size:

| Key               | Type    | Notes                                  |
|-------------------|---------|----------------------------------------|
| `MSG_TYPE`        | uint8   | 1=request status, 2=status response    |
| `VEHICLE_ID`      | string  | Opaque id from proxy                   |
| `SOC_PCT`         | uint8   | 0–100                                  |
| `RANGE_KM`        | uint16  | Estimated range                        |
| `IS_CHARGING`     | bool    |                                        |
| `CHARGE_KW`       | uint16  | x10 to keep 1 decimal without floats   |
| `CHARGE_ETA_MIN`  | uint16  | Minutes to target SoC                  |
| `PLUG_STATE`      | uint8   | 0=unplugged, 1=AC, 2=DC                |
| `DOORS_LOCKED`    | bool    |                                        |
| `CABIN_TEMP_C`    | int8    |                                        |
| `ODO_KM`          | uint32  |                                        |
| `UPDATED_AT`      | uint32  | Unix epoch seconds                     |
| `ERROR`           | string  | Populated on failure                   |

## Phased plan

Each phase ends with something runnable and committable.

1. **Proxy skeleton.** FastAPI app, auth middleware, in-memory stub responses
   for `/vehicles` and `/vehicles/{id}/status`. Dockerfile + compose.
2. **Proxy wired to Kia.** Integrate `hyundai_kia_connect_api`, document the
   one-time Selenium bootstrap, add SQLite persistence + rate limiting.
3. **Pebble watchapp hello-world.** C scaffolding, package.json, AppMessage
   ping/pong to the JS companion, emulator build working.
4. **End-to-end stats.** Companion calls proxy; watch renders main screen from
   real data; persist last-known state on the watch.
5. **Detail + picker screens, configuration UI, polish.**
6. **PV5-specific validation.** Once a PV5 is on the account, inspect the real
   `ccs2/carstatus/latest` payload, patch the proxy's vehicle adapter, confirm
   field mappings (the PV5 is new enough that the community library may not
   yet normalise every field correctly).

Phases 1–5 can be built against an EV6/EV9 today; phase 6 is the PV5-specific
pass once the vehicle is delivered.

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

## Building and running the watchapp

Prerequisites: the Rebble Pebble SDK installed (`pebble` CLI on PATH). See
https://developer.repebble.com/sdk/ for install instructions (Docker image,
Homebrew, or manual).

```sh
cd pebble
pebble build
```

Build artefacts land in `pebble/build/pebble-kia.pbw`.

### Run in the emulator

Any of the supported platforms:

```sh
pebble install --emulator basalt   # Pebble Time / Time Steel
pebble install --emulator chalk    # Pebble Time Round
pebble install --emulator diorite  # Pebble 2
pebble install --emulator emery    # Pebble Time 2 (preview PT2 hardware)
```

### Install on a physical watch

With the Pebble mobile app paired and developer mode enabled, find the watch's
IP from the app (Developer → Pebble IP) and sideload:

```sh
pebble install --phone <WATCH_IP>
```

### Controls (demo mode)

- **Up / Down** — switch between demo vehicles (PV5 Passenger, EV9 GT-Line).
- **Select** — open the detail screen.
- **Select (long press)** — simulate a refresh; vibrates briefly and nudges
  the dummy values so the UI can be exercised.
- **Back** — return to main screen / exit app.

A `DEMO` badge is drawn in the top-right of the main screen so the source of
the data is never in doubt while iterating.

## Out of scope (for now)

- Remote commands (lock/unlock, climate pre-conditioning, start charging).
  Read-only first; commands are a separate risk surface.
- Multi-account / multi-user support.
- Apple Watch / Wear OS parity.
- Android Automotive in-vehicle app (different SDK, different problem).
