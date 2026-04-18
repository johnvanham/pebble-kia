from typing import Protocol

from ..models import Vehicle, VehicleStatus


class VehicleNotFound(Exception):
    def __init__(self, vehicle_id: str) -> None:
        super().__init__(vehicle_id)
        self.vehicle_id = vehicle_id


class DataSource(Protocol):
    name: str

    def list_vehicles(self) -> list[Vehicle]: ...

    def fetch_status(self, vehicle_id: str) -> VehicleStatus: ...
