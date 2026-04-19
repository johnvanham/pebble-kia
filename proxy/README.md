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
- `LIVE_REFRESH_MIN_SECONDS` — min seconds between live pulls per vehicle. Defaults to 600.
- `LOG_LEVEL` — uvicorn log level. Defaults to `info`.
