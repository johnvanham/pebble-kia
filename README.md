# pebble-kia

A Pebble smartwatch app for viewing Kia vehicle stats from the wrist —
state of charge, range, charging status, doors, cabin temp, odometer —
with a self-hosted proxy that talks to Kia Connect on the user's behalf.

Personal project. Open source so others can fork and self-host for their
own vehicles; not run as a hosted service.

## Status

Phase 1 is in: a runnable Pebble watchapp with on-device **demo data**,
so the UI can be iterated on without Kia, a phone, or network access.
The phone companion is a no-op stub and the proxy hasn't been built yet.

See [`DESIGN.md`](./DESIGN.md) for architecture, phased plan, operating
assumptions, and the decision record around proxy vs. direct mode.

| Component              | Status                          |
| ---------------------- | ------------------------------- |
| Pebble watchapp (C)    | Phase 1 — runs with dummy data  |
| PebbleKit JS companion | No-op stub                      |
| Self-hosted proxy      | Not built yet                   |
| HA / dashboard clients | Future                          |

## Repo layout

```
DESIGN.md        architecture, phased plan, decisions
README.md        this file
pebble/          Pebble watchapp
  package.json
  wscript
  src/c/         watchapp C source
  src/pkjs/      PebbleKit JS companion (stub for now)
  resources/
```

## Prerequisites

The Rebble Pebble SDK with the `pebble` CLI on `PATH`. Install options
(Docker image, Homebrew tap, or manual) are documented at
https://developer.repebble.com/sdk/.

Supported target platforms in `pebble/package.json`:

- `basalt` — Pebble Time / Pebble Time Steel
- `chalk`  — Pebble Time Round
- `diorite` — Pebble 2
- `emery`  — Pebble Time 2 (preview hardware)

## Build

```sh
cd pebble
pebble build
```

Output bundle: `pebble/build/pebble-kia.pbw`.

## Run in the emulator

```sh
pebble install --emulator basalt    # Pebble Time
pebble install --emulator chalk     # Time Round
pebble install --emulator diorite   # Pebble 2
pebble install --emulator emery     # Time 2
```

`pebble logs --emulator <platform>` tails `APP_LOG` output.

## Install on a physical watch

Pair the watch in the official Pebble mobile app, then enable the
developer connection (Settings → Developer) and note the watch's IP.

```sh
pebble install --phone <WATCH_IP>
```

The app is small and well within the basalt heap budget, so an existing
Pebble Time should run it fine; the same bundle will run unmodified on
Pebble Time 2 once it ships.

## Controls (demo mode)

- **Up / Down** — switch between demo vehicles (PV5 Passenger, EV9 GT-Line).
- **Select** — open the detail screen (odometer, cabin temp, doors,
  charge rate, ETA).
- **Select (long press)** — simulate a refresh: short vibration, then the
  dummy numbers nudge so you can see the UI react.
- **Back** — return to the main screen or exit the app.

A `DEMO` badge is drawn top-right on the main screen so it's never
ambiguous that the values are fake.

## Display units

UK defaults: range and odometer render in miles, cabin temp in Celsius,
charge rate in kW. Data is transported and cached in km end-to-end; the
watch converts on the fly. Flip `PBK_USE_MILES` in `pebble/src/c/units.h`
to `0` and rebuild if you want kilometres. A runtime toggle via Clay
configuration is deferred until phase 5.

## What's next

The phased plan lives in `DESIGN.md`. Short version:

1. Watchapp with demo data ← **done**
2. FastAPI proxy skeleton (stub endpoints, Dockerfile, Caddy snippet)
3. Proxy wired to `hyundai_kia_connect_api` with SQLite persistence
4. End-to-end: companion calls proxy, watch renders real data
5. Detail + vehicle picker polish, configuration UI
6. PV5-specific payload validation once the vehicle is on the account

This README will grow as each phase lands.

## For forkers

Before cloning for your own use, read `DESIGN.md` →
"Operating assumptions" and "Alternative considered: direct mode". The
default design assumes you'll run the proxy on your own home server
(Raspberry Pi + Docker + Caddy works well). If you don't have home-server
infra, a direct phone-to-Kia mode is feasible but involves more work and
trade-offs — the decision is documented there.
