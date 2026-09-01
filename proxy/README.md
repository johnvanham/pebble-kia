# pebble-kia-proxy

A small FastAPI service that wraps the Kia Connect account and exposes a
tiny JSON API to the Pebble watchapp, Home Assistant, and any other
local client.

Single-user. Open source but not run as a hosted service. See the
repo-root `DESIGN.md` for architecture and the operating-assumption
decisions that shaped it, and the repo-root `README.md` for the full
watch + companion + proxy setup flow. This file is the proxy-side
depth reference — endpoint contract, environment variables, and
local-dev iteration tips.

## Status

All phases built. Two data sources sit behind one cache: `demo` reads
`DEMO_DATA_FILE` on every fetch (a static snapshot or a time-evolving
scenario), and `live` wraps `hyundai_kia_connect_api` against the real
Kia account, with the refresh token and last-known state persisted to
SQLite so restarts cost neither a login nor a cold watch screen.
Remote commands exist too, behind `ENABLE_COMMANDS` — see "Endpoints".

## Endpoints

All routes except `/health` require `Authorization: Bearer <token>`.

| Method | Path                                | Purpose                                     |
| ------ | ----------------------------------- | ------------------------------------------- |
| GET    | `/health`                           | Liveness + which data source is active      |
| GET    | `/vehicles`                         | Account vehicles: id, VIN, nickname, model  |
| GET    | `/vehicles/{id}/status[?force=1][?fresh=1]` | Cached status; see the flags below  |
| POST   | `/vehicles/{id}/refresh`            | Same as `force=1` — wakes the vehicle       |
| POST   | `/vehicles/{id}/actions/{action}`   | Remote command; off unless `ENABLE_COMMANDS=1` |

Two flags, two very different costs. `force=1` (and `/refresh`) wakes
the telematics unit for genuinely current data; it draws on the 12V
battery, so `LIVE_FORCE_MIN_SECONDS` floors it and a force inside the
window is downgraded to an ordinary read — served from cache when the
entry is still fresh — with `forced: false`. `fresh=1` is
an ordinary read that skips the `LIVE_REFRESH_MIN_SECONDS` cache window
— it asks Kia's servers for the state they already hold and never wakes
the car; the watch sends it once per launch. When both are sent, force
semantics win.

Commands are a separate risk surface from reads, so the actions route
is off unless `ENABLE_COMMANDS=1` — the bearer token otherwise grants
nothing but reads, and a leaked token must not silently gain unlock.
Ten actions are accepted: `lock`, `unlock`, `start_charge`,
`stop_charge`, `start_climate`, `stop_climate`, `open_charge_port`,
`close_charge_port`, `start_valet`, `stop_valet` (hazard lights are
absent because `hyundai_kia_connect_api` raises not-implemented for
the EU region). `COMMAND_MIN_SECONDS` floors the interval between
commands; unlike a downgraded force, a command inside the window is
refused with a 429, because silently dropping a lock request would be
worse than an error. Commands are only ever watch-initiated — nothing
in the proxy sends one on a timer.

## Run locally

```sh
cp .env.example .env
# edit .env and set PROXY_BEARER_TOKEN
uv sync
uv run uvicorn app.main:app --reload
```

Then poke it:

```sh
TOKEN=$(grep ^PROXY_BEARER_TOKEN .env | cut -d= -f2)
curl -s http://localhost:8000/health | jq .
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/vehicles | jq .
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/vehicles/pv5-demo/status | jq .
```

`updated_at` in the demo JSON may be an absolute ISO 8601 string, a
relative offset like `"-2m"` / `"-90s"` / `"-1h"` / `"-3d"`, or `null`
(shorthand for "just now"). Relative offsets are resolved at fetch time
so a hand-edited file stays fresh no matter when it was last saved —
useful because the watch's "updated Xm ago" line gets stale fast
otherwise.

Edit `demo-data.json`, then:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  -X POST http://localhost:8000/vehicles/pv5-demo/refresh | jq .
```

The response reflects the edit; subsequent GETs serve the same value
until the cache interval elapses.

## Scenario mode

The same `DEMO_DATA_FILE` can hold a time-evolving scenario instead of a
static snapshot. Point it at one of `scenarios/*.json` and the proxy
walks an event list to compute the current state on every fetch — good
for exercising charging curves, lock/unlock cycles, and climate
transitions in the emulator.

```sh
DEMO_DATA_FILE=scenarios/pv5-rapid-charge.json uv run uvicorn app.main:app --reload
```

Shipped scenarios:

| File                                 | What it plays out                                                      |
| ------------------------------------ | ---------------------------------------------------------------------- |
| `scenarios/pv5-rapid-charge.json`    | 30 min DC rapid session with a realistic taper (180→150→120→90→60→35 kW), 20→80%, then unplug, climate on, unlock, lock. Loops every 45 min. |
| `scenarios/pv5-ac-charge.json`       | Compressed 11 kW AC charge, 30→80%. Loops every 20 min.                |
| `scenarios/pv5-daily-drive.json`     | Unlock → drive (SoC and range falling, odometer climbing) → lock → return. Loops every 15 min. |
| `scenarios/pv5-preconditioning.json` | Cold morning at -2 °C: climate starts, unplug, unlock, drive off. Loops every 10 min. |

Schema of a scenario file:

```json
{
  "vehicles": [{ "id": "pv5-demo", "vin": "...", "nickname": "PV5", "model": "..." }],
  "scenario": {
    "loop_seconds": 2700,
    "vehicles": {
      "pv5-demo": {
        "baseline": { "soc_pct": 20, "plug": "unplugged", ... },
        "events": [
          { "at_s": 60,  "name": "plug_in",      "patch": { "plug": "dc" } },
          { "at_s": 90,  "name": "charge_start", "patch": { "is_charging": true, "charge_kw": 180 } }
        ]
      }
    }
  }
}
```

State at time T is the baseline with every patch whose `at_s ≤ T` applied
in order; T loops every `loop_seconds` so the demo never ends. The
optional `name` field is what the companion uses to label notifications.
Scenario time starts from proxy boot (monotonic), so restarting the
proxy replays from the top.

`DEMO_REFRESH_MIN_SECONDS` (default 5) keeps the proxy cache short while
running a scenario so the companion's polling loop sees progression
instead of cached values.

## Run in Docker

```sh
docker compose up --build
```

The `demo-data.json` file is bind-mounted read-only so edits on the host
are visible to the container after the next forced refresh.

## Deployment (existing Pi + Caddy)

Copy the `Caddyfile.example` block into the Pi's Caddyfile, adjust the
hostname, and point DNS at the Pi. Caddy takes care of Let's Encrypt
automatically. The compose file binds to `127.0.0.1:8000`, so only Caddy
can reach the container — nothing is exposed to the internet directly.

## Configuration

All settings are environment variables (see `.env.example`):

- `PROXY_BEARER_TOKEN` — required; clients send this as `Authorization: Bearer …`.
- `DATA_SOURCE` — `demo` (default) or `live`. `live` needs the `KIA_*` variables; see `.env.example`.
- `DEMO_DATA_FILE` — path to the JSON file the demo source reads. Relative paths resolve against the working directory.
- `LIVE_REFRESH_MIN_SECONDS` — min seconds between live pulls on the live source. Defaults to 600. Protects the 12V battery from aggressive polling.
- `DEMO_REFRESH_MIN_SECONDS` — same knob, demo source. Defaults to 5 so scenario progression is visible to polling clients.
- `ENABLE_COMMANDS` — set to `1` to enable `POST /vehicles/{id}/actions/{action}`. Off by default; see "Endpoints" for why.
- `COMMAND_MIN_SECONDS` — minimum seconds between remote commands (default 10); a command inside the window gets a 429.
- `DETECTOR_INTERVAL_SECONDS` — how often the transition detector polls its source to diff for notifications. Unset means per-source defaults: 20 on demo, 300 on live.
- `NTFY_URL` / `NTFY_TOPIC` / `NTFY_AUTH_TOKEN` — push-notification destination. Leave `NTFY_URL` empty to disable. See "Notifications" below.
- `SETUP_QR_DIR` — directory the setup QR images are written to at startup. Defaults to `setup/` inside the `proxy/` directory, resolved from the package rather than the working directory so it does not move with however the app was launched. Empty disables them.
- `SETUP_QR_LOG` — also print the token QR into the startup log. Off by default; see "Setup QR" for why.
- `PROXY_PUBLIC_URL` — the base URL the phone should use. Only used to draw the URL QR; leave empty and that image is skipped.
- `LOG_LEVEL` — level for the proxy's own loggers (`app.*`) and nothing else. Defaults to `info`. It does not reach uvicorn's request log, and it deliberately does not raise the root level: `hyundai_kia_connect_api` at DEBUG prints whole Kia payloads including VIN and GPS, so third-party loggers stay at WARNING. An unrecognised value logs a warning and falls back to `info`.

## Setup QR

Clay's settings page cannot open the camera — pebble-clay serves it from
a `data:` URI, an opaque origin, so it is not a secure context and
`getUserMedia` is unavailable. Instead the proxy writes QR codes on
startup and you scan them with the phone's ordinary camera app, then
paste into Clay:

- `$SETUP_QR_DIR/bearer-token.png` — the bearer token, nothing else.
- `$SETUP_QR_DIR/proxy-url.png` — `PROXY_PUBLIC_URL`, when it is set.

These images are a live credential in visual form. The directory and
files are created `0700`/`0600`, the directory is gitignored (in
`proxy/.gitignore` and again at the repo root), and nothing serves them
over HTTP — do not add a route or a `StaticFiles` mount for them. Under
compose they land on the `./setup` bind mount, written by the
container's root user, so reading them from the host needs `sudo`.

The old image is removed before the new one is written, so a failed
write leaves no file rather than a `bearer-token.png` still encoding a
superseded token. A missing QR is obvious; a stale one that scans
cleanly is not.

### Putting the QR in the log

Setting `SETUP_QR_LOG=1` additionally renders the token QR into the
startup log, which on a headless Docker host is the only way to see it
without shelling into the container. Understand what that costs: the log
block *is* the bearer token, machine-decodable by any camera. Logs are
kept by the container log driver long after the token is rotated, get
shipped wherever the host sends logs, and end up pasted into issues and
chats. That is a weaker place to hold the credential than the `0600`
file, which is why it is off by default. Turn it on for a first setup,
turn it back off, and rotate `PROXY_BEARER_TOKEN` if a log with the block
in it has gone anywhere you do not control.

## Notifications

An asyncio background task in the proxy (`app/detector.py`) polls each
vehicle's status every `DETECTOR_INTERVAL_SECONDS`, diffs against the
last observation, and fires an ntfy push on transitions worth
surfacing: charge start/end, plug/unplug, lock/unlock, climate on/off.
First observation for a given vehicle is silent so a process restart
doesn't re-announce state that was already true.

Pushes go to ntfy, which is bundled in `docker-compose.yml` so the
whole stack is self-hosted. The phone installs the ntfy app,
subscribes to your topic, and gets standard OS notifications — which
the Pebble mobile app bridges to the watch automatically, so the
watchapp does not need to be open.

To enable push: set `NTFY_TOPIC` (any guess-hard string you like — the
topic name is your only access control on the default ntfy config)
and, if pointing at a non-compose ntfy, `NTFY_URL`. The compose stack
defaults `NTFY_URL=http://ntfy:80` internally.

Leaving `NTFY_TOPIC` empty disables push; the detector still runs and
logs what it would have sent, which is useful for watching the
scenario engine exercise state changes without noise on your phone.
