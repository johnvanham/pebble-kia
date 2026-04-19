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

Phase 2: skeleton with a pluggable data-source layer. Only the `demo`
source is implemented — it reads `demo-data.json` and re-reads on every
fetch so you can edit the file to simulate changing vehicle state. The
`live` source is a stub that returns HTTP 501 until phase 3 wires up
`hyundai_kia_connect_api`.

## Endpoints

All routes except `/health` require `Authorization: Bearer <token>`.

| Method | Path                               | Purpose                                     |
| ------ | ---------------------------------- | ------------------------------------------- |
| GET    | `/health`                          | Liveness + which data source is active      |
| GET    | `/vehicles`                        | Account vehicles: id, VIN, nickname, model  |
| GET    | `/vehicles/{id}/status[?force=1]`  | Cached status; `force=1` bypasses the cache |
| POST   | `/vehicles/{id}/refresh`           | Explicit bypass — same rate limit as force  |

`force=1` and `/refresh` still count against the per-vehicle interval on
the live source to protect the 12V battery; on the demo source the same
call just re-reads the JSON file.

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
| `scenarios/pv5-preconditioning.json` | Cold morning: climate starts, cabin warms, unplug, unlock, drive off. Loops every 10 min. |

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
- `DATA_SOURCE` — `demo` (default) or `live`. `live` currently 501s on every call.
- `DEMO_DATA_FILE` — path to the JSON file the demo source reads. Relative paths resolve against the working directory.
- `LIVE_REFRESH_MIN_SECONDS` — min seconds between live pulls on the live source. Defaults to 600. Protects the 12V battery from aggressive polling.
- `DEMO_REFRESH_MIN_SECONDS` — same knob, demo source. Defaults to 5 so scenario progression is visible to polling clients.
- `LOG_LEVEL` — uvicorn log level. Defaults to `info`.
