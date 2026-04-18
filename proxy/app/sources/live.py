from ..models import Vehicle, VehicleStatus


class LiveNotYetImplemented(RuntimeError):
    pass


class LiveDataSource:
    name = "live"

    def list_vehicles(self) -> list[Vehicle]:
        raise LiveNotYetImplemented(
            "Live Kia integration is not implemented yet — phase 3. "
            "Set DATA_SOURCE=demo.")

    def fetch_status(self, vehicle_id: str) -> VehicleStatus:
        raise LiveNotYetImplemented(
            "Live Kia integration is not implemented yet — phase 3. "
            "Set DATA_SOURCE=demo.")
