# pebble-kia

A Pebble smartwatch app for glancing at Kia vehicle stats —
state of charge, range, charging status, doors, outside temp, odometer —
backed by a small self-hosted proxy that talks to Kia Connect on the
user's behalf.

Personal project. Open source so others can fork and self-host; not run
as a hosted service.

## Status

| Component              | Status                                                     |
| ---------------------- | ---------------------------------------------------------- |
| Pebble watchapp (C)    | Laid out for emery; last-known state persisted for instant boot |
| PebbleKit JS companion | Clay config page (proxy URL, token, miles/km toggle)       |
| Self-hosted proxy      | FastAPI, `demo` + `live` sources, cache + rate limit       |
| Scenario engine        | Time-evolving demos under `proxy/scenarios/`               |
| Push notifications     | Proxy detector → ntfy (self-hosted) → phone/watch          |
| Live Kia integration   | Real Kia Connect data; SQLite-persisted token and state    |
| HA / dashboard clients | Future                                                     |

Set `DATA_SOURCE=live` and the proxy serves the real vehicle. Demo mode
still runs end to end for offline iteration: pick a scenario, the proxy
replays charge curves / lock cycles / climate on/off, pushes arrive on
the phone (and bridge to the watch) as standard OS notifications.

The layout is tuned for the Pebble Time 2 (`emery`); basalt, diorite
and chalk also build.

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

**One-off setup** — install the Pebble SDK and `uv`. See
https://developer.repebble.com/sdk/ for platforms not listed here.

```sh
# Fedora
sudo dnf install -y nodejs dtc SDL-devel SDL2 pixman glib2 uv
# Arch
sudo pacman -S --needed nodejs npm sdl2-compat glib2 pixman zlib libpng sndio uv

uv tool install pebble-tool --python 3.13   # 3.14 is not supported yet
pebble sdk install latest
```

Verified with pebble-tool 5.0.40 and SDK 4.33.1.

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
pebble install --emulator emery      # or basalt / diorite / chalk

# open the Clay config in your browser, fill in:
#   Base URL     http://localhost:8000
#   Bearer token dev-token-change-me   (matches what you set in proxy/.env)
# click Save — values persist in the emulator's localStorage.
pebble emu-app-config

# First launch will have already failed with "Open Settings to configure
# proxy"; long-press Select on the emulator (or re-install) to retry now
# that config is saved.
pebble logs --emulator emery         # tail APP_LOG + companion output
```

**Exercise a scenario** (time-evolving demo):

```sh
# stop the static proxy, then re-run pointing at a scenario
DEMO_DATA_FILE=scenarios/pv5-rapid-charge.json uv run uvicorn app.main:app --port 8000

# watch proxy-side push detection fire (no phone app needed — pushes
# go to a NullNotifier when NTFY_TOPIC is unset, just logged)
#   [INFO] notifier: would notify: PV5: Plugged in — DC
#   [INFO] notifier: would notify: PV5: Charging — 180.0 kW • ETA 28 min
```

Pointing at real ntfy is the same command plus `NTFY_URL` and
`NTFY_TOPIC`. See `proxy/README.md` → "Notifications".

See [Controls](#controls) for what the buttons do.

## Production setup

Target layout: the proxy runs in a container on a home server, reachable
at an HTTPS URL via a reverse proxy that owns TLS (Caddy is the default
here; the author's Raspberry Pi already runs it with automatic Let's
Encrypt). The watchapp is sideloaded onto a Pebble paired with the
official Core Devices mobile app.

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
offsets so a hand-edited file stays fresh. For a time-evolving demo
(charging curves, lock/unlock cycles, climate events firing
notifications on the watch), point `DEMO_DATA_FILE` at one of the
scripted files under `proxy/scenarios/` — see `proxy/README.md` →
"Scenario mode".

**Or go live** — set `DATA_SOURCE=live` and add your Kia account to
`.env`:

```
DATA_SOURCE=live
KIA_USERNAME=you@example.com
KIA_PASSWORD=...
KIA_PIN=
KIA_REGION=1     # 1=Europe, 2=Canada, 3=USA, 4=China, 5=Australia
KIA_BRAND=1      # 1=Kia, 2=Hyundai, 3=Genesis
```

Same credentials as the official app — there is no browser bootstrap or
token capture step. Accept any outstanding consent prompt in the Kia app
first, or the first login fails. The refresh token is then cached in
SQLite (`PROXY_STATE_DB`) so restarts don't re-login; the password is
never written there.

Two rate limits apply, and they are not the same thing.
`LIVE_REFRESH_MIN_SECONDS` (600) bounds ordinary reads, which take the
state Kia already holds and leave the car asleep.
`LIVE_FORCE_MIN_SECONDS` (900) bounds the long-press refresh, which
wakes the car and draws on its 12V battery. A long-press inside that
window quietly returns cached data with `forced: false` rather than an
error.

**Pick a push topic** — any guess-hard string (the topic name is the
only access control on a default ntfy install). Add to `.env`:

```
NTFY_TOPIC=kia-<something-random-here>
NTFY_PUBLIC_URL=https://ntfy.example.com
```

**Run it** (still inside `pebble-kia/proxy`):

```sh
docker compose up -d --build        # or: podman compose up -d --build
docker logs -f pebble-kia-proxy     # sanity-check startup, Ctrl-C to detach
docker logs -f pebble-kia-ntfy      # ntfy server in a second shell
```

The compose file brings up two services — the proxy on
`127.0.0.1:8000` and ntfy on `127.0.0.1:2586`. Both are loopback-only;
Caddy fronts both subdomains, so nothing is exposed to the public
internet directly. Swap the binds for `0.0.0.0` only if you plan to
skip the reverse proxy.

**Sanity check** — from the host itself (still in `pebble-kia/proxy`):

```sh
curl -s http://127.0.0.1:8000/health
# {"status":"ok","data_source":"demo"}

TOKEN=$(grep ^PROXY_BEARER_TOKEN .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/vehicles | jq .
```

**Add TLS via Caddy** — drop these blocks into the Caddy config
(see `proxy/Caddyfile.example`) and point DNS at the server:

```
kia-proxy.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}

ntfy.example.com {
    reverse_proxy 127.0.0.1:2586 {
        flush_interval -1
    }
}
```

`caddy reload` and Caddy obtains Let's Encrypt certs automatically.
The phone will subscribe to `https://ntfy.example.com/<your-topic>`
for push notifications; the watchapp will call
`https://kia-proxy.example.com` for data.

**Subscribe the phone to ntfy** — install the ntfy app
(<https://ntfy.sh/> has App Store / Play Store links), then add a
subscription with the URL above. Phone OS notifications from the ntfy
app bridge to the watch automatically via the Pebble mobile app's
notification forwarding; no watchapp-side setup needed.

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

These steps are for a Pebble Time 2 (or Pebble 2 Duo) — Core Devices
hardware. They have not been run end to end on a watch yet; the build and
the emulator path have. Corrections welcome.

**Use the official Core Devices app, not the Rebble one.** Rebble does not
publish a companion app — what `rebble.io/apk` distributes is the 2016
Pebble Technology APK, which Rebble's own help centre describes as no
longer maintained. The app you want is **"Pebble" by Core Devices**
(package `coredevices.coreapp`), on Google Play or via
https://repebble.com/app.

1. **Uninstall the legacy app first if you have it.** Core Devices'
   support docs are explicit that pairing breaks with both installed:
   "You won't be able to pair or use any watches with both the old and
   new apps installed simultaneously."
2. Install "Pebble" by Core Devices and pair the watch. The Devices tab
   should show it connected.
3. Build the bundle. One `.pbw` carries every platform, so the same file
   installs on emery, basalt, diorite and chalk:

   ```sh
   cd pebble
   npm install        # first time only
   pebble build
   ```

Then pick a transport. All three are supported by `pebble install`; the
first needs the least setup.

**Over USB with adb** — nothing to toggle in the phone's UI:

```sh
sudo pacman -S android-tools        # or your distro's platform-tools
adb devices                         # phone must show as "device"
cd pebble && pebble install --adb --logs
```

`pebble` broadcasts a developer-connection intent to the app, forwards
the port it answers with, and installs over it. Needs the Pebble app at
1.10.0 or newer, USB debugging enabled on the phone, and the watch
connected over Bluetooth.

**Over the LAN** — needs two separate toggles, and missing either gives
connection refused:

1. Settings → Phone → Connectivity → enable **Use LAN developer
   connection** (off by default).
2. Devices → the watch's overflow menu → enable **Dev Connection**. It
   then displays an `IPv4:` address. That is the **phone's** address,
   not the watch's — the watch has no IP, and every install goes
   workstation → phone → watch over Bluetooth.

```sh
cd pebble && pebble install --phone <PHONE_IPv4> --logs
```

Port 9000 is the default on both ends. Pass the IP explicitly: bare
`pebble install --phone` is an alias for the CloudPebble relay, not a
LAN install, and fails with "You must be logged in".

**Over the CloudPebble relay** — works off your LAN, at the cost of
signing in on both ends. Enable Dev Connection in the app while signed
in to a Pebble account, leave the LAN toggle off, then:

```sh
pebble login
cd pebble && pebble install --cloudpebble --logs
```

The watch vibrates when the install lands, and Kia appears in the app
launcher.

#### In the emulator

Already covered above. `pebble install --emulator emery` (or `basalt` /
`diorite` / `chalk`) reinstalls any time you rebuild.

#### Getting the token onto the phone

The bearer token is 64 hex characters and typing it on a phone keyboard is
awful. On startup the proxy writes QR codes for it, so you can scan rather
than type.

Set the URL the phone should use in `proxy/.env` — the proxy has no way to
know its own externally-reachable address, so if this is unset it writes
the token code only:

```
PROXY_PUBLIC_URL=https://kia-proxy.example.com
```

Restart the proxy. Two images appear in `proxy/setup/`:

```
proxy/setup/bearer-token.png    the value for the Bearer token field
proxy/setup/proxy-url.png       the value for the Base URL field
```

Open them on the machine running the proxy (`xdg-open proxy/setup/bearer-token.png`),
scan with the phone's ordinary camera app, and paste the recognised text
into the matching field in the watchapp's settings. The camera app copies
the decoded string to the clipboard; there is no scanning built into the
settings page itself, because Pebble serves the Clay config page as a
`data:` URI, which is not a secure context and so cannot open a camera.

Running in Docker on a headless box? The token QR can also go to the
startup log, which is then the only channel you need. It is off by
default, because a QR block in a log *is* the token in machine-readable
form, and logs outlive token rotation, get shipped off the host by
collectors, and end up pasted into issues. Turn it on deliberately:

```
SETUP_QR_LOG=1
```

```sh
sudo docker compose logs proxy | head -40
```

Rotate the token if a log carrying it has gone anywhere you don't control.

`proxy/setup/` is gitignored and written owner-only, because those images
are a live credential in scannable form — treat them exactly like the
`.env` they came from. Anyone who photographs your screen has your token.
They are never served over HTTP.


### 3. Configure from the phone

There is no CLI route to the settings page on real hardware —
`pebble emu-app-config` is emulator-only. It has to come from the phone.

1. Keep the watch connected and nearby.
2. Phone app → Apps → **Kia** → **Settings**. The gear appears because
   `package.json` declares `capabilities: ["configurable"]`, and is
   enabled only while a compatible watch is connected.
3. Clay opens in a WebView. Fill in:
   - **Base URL**: the public HTTPS URL Caddy serves, e.g.
     `https://kia-proxy.example.com`.
   - **Bearer token**: `PROXY_BEARER_TOKEN` from the `proxy/.env` **on
     the Pi**, not a local dev one if those have drifted. Watch for a
     trailing newline when pasting.
4. Tap **Save**.

**Do not leave the default URL.** It is `http://localhost:8000`, which
only ever made sense in the emulator, where the companion JS runs on your
workstation. On a phone, `localhost` is the phone — every request would
die against the handset's own loopback. A LAN address like
`http://192.168.1.20:8000` works while you are at home but breaks the
moment you leave, so prefer the Let's Encrypt hostname. A self-signed
certificate will fail opaquely inside the WebView.

Then verify:

1. Launch Kia on the watch. It should show vehicle data rather than `ERR`
   in the top-right.
2. Tail logs over whichever transport you installed with —
   `pebble logs --adb`, `--phone <ip>`, or `--cloudpebble`. Both the
   watch's `APP_LOG` output and the companion's `[kia] req …` lines come
   through the same stream.
3. Confirm the phone can reach the proxy at all by opening
   `https://<your-host>/health` in the phone's browser. It should return
   `{"status":"ok","data_source":"live"}`. A 401 there means the token
   doesn't match; a timeout means DNS, firewall or Caddy.

## Controls

- **Up / Down** — switch between vehicles returned by the proxy. If the
  newly-selected vehicle has no cached status yet, the watch asks the
  companion to fetch it.
- **Select** — open the detail screen (odometer, outside temp, doors,
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

UK defaults: range and odometer render in miles, outside temp in Celsius,
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
- **Watch shows `Kia needs consent`** — open the official Kia app and
  accept the outstanding consent/terms screen, then retry. The proxy
  can't clear this for you.
- **Watch shows `Kia login failed`** — `KIA_USERNAME` / `KIA_PASSWORD`
  don't work. Confirm them in the official app first. If they work
  there but not here, Kia has probably changed its login flow — try
  `uv sync --upgrade-package hyundai-kia-connect-api`.
- **Watch shows `Kia rate limited`** — too many calls against the
  account. Back off; the intervals in `.env` exist to prevent this.
- **A long-press refresh seems to do nothing** — check `forced` in the
  response. `false` means you were inside `LIVE_FORCE_MIN_SECONDS` and
  got cached data on purpose, which is the intended behaviour rather
  than a fault.
- **A field reads wrong or empty on the watch** — run
  `uv run python tools/dump_vehicle.py` in `proxy/` and compare the
  real payload against the mapping in `app/sources/live.py`. The dump
  redacts VIN and coordinates and lists which fields came back null.
- **Watch sits on `Connecting…` forever** — the companion isn't sending
  its ready nudge, usually because the phone's Bluetooth link to the
  watch is down or the mobile app isn't running. Re-open the app on
  the phone.
- **`pebble install --phone` fails** — developer connection is
  disabled or the watch IP is wrong. Re-check Settings → Developer in
  the mobile app.
- **No push notifications arriving** — subscribe to the topic from any
  host first with `curl -sN https://ntfy.example.com/your-topic/json`
  and trigger a transition (edit the scenario's `at_s` offsets or
  force-refresh). If that works but the phone app doesn't buzz,
  check the ntfy app's subscription URL and the phone's OS
  notification permissions. If the proxy logs "would notify" without
  trying the HTTP push, `NTFY_TOPIC` is unset — push is disabled.

## What's next

The phased plan lives in `DESIGN.md`. Short version:

1. Watchapp with demo data ← **done**
2. Proxy skeleton + end-to-end wiring against demo ← **done**
3. Proxy wired to `hyundai_kia_connect_api`, SQLite persistence, live
   rate limits, PV5 field mappings confirmed against a real dump
   ← **done**
4. Watchapp laid out for the Pebble Time 2 (`emery`) ← **done**
5. UX polish — last-known state persisted to watch storage, 12V
   battery readout, staleness visible alongside errors, vibrate on the
   OK→error edge ← **done**

All planned phases are complete.

## For forkers

Before cloning for your own use, read `DESIGN.md` →
"Operating assumptions" and "Alternative considered: direct mode". The
default design assumes you'll run the proxy on your own home server
(Raspberry Pi + Docker + Caddy works well). If you don't have
home-server infra, a direct phone-to-Kia mode is feasible but involves
more work and trade-offs — the decision is documented there.
