import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Vehicle, VehicleStatus
from .base import VehicleNotFound

_REL = re.compile(r"^-\s*(\d+)\s*([smhd])$")


def _resolve_updated_at(raw) -> datetime:
    """Allow demo-data.json authors to write `updated_at` as:

    - an absolute ISO 8601 timestamp (same as the live source produces),
    - a relative offset like "-2m" / "-90s" / "-1h" so a hand-edited file
      stays fresh no matter when it was last saved,
    - null / missing, which defaults to "just now".

    The wire contract (absolute UTC datetime) is unchanged.
    """
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, str):
        m = _REL.match(raw.strip())
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            delta = {
                "s": timedelta(seconds=amount),
                "m": timedelta(minutes=amount),
                "h": timedelta(hours=amount),
                "d": timedelta(days=amount),
            }[unit]
            return datetime.now(timezone.utc) - delta
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    raise TypeError(f"updated_at must be a string or null, got {type(raw).__name__}")


def _evaluate_scenario(
    baseline: dict,
    events: list[dict],
    loop_seconds: int,
    scenario_start_mono: float,
) -> VehicleStatus:
    """Compute a vehicle's current status by walking the event list.

    Each event has `at_s` (seconds into the loop), an optional `name`, and
    a `patch` dict of status-field overrides. State at time T is the
    baseline with every patch whose `at_s <= T` applied in order.

    `updated_at` is set to the wall-clock time of the most recent applied
    event (or the cycle start if none have fired) so the watch's
    "Xm ago" line mirrors what a real Kia telematics push would look
    like — it changes when the vehicle state changes.
    """
    now_mono = time.monotonic()
    now_wall = datetime.now(timezone.utc)

    elapsed_total = now_mono - scenario_start_mono
    cycle_index = int(elapsed_total // loop_seconds)
    elapsed_in_cycle = elapsed_total - cycle_index * loop_seconds
    cycle_start_wall = now_wall - timedelta(seconds=elapsed_in_cycle)

    sorted_events = sorted(events, key=lambda e: e["at_s"])

    state = dict(baseline)
    last_event_at_s = 0
    for event in sorted_events:
        if event["at_s"] > elapsed_in_cycle:
            break
        state.update(event.get("patch", {}))
        last_event_at_s = event["at_s"]

    state["updated_at"] = cycle_start_wall + timedelta(seconds=last_event_at_s)
    return VehicleStatus(**state)


class DemoDataSource:
    name = "demo"

    def __init__(self, path: Path) -> None:
        self.path = path
        # Monotonic start so scenario time is restart-fresh and immune
        # to wall-clock jumps.
        self._scenario_start_mono = time.monotonic()

    def _load(self) -> dict:
        # Re-read on every call so edits to the file surface on the next
        # forced refresh without restarting the proxy.
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def list_vehicles(self) -> list[Vehicle]:
        data = self._load()
        return [Vehicle(**v) for v in data["vehicles"]]

    def fetch_status(self, vehicle_id: str) -> VehicleStatus:
        data = self._load()
        for v in data["vehicles"]:
            if v["id"] != vehicle_id:
                continue
            scenario = (data.get("scenario") or {}).get("vehicles", {}).get(vehicle_id)
            if scenario is not None:
                return _evaluate_scenario(
                    baseline=scenario["baseline"],
                    events=scenario.get("events", []),
                    loop_seconds=data["scenario"].get("loop_seconds", 3600),
                    scenario_start_mono=self._scenario_start_mono,
                )
            payload = dict(v["status"])
            payload["updated_at"] = _resolve_updated_at(payload.get("updated_at"))
            return VehicleStatus(**payload)
        raise VehicleNotFound(vehicle_id)

    def has_scenario(self) -> bool:
        try:
            return bool(self._load().get("scenario"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
