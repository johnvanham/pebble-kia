# pebble-kia

A Pebble smartwatch app for glancing at Kia vehicle stats —
state of charge, range, charging status, doors, cabin temp, odometer —
backed by a small self-hosted proxy that talks to Kia Connect on the
user's behalf.

Personal project. Open source so others can fork and self-host; not run
as a hosted service.

## Status

| Component              | Status                                                     |
| ---------------------- | ---------------------------------------------------------- |
| Pebble watchapp (C)    | Fetches vehicle list + status from the companion           |
| PebbleKit JS companion | Clay config page; calls the proxy and packs AppMessage     |
| Self-hosted proxy      | FastAPI, demo data source, in-memory cache + rate limit    |
| Live Kia integration   | Not built yet — proxy has a `demo` source only             |
| HA / dashboard clients | Future                                                     |

End-to-end demo mode runs today: edit `proxy/demo-data.json`, long-press
SELECT on the watch, the proxy re-reads the file and the new values
render.

See [`DESIGN.md`](./DESIGN.md) for architecture, phased plan, operating
assumptions, and the decision record around proxy vs. direct mode.

## How it fits together

```
┌──────────────┐  BT / AppMessage   ┌──────────────────┐   HTTPS   ┌──────────────┐   HTTPS    ┌────────────────┐
│ Pebble watch │ ◀────────────────▶ │ Pebble mobile    │ ◀───────▶ │ Self-hosted  │ ◀────────▶ │ Kia Connect    │
│ (C watchapp) │                    │ app + PebbleKit  │           │ proxy        │            │ EU servers     │
└──────────────┘                    │ JS companion     │           │ (Docker)     │            │ (phase 3)      │
                                    └──────────────────┘           └──────────────┘            └────────────────┘
```

The watch holds no credentials and has no network of its own. The phone
companion holds the proxy URL and a bearer token. The proxy holds the
Kia refresh token and caches state so every client (watch, Home
Assistant, future dashboard) shares one upstream session.

## Repo layout

```
DESIGN.md          architecture, phased plan, decisions
README.md          this file
pebble/            Pebble watchapp
  package.json
  wscript
  src/c/           watchapp C source
  src/pkjs/        PebbleKit JS companion + Clay config page
proxy/             FastAPI proxy (phase 2: demo data source only)
  app/
  demo-data.json   editable sample payload
  Dockerfile
  docker-compose.yml
  Caddyfile.example
```

## Emulator quickstart

Everything runs on one machine, no phone or physical watch needed.
Useful for iterating on the watchapp UI or exercising the proxy.

**One-off setup** — install the Rebble Pebble SDK and `uv` (Fedora
example; see https://developer.repebble.com/sdk/ for other platforms):

```sh
sudo dnf install -y nodejs dtc SDL-devel SDL2 pixman glib2 uv
uv tool install pebble-tool --python 3.13
pebble sdk install latest
```

**Run it**:

```sh
# terminal 1 — start the proxy
cd proxy
echo 'PROXY_BEARER_TOKEN=dev-token-change-me' > .env
uv sync
uv run uvicorn app.main:app --port 8000

# terminal 2 — build and install the watchapp
cd pebble
npm install              # one-off: pulls pebble-clay
pebble build
pebble install --emulator basalt     # or chalk / diorite / emery

# open the Clay config in your browser, fill in:
#   Base URL     http://localhost:8000
#   Bearer token dev-token-change-me   (matches what you set in proxy/.env)
# click Save — values persist in the emulator's localStorage.
pebble emu-app-config

# First launch will have already failed with "Open Settings to configure
# proxy"; long-press Select on the emulator (or re-install) to retry now
# that config is saved.
pebble logs --emulator basalt        # tail APP_LOG + companion output
```

See [Controls](#controls) for what the buttons do.

## Production setup

Target layout: the proxy runs in a container on a home server, reachable
at an HTTPS URL via a reverse proxy that owns TLS (Caddy is the default
here; the author's Raspberry Pi already runs it with automatic Let's
Encrypt). The watchapp is sideloaded onto a Pebble paired with the
Rebble Pebble mobile app.

### 1. Deploy the proxy

**Generate a bearer token** — both the proxy's environment and the
phone's Clay config will use this exact value:

```sh
openssl rand -hex 32
```

**On the home server**, clone (or copy) the repo and set up the env:

```sh
git clone https://github.com/johnvanham/pebble-kia.git
cd pebble-kia/proxy
cp .env.example .env
# edit PROXY_BEARER_TOKEN to the value you just generated
```

Only the `proxy/` subtree is needed on the server; you can sparse-check
or scp just that directory if you prefer.

**Edit `proxy/demo-data.json`** so the vehicle list matches what you
actually want to see. `updated_at` accepts `"-2m"`-style relative
offsets so a hand-edited file stays fresh. (When phase 3 lands, you can
set `DATA_SOURCE=live` in `.env` to talk to Kia instead. The demo source
stays available for offline iteration.)

**Run it** (still inside `pebble-kia/proxy`):

```sh
docker compose up -d --build        # or: podman compose up -d --build
docker logs -f pebble-kia-proxy     # sanity-check startup, Ctrl-C to detach
```

The compose file binds to `127.0.0.1:8000` on the host, so nothing is
exposed on the public internet directly. Swap the bind for `0.0.0.0`
only if you plan to skip the reverse proxy.

**Sanity check** — from the host itself (still in `pebble-kia/proxy`):

```sh
curl -s http://127.0.0.1:8000/health
# {"status":"ok","data_source":"demo"}

TOKEN=$(grep ^PROXY_BEARER_TOKEN .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/vehicles | jq .
```

**Add TLS via Caddy** — drop this into the Caddy config (see
`proxy/Caddyfile.example`) and point DNS at the server:

```
kia-proxy.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

`caddy reload` and Caddy obtains a Let's Encrypt cert automatically. The
public URL `https://kia-proxy.example.com` is what the phone will use.

### 2. Build and install the watchapp

On a workstation with the Pebble SDK installed (see the
[Emulator quickstart](#emulator-quickstart) for install commands):

```sh
cd pebble
npm install
pebble build
# artefact: build/pebble.pbw
```

#### On a physical watch

1. Install the Rebble Pebble mobile app (https://rebble.io/download)
   and pair your watch — the app replaces Pebble Inc's long-defunct one
   and talks to the Rebble Web Services token endpoint for login.
2. In the mobile app, open Settings → Developer → enable **Developer
   Connection**. Note the watch's IP, which the app displays.
3. From the workstation (on the same LAN as the phone):

   ```sh
   cd pebble
   pebble install --phone <WATCH_IP>
   ```

   The watch vibrates when the install lands. The Kia app then appears
   in the mobile app's locker and on the watch's app launcher.

Alternatively, share the `pebble/build/pebble.pbw` file to the Rebble
app via the OS share sheet — that sideload path works without the
`pebble` CLI at the cost of not being scriptable.

#### In the emulator

Already covered above. `pebble install --emulator basalt` (or `chalk` /
`diorite` / `emery`) reinstalls any time you rebuild.

### 3. Configure from the phone

1. Open the Rebble Pebble mobile app → locker → **Kia** → **Settings**.
2. The Clay config page opens in a webview. Fill in:
   - **Base URL**: `https://kia-proxy.example.com` (or the LAN URL you
     chose if you're skipping TLS — e.g. `http://192.168.1.20:8000`).
   - **Bearer token**: paste the value from `proxy/.env`.
3. Tap **Save**. The values persist in `localStorage` on the phone; the
   watch doesn't need to know them.
4. Back on the watch, launch the Kia app. It should load the vehicle
   list within a second or two. If you see `Can't reach proxy`, check
   that the phone can reach the URL (open the URL's `/health` in the
   phone's browser — should return `{"status":"ok","data_source":…}`;
   401s there mean the token doesn't match).

## Controls

- **Up / Down** — switch between vehicles returned by the proxy. If the
  newly-selected vehicle has no cached status yet, the watch asks the
  companion to fetch it.
- **Select** — open the detail screen (odometer, cabin temp, doors,
  charge rate, ETA).
- **Select (long press, ≥500ms)** — force refresh the current vehicle
  (POSTs `/vehicles/{id}/refresh`, short vibration, `ERR` top-right if
  the phone link or proxy is down).
- **Back** — return to the main screen or exit the app.

While a request is in flight the watch shows `...` top-right. Errors
surface in the bottom status line so the user can read what went wrong
without digging into logs. Nothing is rendered from compiled state — if
the companion never responds, the watch sits on a "Connecting…" screen.

## Display units

UK defaults: range and odometer render in miles, cabin temp in Celsius,
charge rate in kW. Data is transported and cached in km end-to-end; the
watch converts on the fly. Flip `PBK_USE_MILES` in `pebble/src/c/units.h`
to `0` and rebuild if you want kilometres. A runtime toggle via Clay
configuration is deferred until later (see DESIGN.md).

## Updating

**Watchapp** — `cd pebble && pebble build && pebble install --phone <IP>`
(or `--emulator basalt`). The Clay config persists across installs.

**Proxy** — on the home server, pull the new code and restart:

```sh
cd proxy
git pull
docker compose up -d --build
```

Clients re-fetch on the next request; no watch-side restart needed.

## Troubleshooting

- **Watch shows `Can't reach proxy`** — the phone can't reach the URL
  in Clay config. Open `/health` on that URL from the phone's browser;
  if it fails too, the issue is network/DNS/firewall, not the watch.
- **Watch shows `Bad proxy token`** — the token in Clay config doesn't
  match `PROXY_BEARER_TOKEN` in `proxy/.env`. Paste both from the same
  source to rule out whitespace.
- **Watch shows `Live mode not ready`** — the proxy is running with
  `DATA_SOURCE=live`, which isn't implemented yet. Set
  `DATA_SOURCE=demo` (or unset it) and restart the container.
- **Watch sits on `Connecting…` forever** — the companion isn't sending
  its ready nudge, usually because the phone's Bluetooth link to the
  watch is down or the mobile app isn't running. Re-open the app on
  the phone.
- **`pebble install --phone` fails** — developer connection is
  disabled or the watch IP is wrong. Re-check Settings → Developer in
  the mobile app.

## What's next

The phased plan lives in `DESIGN.md`. Short version:

1. Watchapp with demo data ← **done**
2. Proxy skeleton + end-to-end wiring against demo ← **done**
3. Proxy wired to `hyundai_kia_connect_api` with SQLite persistence
4. UX polish (richer status line, persist last-known state on the
   watch, units toggle in Clay)
5. PV5-specific payload validation once the vehicle is on the account

## For forkers

Before cloning for your own use, read `DESIGN.md` →
"Operating assumptions" and "Alternative considered: direct mode". The
default design assumes you'll run the proxy on your own home server
(Raspberry Pi + Docker + Caddy works well). If you don't have
home-server infra, a direct phone-to-Kia mode is feasible but involves
more work and trade-offs — the decision is documented there.
