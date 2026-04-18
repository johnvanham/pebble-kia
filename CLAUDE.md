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

Phase 1 is done: watchapp runs against an on-device demo data module
(`pebble/src/c/demo_data.c`). No network code yet. PebbleKit JS and the
proxy are unbuilt / stubbed.

Status table at the top of `README.md` reflects current state; update it
as phases land.

## Repo layout

- `DESIGN.md` — architecture, operating assumptions, phased plan,
  decisions. Authoritative.
- `README.md` — user-facing quickstart (build, emulator, sideload).
- `pebble/` — watchapp. `src/c/` is the C source, `src/pkjs/index.js`
  is the companion stub.
- `proxy/` — will exist once phase 2 starts. Plan is FastAPI + Docker,
  deployed behind the user's existing Raspberry Pi Caddy (automatic TLS
  via Let's Encrypt).

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

- Development branch: `claude/pebble-kia-watch-app-CtLHo`. Push only
  there unless the user explicitly redirects.
- Commit messages so far: imperative subject line, blank line, body
  explaining the *why* (not the what). No trailers, no emojis. Match
  that style.
- Do **not** open pull requests unless explicitly asked.

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
