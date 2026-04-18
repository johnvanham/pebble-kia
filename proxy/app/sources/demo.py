import json
from pathlib import Path

from ..models import Vehicle, VehicleStatus
from .base import VehicleNotFound


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
                return VehicleStatus(**v["status"])
        raise VehicleNotFound(vehicle_id)
