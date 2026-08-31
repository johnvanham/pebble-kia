"""Dump a real vehicle's library state, redacted, for offline inspection.

Run from `proxy/`:

    uv run python tools/dump_vehicle.py

Reads the same .env as the proxy, does one cached-state update per vehicle
(no force refresh, so the car is not woken), and writes
`tools/dump-<timestamp>.json`. The dump is what resolves the two open
questions in `app/sources/live.py`: how the PV5 signals AC vs DC, and which
fields a real car leaves unset.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyundai_kia_connect_api import VehicleManager  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.sources.live import map_status  # noqa: E402

# Redacted wherever they appear in the raw payload. Whole subtrees for the
# coordinate containers, since their inner key names vary by protocol.
SENSITIVE_KEYS = {
    "vin",
    "lat",
    "lon",
    "lng",
    "latitude",
    "longitude",
    "alt",
    "altitude",
    "coord",
    "coords",
    "geocoord",
    "geocode",
    "address",
    "location",
    "vehiclelocation",
}

# Suffixes catch the composed names — location_latitude, geocode_address —
# without the false positives a substring match would bring.
SENSITIVE_SUFFIXES = ("latitude", "longitude", "address", "vin", "coord", "location")

REDACTED = "<redacted>"


def is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return lowered in SENSITIVE_KEYS or lowered.endswith(SENSITIVE_SUFFIXES)


def redact(value):
    if isinstance(value, dict):
        return {
            k: (REDACTED if is_sensitive(k) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def public_attrs(vehicle) -> dict:
    out = {}
    for name in dir(vehicle):
        if name.startswith("_"):
            continue
        value = getattr(vehicle, name)
        if callable(value):
            continue
        out[name] = REDACTED if is_sensitive(name) else redact(value)
    return out


def main() -> None:
    settings = load_settings()
    vm = VehicleManager(
        region=settings.kia_region,
        brand=settings.kia_brand,
        username=settings.kia_username,
        password=settings.kia_password,
        pin=settings.kia_pin,
        language=settings.kia_language,
    )
    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(__file__).resolve().parent / f"dump-{stamp}.json"

    dump = {}
    for vehicle_id, vehicle in vm.vehicles.items():
        dump[vehicle_id] = public_attrs(vehicle)

    # default=str so datetimes and enums serialise; the file is for reading,
    # not for round-tripping.
    out_path.write_text(json.dumps(dump, indent=2, default=str, sort_keys=True))
    print(f"wrote {out_path}")

    for vehicle_id, attrs in dump.items():
        vehicle = vm.vehicles[vehicle_id]
        print()
        print(f"{vehicle.name} ({vehicle.model}) — {vehicle.engine_type}")
        print(f"  ccs2 protocol support: {vehicle.ccu_ccs2_protocol_support}")
        print(f"  plugged in: {vehicle.ev_battery_is_plugged_in!r}"
              f"  charging: {vehicle.ev_battery_is_charging!r}"
              f"  power: {vehicle.ev_charging_power!r}")

        none_fields = sorted(k for k, v in attrs.items() if v is None)
        print(f"  fields reported as None ({len(none_fields)}):")
        for name in none_fields:
            print(f"    {name}")

        print(f"  mapped status: {map_status(vehicle).model_dump_json()}")


if __name__ == "__main__":
    main()
