from typing import Protocol

from ..models import Vehicle, VehicleStatus


class VehicleNotFound(Exception):
    def __init__(self, vehicle_id: str) -> None:
        super().__init__(vehicle_id)
        self.vehicle_id = vehicle_id


# Every remote command the proxy can relay. The name is the wire
# contract — identical on the watch, in the companion, and in the URL.
ACTIONS = (
    "lock",
    "unlock",
    "start_charge",
    "stop_charge",
    "start_climate",
    "stop_climate",
    "open_charge_port",
    "close_charge_port",
    "start_valet",
    "stop_valet",
)


class DataSource(Protocol):
    name: str

    def list_vehicles(self) -> list[Vehicle]: ...

    # force=True means "wake the vehicle"; False reads whatever the
    # upstream already has cached.
    def fetch_status(
        self, vehicle_id: str, *, force: bool = False
    ) -> VehicleStatus: ...

    # Raises VehicleNotFound for an unknown vehicle and ValueError for
    # an action not in ACTIONS.
    def perform_action(self, vehicle_id: str, action: str) -> None: ...
