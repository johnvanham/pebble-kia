# Appstore listing draft

Draft copy and asset checklist for a future Rebble / Core Devices
appstore submission, so publishing is a fill-in job when the time
comes. Nothing here is submitted yet.

## Title

Kia

## Category

Tools & Utilities (watchapp)

## Short description

Glanceable stats for your Kia EV: charge, range, doors and charging
status, straight from the wrist.

## Long description

Your car's current state on the wrist: battery percentage, range,
charging status with rate and time-to-full, door locks, climate,
outside temperature and odometer. Opening the app and pulling down
both ask the car itself rather than showing whatever Kia's servers
last heard, and while the car is charging the numbers keep moving on
their own. Read-only by default; remote actions (lock/unlock,
charging, climate, de-ice, charge limits, hazards) are available only
when the self-hosted
proxy explicitly enables them. It talks to a small self-hosted proxy
(FastAPI, source in the same repo) rather than to Kia directly, so you
need a Kia Connect account and somewhere to run the proxy — a
Raspberry Pi is plenty. Buttons work everywhere, and on Pebble Time 2
you can also pull down to refresh, swipe left for detail and swipe
right to go back or quit. Runs on Pebble Time 2, Pebble Time, Pebble
Time Round and Pebble 2.

## Asset checklist

What the Rebble submission flow asks for, per
https://help.rebble.io/appstore-submission/ and
https://developer.rebble.io/guides/appstore-publishing/preparing-a-submission/
(checked 2026-09-01; neither page publishes exact pixel dimensions,
so confirm sizes in the dev portal at submission time):

- Large icon and small icon (watchapps only). The launcher badge in
  `resources/images/menu_icon.png` is the starting point; regenerate
  larger renders with `tools/gen_menu_icon.py`.
- Marketing banner, required for apps (optional only for watchfaces).
- Screenshots: at least one, up to five per platform, PNG or GIF, and
  at least one for each supported platform. `pebble screenshot`
  captures native sizes: 200x228 (emery), 144x168 (basalt, diorite),
  180x180 (chalk).
- Per-platform description, maximum 1600 characters.
- One release `.pbw` built with a non-beta SDK and a unique UUID.
- Base info: app name, category, optional website and source URLs,
  Developer ID (assigned on first submission).
- Process: fill in rebble.io/submit, generate the zip bundle, email
  it to support@rebble.io for publishing.

## Trademark note

The KN mark is a Kia trademark (the rendition itself is below the
threshold of originality, but trademark is separate from copyright).
Fine for a personal sideload; a public listing may need a neutral
icon variant. Fallback: the same black rounded badge with a plain
bold white K.
