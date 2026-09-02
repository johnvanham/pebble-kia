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
| Pebble watchapp (C)    | Emery-first layout, touch + button controls, actions menu, launcher icon, instant boot |
| PebbleKit JS companion | Clay config page (proxy URL, token, miles/km toggle)       |
| Self-hosted proxy      | FastAPI, `demo` + `live` sources, coalescing cache, charging-aware refresh, opt-in remote commands |
| Scenario engine        | Time-evolving demos under `proxy/scenarios/`               |
| Live Kia integration   | Real Kia Connect data; SQLite-persisted token and state    |
| HA / dashboard clients | Future                                                     |

The setup guide below runs against a real Kia Connect account. No Kia
login? Everything also runs end to end against bundled demo data —
scenarios replay charge curves, lock cycles and climate events, with no
car and no physical watch. See [Demo mode](#demo-mode).

The layout is tuned for the Pebble Time 2 (`emery`), the only target
with a touchscreen; basalt, diorite and chalk also build.

See [`DESIGN.md`](./DESIGN.md) for architecture, phased plan, operating
assumptions, and the decision record around proxy vs. direct mode.

## How it fits together

```
┌──────────────┐  BT / AppMessage   ┌──────────────────┐   HTTPS   ┌──────────────┐   HTTPS    ┌────────────────┐
│ Pebble watch │ ◀────────────────▶ │ Pebble mobile    │ ◀───────▶ │ Self-hosted  │ ◀────────▶ │ Kia Connect    │
│ (C watchapp) │                    │ app + PebbleKit  │           │ proxy        │            │ EU servers     │
└──────────────┘                    │ JS companion     │           │ (Docker)     │            │ (unofficial)   │
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
  appstore.md      draft store listing (sideloads can't carry one)
  resources/       launcher menu icon
  tools/           icon generator, emulator touch injector
  src/c/           watchapp C source
  src/pkjs/        PebbleKit JS companion + Clay config page
proxy/             FastAPI proxy
  app/
  demo-data.json   editable sample payload
  scenarios/       time-evolving demo payloads
  Dockerfile
  docker-compose.yml
  Caddyfile.example
```

## Setup

Target layout: the proxy runs in a container on a home server, logged
into your Kia account, reachable at an HTTPS URL via a reverse proxy
that owns TLS (Caddy is the default here; the author's Raspberry Pi
already runs it with automatic Let's Encrypt). The watchapp is
sideloaded onto a Pebble paired with the official Core Devices mobile
app.

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

**Add your Kia account** to `.env`:

```
DATA_SOURCE=live
KIA_USERNAME=you@example.com
KIA_PASSWORD='...'
KIA_PIN=
KIA_REGION=1     # 1=Europe, 2=Canada, 3=USA, 4=China, 5=Australia
KIA_BRAND=1      # 1=Kia, 2=Hyundai, 3=Genesis
```

Same credentials as the official app — there is no browser bootstrap or
token capture step. `KIA_PIN` is the PIN the app asks for before a remote
command. Reads work without it; with it empty, every action comes back
502 `Kia error: PIN verification failed`. Accept any outstanding consent prompt in the Kia app
first, or the first login fails. Wrap the password in single quotes if
it contains a `$` or a `#`: `docker compose` reads `.env` for its own
interpolation, so an unquoted `$` silently truncates the password
before the container ever sees it. The refresh token is cached in
SQLite (`PROXY_STATE_DB`) so restarts don't re-login; the password is
never written there.

Two kinds of read reach Kia, and they cost different things. An
ordinary read takes the state Kia already holds and leaves the car
asleep; the proxy serves its own copy of that for
`LIVE_REFRESH_MIN_SECONDS` (600) before asking again. A forced read
wakes the car for a current reading — the watch does one at launch and
on every long-press Select or drag-down — and takes about thirty
seconds on a CCS2 car, because the Kia library waits for the car to
report. There is no floor on forced reads; two arriving together share
one wake. While the car is charging, an ordinary poll landing
`LIVE_CHARGING_REFRESH_SECONDS` (60) after the last wake becomes a wake
itself, so the charge rate and ETA keep moving on their own; add the
wake's own half minute and the numbers change about every minute and a
half. It stops as soon as a read shows the session over.

**Decide whether to enable remote commands.** The watch has an actions
menu (lock/unlock, charging, climate, de-ice, charge port, charge
limits, hazards), but the
proxy ships with the endpoint behind it disabled. The bearer token
otherwise grants only reads, so a leaked token means someone can watch
the car's state — not unlock it. Enabling commands raises that blast
radius, which is why it is a deliberate opt-in rather than a default.
Add to `.env` if you want it:

```
ENABLE_COMMANDS=1
```

On `DATA_SOURCE=live` this also needs `KIA_PIN`, and the proxy refuses
to start without it — a CCS2 car takes every command through a control
token minted from the PIN, so the alternative is a proxy that reads
fine and 502s on the first unlock.

`COMMAND_MIN_SECONDS` (default 10) spaces commands apart; one sent
inside the window is rejected with a 429 rather than queued.

**Run it** (still inside `pebble-kia/proxy`):

```sh
docker compose up -d --build        # or: podman compose up -d --build
docker logs -f pebble-kia-proxy     # sanity-check startup, Ctrl-C to detach
```

The compose file brings up one service, the proxy on `127.0.0.1:8000`.
It is loopback-only; Caddy fronts it, so nothing is exposed to the
public internet directly. Swap the bind for `0.0.0.0` only if you plan
to skip the reverse proxy.

**Sanity check** — from the host itself (still in `pebble-kia/proxy`):

```sh
curl -s http://127.0.0.1:8000/health
# {"status":"ok","data_source":"live"}

TOKEN=$(grep ^PROXY_BEARER_TOKEN .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/vehicles | jq .
```

`/vehicles` should list your real vehicle(s). A "Kia login failed"
error here is a credentials problem — see
[Troubleshooting](#troubleshooting) before blaming anything else.

**Add TLS via Caddy** — drop this block into the Caddy config
(see `proxy/Caddyfile.example`) and point DNS at the server:

```
kia-proxy.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

`caddy reload` and Caddy obtains a Let's Encrypt cert automatically.
The watchapp will call `https://kia-proxy.example.com` for data.

Notifications (charging started, charge complete, locked, and so on)
are not the proxy's job: the official Kia app already sends them to
the phone, and the Pebble mobile app bridges phone notifications to
the watch. Keep the Kia app installed and its notifications on.

**Write the setup QR codes** — the bearer token is 64 hex characters
and typing it on a phone keyboard is awful. On startup the proxy writes
QR codes for it, so you can scan rather than type.

Set the URL the phone should use in `proxy/.env` — the proxy has no way
to know its own externally-reachable address, so if this is unset it
writes the token code only:

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

### 2. Build and install the watchapp

On a workstation with the Pebble SDK installed (see
[Demo mode](#demo-mode) for the SDK install commands):

```sh
cd pebble
npm install
pebble build
# artefact: build/pebble.pbw
```

#### On a physical watch

These steps are for a Pebble Time 2 (or Pebble 2 Duo) — Core Devices
hardware.

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
launcher with its own menu icon.

#### In the emulator

`pebble install --emulator emery` (or `basalt` / `diorite` / `chalk`)
reinstalls any time you rebuild. See [Demo mode](#demo-mode) for the
full emulator flow.

### 3. Configure from the phone

There is no CLI route to the settings page on real hardware —
`pebble emu-app-config` is emulator-only. It has to come from the phone.

1. Keep the watch connected and nearby.
2. Phone app → Apps → **Kia** → **Settings**. The gear appears because
   `package.json` declares `capabilities: ["configurable"]`, and is
   enabled only while a compatible watch is connected.
3. Clay opens in a WebView. Scan the QR codes from step 1 with the
   phone's camera app rather than typing, and fill in:
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

### 4. Verify

1. Launch Kia on the watch. It should show vehicle data rather than `ERR`
   in the top-right. Launch shows the proxy's copy within a second and
   then wakes the car; the spinner runs for about thirty seconds and
   the numbers should then match the official app.
2. Tail logs over whichever transport you installed with —
   `pebble logs --adb`, `--phone <ip>`, or `--cloudpebble`. Both the
   watch's `APP_LOG` output and the companion's `[kia] req …` lines come
   through the same stream.
3. Confirm the phone can reach the proxy at all by opening
   `https://<your-host>/health` in the phone's browser. It should return
   `{"status":"ok","data_source":"live"}`. A 401 there means the token
   doesn't match; a timeout means DNS, firewall or Caddy.

## Controls

Opening the app shows what the watch remembered, then what the proxy
holds, then asks the car itself: the first status reply each launch is
answered with a forced refresh, which wakes the car and takes about
thirty seconds. If that first reply already came from the car — the
proxy wakes a charging vehicle of its own accord — the watch skips its
own wake instead of spending another half minute on it. While the app
is open the companion polls the proxy every 15 seconds; that only
reaches Kia every `LIVE_REFRESH_MIN_SECONDS`, except while the car is
charging, when the proxy turns the poll into a wake and the readings
move about every minute and a half until the session ends.

### Touch (Pebble Time 2 / emery)

The Pebble Time 2 is the only target with a touchscreen.

- **Drag down** (main or detail) — refresh from the car: wakes it, same
  as long-press Select. Every pull wakes; two pulls inside one wake
  share it.
- **Swipe left** (main screen) — open the detail screen.
- **Swipe left** (detail screen) — open the actions menu.
- **Swipe right** (detail screen) — back to the main screen.
- **Swipe right** (main screen) — quit the app.

The actions menu itself is a native menu, so touch works on it
directly: drag to scroll, tap a row to trigger it, swipe right to go
back.

### Buttons (all platforms)

The only controls on basalt, diorite and chalk.

- **Up / Down** — on the main screen, switch vehicle. Only does
  anything when the account has more than one vehicle; with a single
  car it no longer triggers a phantom refresh and spinner. On the
  detail screen they scroll.
- **Select** — on the main screen, open the detail screen; on the
  detail screen, open the actions menu.
- **Select (long press, ≥500ms)** — refresh the current vehicle from
  the car, with a short vibration. Takes about thirty seconds; the
  spinner runs meanwhile.
- **Back** — return to the previous screen or exit the app.

The detail screen scrolls: door/lock state with a count of anything
open (doors, windows, trunk, hood, sunroof), outside temp, which
heaters are running, 12V battery, AC and DC charge limits and the
range predicted at each, V2L (the discharge rate when something is
plugged into the socket, otherwise the floor it stops at), whether the
pack is being conditioned, charge rate and ETA while charging,
efficiency, battery temperature and odometer.

### Actions menu

The menu lists lock, unlock, charging start/stop, climate start/stop,
de-ice, charge limits, charge port open/close and hazards. De-ice is
the climate preset with the windscreen, rear-window and steering-wheel
heaters on. Hazards flash for thirty seconds and stop by themselves.
Charge limits open a small picker: Up/Down set the highlighted
percentage, Select moves on to the next field and then sends, and
because Kia writes the AC and DC targets together the picker always
sends both. Risky ones — unlock, stopping a charge — ask for
confirmation before anything is sent. "Sent" means the proxy accepted the command and
handed it to Kia, not that the car has finished acting on it: the car
takes a few seconds, and the app re-reads state on its own shortly
after, so the display catches up without any extra taps. The menu
needs `ENABLE_COMMANDS=1` on the proxy (see [Setup](#setup)); without
it the watch shows `Commands disabled`.

While a request is in flight the watch shows a small spinner top-right.
Errors surface in the bottom status line so the user can read what went
wrong without digging into logs. On a first-ever launch with no
companion the watch sits on a "Connecting…" screen; after any
successful session it restores the last-known state from watch storage
instantly, with the age line showing how old those numbers are.

## Display units

UK defaults: range and odometer render in miles, outside temp in Celsius,
charge rate in kW. Data is transported and cached in km end-to-end; the
watch converts on the fly. Switch between miles and km with the toggle
in the watchapp's settings page on the phone — no rebuild needed.
`PBK_USE_MILES_DEFAULT` in `pebble/src/c/units.h` only decides what a
fresh install shows before that toggle has been seen once.

## Demo mode

Everything runs on one machine against bundled sample data — no Kia
account, no phone, no physical watch. Useful for iterating on the
watchapp UI or exercising the proxy.

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
# terminal 1 — start the proxy (demo is the default data source)
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

**Edit the data** — `proxy/demo-data.json` is the static payload the
`demo` source re-reads on every fetch, so edits show up on the next
refresh. `updated_at` accepts `"-2m"`-style relative offsets so a
hand-edited file stays fresh no matter when it was last saved.

**Exercise a scenario** (time-evolving demo):

```sh
# stop the static proxy, then re-run pointing at a scenario
DEMO_DATA_FILE=scenarios/pv5-rapid-charge.json uv run uvicorn app.main:app --port 8000
```

The watch's 15-second poll picks the progression up as it happens —
plug-in, the charge curve tapering, unplug, climate, lock — since the
demo cache window is only five seconds. See `proxy/README.md` →
"Scenario mode" for the file format and the shipped scenarios.

Touch gestures can be exercised in the emery emulator too: from
`pebble/`, start it with `pebble install --emulator emery --vnc` and
drive the touchscreen with `python3 tools/vnc_touch.py` (swipe and drag
recipes in the script header). Pass `--vnc` to every later pebble
command in that session — a command without it respawns the emulator
without VNC and kills the running app. Buttons cover everything else —
see [Controls](#controls).

## Updating

**Watchapp** — `cd pebble && pebble build && pebble install --phone <IP>`
(or `--emulator emery`). The Clay config persists across installs.

**Proxy** — on the home server, pull the new code and restart:

```sh
cd proxy
git pull
docker compose up -d --build --remove-orphans
```

Clients re-fetch on the next request; no watch-side restart needed.

Coming from a version before the notification pipeline was removed?
`--remove-orphans` stops the old `pebble-kia-ntfy` container, but two
leftovers need a hand: delete the `ntfy.example.com` block from your
Caddyfile (Caddy keeps renewing a certificate for it and answers 502
otherwise), and drop the volume with
`docker volume rm proxy_ntfy-data` once you no longer want its cache.

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
  don't work. Confirm them in the official app first. **If they work in
  the app and locally but fail under Docker, wrap the password in single
  quotes in `.env`.** `docker compose` reads that file for its own
  interpolation and treats `$NAME` as a variable reference, so an
  unquoted `$` truncates the password before the container ever sees it;
  compose warns about an unset variable, which reads like noise. Single
  quotes are literal to both compose and pydantic-settings. If the
  credentials work everywhere and still fail, Kia has probably changed
  its login flow — try `uv sync --upgrade-package hyundai-kia-connect-api`.
- **Watch shows `Kia is rate limiting this account`** — too many calls
  against the account. Back off. Every pull wakes the car and a
  charging session being watched wakes it about every minute and a
  half; raising `LIVE_CHARGING_REFRESH_SECONDS` in `.env` is the lever
  if this recurs.
- **A refresh (long-press or drag-down) takes ages** — that is what a
  wake costs: the Kia library triggers it, waits about 25 seconds, then
  reads the result. The spinner runs the whole time. `Proxy timed out`
  after a full minute means the wake outlasted the companion's
  patience, usually a library retry or a second vehicle's wake ahead of
  it in the queue. The proxy finishes anyway, so the fresh numbers
  arrive with the next poll.
- **Watch shows `Commands disabled`** — the actions endpoint is off,
  which is the shipped default. Set `ENABLE_COMMANDS=1` in `proxy/.env`
  and restart the proxy.
- **An action seems to do nothing** — two benign causes. Repeating a
  command inside `COMMAND_MIN_SECONDS` gets a 429 from the proxy, which
  the watch surfaces in the status line. And a command that *was*
  accepted takes the car a few seconds to act on; the app re-reads
  state automatically shortly after, so give the display a moment to
  catch up before retrying.
- **Data looks stale right after launch** — the first thing on screen
  is the watch's own last copy, then the proxy's; the car's answer
  lands about thirty seconds in, once the launch wake completes. If the
  age line still doesn't move, the car never answered the wake — no
  signal, or parked somewhere without any. That is not reported as an
  error, because Kia's API doesn't report it either; the proxy log
  carries a warning and the age line is the honest reading.
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
- **No charging or lock notifications on the watch** — those come
  from the official Kia app, not from this project. Check the Kia
  app's notification settings on the phone and that the Pebble app is
  allowed to forward that app's notifications.

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
6. Touch controls on emery, always-fresh launch reads (superseded by
   the launch wake in phase 8), launcher icon and store-listing prep
   ← **done**
7. Opt-in remote commands (`ENABLE_COMMANDS`) with a watch actions
   menu and confirm steps, plus a scrollable detail screen with charge
   limits, a summary of anything open, efficiency and battery
   temperature ← **done**
8. Refresh model rework — every pull and launch wakes the car, wakes
   coalesce, charging sessions refresh on their own — and the ntfy
   notification pipeline removed in favour of the Kia app's own
   ← **done**

All eight phases are complete.

## For forkers

Before cloning for your own use, read `DESIGN.md` →
"Operating assumptions" and "Alternative considered: direct mode". The
default design assumes you'll run the proxy on your own home server
(Raspberry Pi + Docker + Caddy works well). If you don't have
home-server infra, a direct phone-to-Kia mode is feasible but involves
more work and trade-offs — the decision is documented there.
