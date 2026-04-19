import json
import re
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


class DemoDataSource:
    name = "demo"

    def __init__(self, path: Path) -> None:
        self.path = path

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
            if v["id"] == vehicle_id:
                payload = dict(v["status"])
                payload["updated_at"] = _resolve_updated_at(payload.get("updated_at"))
                return VehicleStatus(**payload)
        raise VehicleNotFound(vehicle_id)
