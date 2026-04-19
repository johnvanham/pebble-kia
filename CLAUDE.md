# CLAUDE.md

Context for future Claude sessions picking up this repo.

## What this is

A personal Pebble smartwatch app (C + PebbleKit JS) for viewing Kia
vehicle stats, backed by a self-hosted FastAPI proxy that wraps the
community `hyundai_kia_connect_api` library. Single user (the repo
owner), open source so others can fork, but not run as a hosted
service.

For architecture and the phased plan, read `DESIGN.md` before changing
anything. The proxy-vs-direct-mode decision is recorded there and was
deliberate — don't re-litigate it without being asked.

## Current phase

Phase 2 done: watch → companion → proxy wiring is in place end-to-end
against the demo data source. Watch no longer has compiled-in demo data;
the companion's Clay config page (`pebble/src/pkjs/config.js`) holds
proxy URL + bearer token in `localStorage['clay-settings']`. The proxy
(`proxy/`) has a pluggable data-source layer — the `demo` source reads
an editable `proxy/demo-data.json`; the `live` source is a 501 stub
until phase 3 wires up `hyundai_kia_connect_api`.

Status table at the top of `README.md` reflects current state; update it
as phases land.

## Repo layout

- `DESIGN.md` — architecture, operating assumptions, phased plan,
  decisions. Authoritative.
- `README.md` — user-facing quickstart (build, emulator, sideload).
- `pebble/` — watchapp. `src/c/` is the C source, `src/pkjs/index.js`
  is the companion stub.
- `proxy/` — FastAPI service. `app/sources/` holds the data-source
  layer; `demo-data.json` is the editable sample payload. Dockerfile +
  `docker-compose.yml` + `Caddyfile.example` cover deployment behind
  the user's existing Raspberry Pi Caddy (automatic TLS via Let's
  Encrypt).

## Working on the proxy

Python 3.13 managed by `uv`. Iterate locally with:

```sh
cd proxy
cp .env.example .env   # fill in PROXY_BEARER_TOKEN
uv sync
uv run uvicorn app.main:app --reload
```

Guardrails:

- Never add endpoints that mutate the vehicle (lock, start charging,
  climate). Read-only; commands are a separate risk surface — see
  DESIGN.md "Out of scope".
- The `demo` and `live` sources must expose exactly the same shape.
  When adding a field, extend `app/models.py`, update the demo JSON,
  and leave a clear TODO on the live side if it's not yet
  implementable.
- Cache/rate-limit belong in `app/cache.py`, not in individual sources.
  The 12V drain concern only matters for `live`, but `demo` uses the
  same cache so behaviour is testable without a car.
- Live source is a stub today — raise `LiveNotYetImplemented` rather
  than faking data; it surfaces as HTTP 501 and stays loud.
- The machine this runs on uses Podman, not Docker proper (the `docker`
  binary is the `podman-docker` shim). Dockerfiles work unchanged;
  `docker compose` is alias for `podman compose`. Watch for SELinux
  label issues on bind mounts (`:z` / `:Z`).

## Testing end-to-end in the emulator

Interactive path: `pebble emu-app-config` opens the Clay config page in
the host browser. Fill in URL + token, Save, and the emulator's
localStorage picks up the values.

Scripted path (for headless testing — no browser): pypkjs stores
`localStorage` as a `dbm.dumb` database under
`~/.pebble-sdk/*/basalt/localstorage/<uuid>`. The uuid-specific files
are created the first time the app runs, so `pebble install --emulator
basalt` must happen at least once before the preload. Then:

```sh
python3 - <<'PY'
import dbm.dumb, json, pathlib
uuid = '5b7e9a12-3c4d-4e8f-9a1b-2c3d4e5f6a7b'
ls = next((pathlib.Path.home() / '.pebble-sdk').glob('*/basalt/localstorage'))
db = dbm.dumb.open(str(ls / uuid), 'c')
db['clay-settings'] = json.dumps({
    'PROXY_URL': 'http://localhost:8000',
    'PROXY_TOKEN': 'testtoken123',
})
db.close()
PY
pebble install --emulator basalt     # reinstall to reload localStorage
```

Start the proxy with a matching `PROXY_BEARER_TOKEN` and watch `pebble
logs --emulator basalt`. The companion prints `[kia] req …` lines and
errors surface as `ERR` top-right plus a readable message at the bottom
of the main screen.

Emulator button presses from the CLI:

```sh
pebble emu-button click select              # tap
pebble emu-button click select --duration 700   # long press (≥500ms)
pebble emu-button click down
```

`pebble emu-button --help` and `pebble emu-button` (no args) crash with a
Python-3.13 argparse regression (`ValueError: empty group ...`). The
command itself works; only the usage-printing path is broken. Pass a
valid `action` plus at least one button and it's fine. Valid actions are
`click`, `push` (hold), `release`.

## Working on the watchapp

The Pebble SDK is almost certainly not installed in the sandbox, so you
cannot `pebble build` to verify compilation. Be meticulous reading the
SDK docs rather than guessing API shapes. Known gotchas:

- Never call `window_destroy` inside a window's `unload` handler — it
  recurses. Pattern: destroy child layers in unload, destroy the window
  itself from a top-level `*_deinit` function called out of `main.c`.
- `graphics_draw_text` takes 7 args (ctx, text, font, rect, overflow,
  alignment, attributes).
- Color constants like `GColorIslamicGreen` only exist on color
  platforms — guard with `#ifdef PBL_COLOR`. `GColorBlack` / `GColorWhite`
  are safe everywhere.
- Target platforms in `package.json`: `basalt`, `chalk`, `diorite`,
  `emery`. The owner's daily driver is an existing Pebble Time (basalt)
  with a Pebble Time 2 (emery) on order — keep the app's footprint
  small enough that basalt stays comfortable (24 KB heap).
- `app_state_subscribe` dedupes by function pointer, so it's safe to
  call on each window load.

## Git and commits

- Branch freely. The whole project is developed by Claude, and the
  owner is happy for new sessions to create their own branches rather
  than all piling onto one. Reasonable defaults:
  - Phase-scoped or topic-scoped branches (`claude/<short-topic>`) for
    anything non-trivial, so the history reads phase by phase.
  - Direct commits to `main` are fine for small fixes, docs, and
    status-table updates.
  - Leave whichever branch the harness starts you on as the active
    branch unless there's a reason to cut a new one.
- Commit messages so far: imperative subject line, blank line, body
  explaining the *why* (not the what). No trailers, no emojis. Match
  that style.
- **Push after committing.** On this project the owner has granted
  standing permission to `git push` whenever a commit lands, so the
  default "don't push unless asked" guardrail does not apply here.
  Push to whichever branch you're on (normally `main`). Never force
  push to `main` — if a push is rejected, investigate why.
- Do **not** open pull requests unless explicitly asked.

## Display units

The owner is in the UK, so range and odometer render in **miles** by
default; cabin temp in °C, charge rate in kW. The canonical data stays in
km (matching what the Kia API returns) and conversion happens only at
render time — see `pebble/src/c/units.h` (`PBK_USE_MILES` macro and
`format_distance_km()` helper). When adding a new distance readout, route
it through that helper rather than hard-coding "km". `DESIGN.md` →
"Display units" has the longer rationale.

## Style conventions already in play

- Comments only when the *why* is non-obvious. No docstrings on
  short functions, no narration of what the code does.
- No speculative abstractions, no backwards-compatibility shims, no
  fallback error handling for impossible cases.
- Markdown docs: plain prose, hard-wrap around 78 chars, no emoji, no
  badge soup.

## Things to not do without checking

- Add a second user / multi-tenant anything — explicitly out of scope.
- Add remote commands (lock/unlock, start charging, climate). Read-only
  first; commands are a separate risk surface.
- Replace the proxy with direct phone-to-Kia mode. The decision is in
  `DESIGN.md`; the proxy is reused by Home Assistant and a planned
  dashboard, so it earns its keep beyond the watchapp.
- Introduce CI, pre-commit hooks, linters, or test frameworks without
  asking — none exist yet and the project may be too small to need
  them.

## When picking up next

1. Read `DESIGN.md` "Phased plan" to find the next phase.
2. Ask the user to confirm before starting a new phase — they may
   want to iterate on the current one first.
3. Update the status table in `README.md` and the phased-plan checklist
   in `DESIGN.md` as you complete work.
