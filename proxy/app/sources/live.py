"""Live Kia Connect data source.

Wraps `hyundai_kia_connect_api`. Library exceptions are deliberately not
caught here — `main.py` maps them onto HTTP statuses the companion can
render.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from hyundai_kia_connect_api import ClimateRequestOptions, Token, VehicleManager
from hyundai_kia_connect_api.Vehicle import Vehicle as KiaVehicle
from hyundai_kia_connect_api.const import LENGTH_MILES, TEMPERATURE_F
from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    AuthenticationOTPRequired,
    ConsentRequiredError,
)
from hyundai_kia_connect_api.utils import get_child_value

from ..config import Settings
from ..models import PlugState, Vehicle, VehicleStatus
from ..store import TOKEN_SECRET_KEYS, StateStore
from .base import VehicleNotFound

log = logging.getLogger(__name__)

MILES_TO_KM = 1.609344

# Raw unit code in Drivetrain.FuelSystem.AverageFuelEconomy.Unit. 6 is
# what this account reports with the head unit set to miles, i.e.
# mi/kWh; anything else is taken as already km/kWh, mirroring how
# ev_driving_range_unit is handled.
ECONOMY_UNIT_MILES = 6

_DOOR_OPEN_FIELDS = (
    "front_left_door_is_open",
    "front_right_door_is_open",
    "back_left_door_is_open",
    "back_right_door_is_open",
)
_WINDOW_OPEN_FIELDS = (
    "front_left_window_is_open",
    "front_right_window_is_open",
    "back_left_window_is_open",
    "back_right_window_is_open",
)

# Above this the charge can only be DC. See _derive_plug().
AC_MAX_KW = 15.0

# A sleeping or partially-reporting car leaves fields None, and every poll
# would otherwise repeat the same warnings. One line per field per process
# is enough to notice a field that never arrives.
_missing_logged: set[str] = set()


def _missing(field: str) -> None:
    if field not in _missing_logged:
        _missing_logged.add(field)
        log.warning("vehicle did not report %s; using fallback", field)


def _to_km(value: float | None, unit: str | None, field: str) -> int:
    if value is None:
        _missing(field)
        return 0
    km = value * MILES_TO_KM if unit == LENGTH_MILES else value
    return max(0, round(km))


def _to_celsius(value: float | None, unit: str | None, field: str) -> int:
    if value is None:
        _missing(field)
        return 0
    c = (value - 32) * 5 / 9 if unit == TEMPERATURE_F else value
    return round(c)


def _count_open(vehicle: KiaVehicle, fields: tuple[str, ...]) -> int:
    # None and 0 both mean "not open"; a car that never reported a
    # field shouldn't count as ajar.
    return sum(bool(getattr(vehicle, field)) for field in fields)


def _derive_plug(vehicle: KiaVehicle) -> PlugState:
    """Best available AC/DC derivation — pending confirmation on a real PV5.

    CCS2 (what the PV5 speaks) gives no connector *type*: the library sets
    `ev_battery_is_plugged_in` from
    `Green.ChargingInformation.ConnectorFastening.State`, which only says a
    connector is latched. Charging power is therefore the only discriminator
    available: the PV5's onboard AC charger tops out at 11 kW, so sustained
    power well above that has to be DC.

    Consequences to check against `tools/dump_vehicle.py` output:
    - plugged in but not yet drawing power reads as "ac", because a home AC
      charger is by far the likelier case for a parked car;
    - the first seconds of a DC session, before power ramps, read as "ac".

    If a real dump turns out to carry an explicit connector type in
    `vehicle.data`, use that instead of this heuristic.
    """
    if not vehicle.ev_battery_is_plugged_in:
        return "unplugged"
    power = vehicle.ev_charging_power
    if power is not None and power > AC_MAX_KW:
        return "dc"
    return "ac"


def map_status(vehicle: KiaVehicle) -> VehicleStatus:
    """Library Vehicle -> wire model, normalised to km and °C.

    Distances and temperatures arrive in whatever unit the car's display is
    set to; the wire contract is metric end to end and the watch converts at
    render time.
    """
    soc = vehicle.ev_battery_percentage
    if soc is None:
        _missing("ev_battery_percentage")
        soc = 0

    charging = vehicle.ev_battery_is_charging
    if charging is None:
        _missing("ev_battery_is_charging")
        charging = False

    # Negative power is V2L discharge, which this model has no field for.
    power = max(0.0, vehicle.ev_charging_power or 0.0)

    eta = vehicle.ev_estimated_current_charge_duration
    if eta is None:
        _missing("ev_estimated_current_charge_duration")
        eta = 0

    locked = vehicle.is_locked
    if locked is None:
        _missing("is_locked")
        # Reporting "locked" on no evidence could send the owner away from an
        # unlocked car; the reverse only costs a needless check.
        locked = False

    aux = vehicle.car_battery_percentage
    if aux is None:
        _missing("car_battery_percentage")
        aux = 0

    climate = vehicle.air_control_is_on
    if climate is None:
        _missing("air_control_is_on")
        climate = False

    updated_at = vehicle.last_updated_at or vehicle.last_scanned_at
    if updated_at is None:
        _missing("last_updated_at")
        updated_at = datetime.now(timezone.utc)

    data = vehicle.data or {}

    # The library's sunroof_is_open truthies the raw CCS2 value, and
    # Body.Sunroof.Glass.Open reads 2 on this PV5 while the sunroof is
    # closed — so the accessor claims open on a closed roof. Only 1
    # means open; read the raw node instead. get_child_value returns
    # None wherever the node is absent, so a sunroof-less car reads
    # closed.
    sunroof_open = get_child_value(data, "Body.Sunroof.Glass.Open") == 1

    # AverageFuelEconomy has no library accessor, so it comes straight
    # from the raw payload, normalised to km/kWh like every distance.
    eff = get_child_value(
        data, "Drivetrain.FuelSystem.AverageFuelEconomy.Drive"
    )
    if eff is None:
        efficiency = 0.0
    else:
        eff_unit = get_child_value(
            data, "Drivetrain.FuelSystem.AverageFuelEconomy.Unit"
        )
        efficiency = float(eff) * (
            MILES_TO_KM if eff_unit == ECONOMY_UNIT_MILES else 1.0
        )

    # Kia gives no single "conditioning" flag we can trust the encoding
    # of, so take the union of the three nodes that each say the pack is
    # being worked on: the runtime flag itself (1 = on, the same 0/1/2
    # convention the defog nodes use), the heater, and the chiller.
    # Together they cover both directions regardless of which one this
    # car happens to populate.
    conditioning = (
        get_child_value(data, "Green.BatteryManagement.BatteryConditioning") == 1
        or bool(vehicle.ev_battery_heating_state)
        or bool(get_child_value(data, "Green.BatteryManagement.ChillerRPM"))
    )

    return VehicleStatus(
        soc_pct=min(100, max(0, round(soc))),
        range_km=_to_km(
            vehicle.ev_driving_range,
            vehicle.ev_driving_range_unit,
            "ev_driving_range",
        ),
        is_charging=bool(charging),
        charge_kw=power,
        charge_eta_min=max(0, round(eta)),
        plug=_derive_plug(vehicle),
        doors_locked=bool(locked),
        # Outside, not cabin: the PV5 reports no measured cabin temperature.
        # `Cabin.HVAC.Row1.Driver.Temperature.Value` is the climate setpoint
        # and reads 'OFF' with the climate off, which the library declines to
        # map, leaving `air_temperature` None forever. Outside temperature is
        # a real reading the car always carries.
        outside_temp_c=_to_celsius(
            vehicle.outside_temperature,
            # No public accessor for this one, unlike the two distances.
            vehicle._outside_temperature_unit,
            "outside_temperature",
        ),
        odo_km=_to_km(vehicle.odometer, vehicle.odometer_unit, "odometer"),
        aux_battery_pct=min(100, max(0, round(aux))),
        is_climate_on=bool(climate),
        charge_limit_ac=min(100, max(0, vehicle.ev_charge_limits_ac or 0)),
        charge_limit_dc=min(100, max(0, vehicle.ev_charge_limits_dc or 0)),
        doors_open=_count_open(vehicle, _DOOR_OPEN_FIELDS),
        windows_open=_count_open(vehicle, _WINDOW_OPEN_FIELDS),
        trunk_open=bool(vehicle.trunk_is_open),
        hood_open=bool(vehicle.hood_is_open),
        sunroof_open=sunroof_open,
        efficiency_kmpkwh=efficiency,
        defrost_on=bool(vehicle.defrost_is_on),
        rear_defrost_on=bool(vehicle.back_window_heater_is_on),
        wheel_heat_on=bool(vehicle.steering_wheel_heater_is_on),
        batt_conditioning=conditioning,
        v2l_limit_pct=min(100, max(0, vehicle.ev_v2l_discharge_limit or 0)),
        # The other half of the reading `power` above truncates away.
        v2l_kw=max(0.0, -(vehicle.ev_charging_power or 0.0)),
        target_range_ac_km=_to_km(
            vehicle.ev_target_range_charge_AC,
            vehicle.ev_target_range_charge_AC_unit,
            "ev_target_range_charge_AC",
        ),
        target_range_dc_km=_to_km(
            vehicle.ev_target_range_charge_DC,
            vehicle.ev_target_range_charge_DC_unit,
            "ev_target_range_charge_DC",
        ),
        # None stays None: 0 C is a real reading, so a car that reports
        # no pack temperature must not be dressed up as a freezing one.
        batt_temp_c=None if vehicle.ev_battery_temperature_max is None
        else _to_celsius(
            vehicle.ev_battery_temperature_max,
            vehicle.ev_battery_temperature_max_unit,
            "ev_battery_temperature_max",
        ),
        updated_at=updated_at,
    )


class LiveDataSource:
    name = "live"

    def __init__(self, settings: Settings, store: StateStore) -> None:
        self._settings = settings
        self._store = store
        # VehicleManager holds mutable session state and FastAPI runs the
        # sync route handlers in a threadpool, so every touch is serialised.
        self._lock = threading.RLock()
        self._vm: VehicleManager | None = None
        self._token_from_store = False
        # What was last written to the store, minus the secrets the
        # store strips, so a change can be noticed by value — see
        # _persist_token.
        self._saved_token: dict | None = None

    def list_vehicles(self) -> list[Vehicle]:
        with self._lock:
            vm = self._manager()
            return [
                Vehicle(id=v.id, vin=v.VIN, nickname=v.name, model=v.model)
                for v in vm.vehicles.values()
            ]

    def fetch_status(self, vehicle_id: str, *, force: bool = False) -> VehicleStatus:
        with self._lock:
            vm = self._manager()
            if vehicle_id not in vm.vehicles:
                raise VehicleNotFound(vehicle_id)
            if force:
                # Wakes the car. For a CCS2 car the library triggers the
                # wake, sleeps a fixed 25 s, then reads Kia's snapshot —
                # so this holds the lock for that long, and cache.py
                # coalesces anything that arrives meanwhile.
                before = vm.vehicles[vehicle_id].last_updated_at
                vm.force_refresh_vehicle_state(vehicle_id)
                after = vm.vehicles[vehicle_id].last_updated_at
                if after == before:
                    # The library discards the wake trigger's response
                    # and sleeps a fixed interval, so a car that never
                    # answered is indistinguishable from a successful
                    # wake except by its report time not moving. The
                    # watch shows that age, but say so in the log too.
                    log.warning(
                        "forced refresh did not advance the vehicle's report "
                        "time; the car may not have answered"
                    )
            else:
                vm.update_vehicle_with_cached_state(vehicle_id)
            self._persist_token(vm)
            return map_status(vm.vehicles[vehicle_id])

    def perform_action(
        self, vehicle_id: str, action: str, params: dict[str, int] | None = None
    ) -> None:
        with self._lock:
            vm = self._manager()
            if vehicle_id not in vm.vehicles:
                raise VehicleNotFound(vehicle_id)
            if action in ("start_climate", "start_defrost"):
                # Fixed preset: the watch has no UI for picking a
                # temperature, and 21°C for 10 minutes is a sensible
                # pre-departure warm-up/cool-down either way. Defrost
                # adds every de-icing surface the CCS2 payload carries —
                # heating=1 is what the library turns into
                # sideRearMirrorHeating, and it drives the rear window
                # with the mirrors.
                deice = action == "start_defrost"
                vm.start_climate(
                    vehicle_id,
                    ClimateRequestOptions(
                        set_temp=21.0,
                        duration=10,
                        defrost=deice,
                        heating=1 if deice else 0,
                        steering_wheel=1 if deice else 0,
                    ),
                )
                self._persist_token(vm)
                return
            if action == "set_charge_limit":
                if params is None:
                    raise ValueError("set_charge_limit needs ac and dc")
                vm.set_charge_limits(vehicle_id, params["ac"], params["dc"])
                self._persist_token(vm)
                return
            method = {
                "lock": vm.lock,
                "unlock": vm.unlock,
                "start_charge": vm.start_charge,
                "stop_charge": vm.stop_charge,
                "stop_climate": vm.stop_climate,
                "open_charge_port": vm.open_charge_port,
                "close_charge_port": vm.close_charge_port,
                # 30 seconds of flashing, then the car stops on its own —
                # the library has no matching "off".
                "hazard_lights": vm.start_hazard_lights,
            }.get(action)
            if method is None:
                raise ValueError(f"unknown action: {action}")
            method(vehicle_id)
            self._persist_token(vm)

    def _manager(self) -> VehicleManager:
        if self._vm is None:
            self._vm = self._build_manager(self._stored_token())
        vm = self._vm
        try:
            self._refresh_token(vm)
        except (AuthenticationOTPRequired, ConsentRequiredError):
            raise
        except AuthenticationError:
            if not self._token_from_store:
                raise
            # A persisted token Kia has since invalidated would otherwise
            # wedge every request until state.db is deleted by hand.
            log.warning("stored Kia token rejected, logging in fresh")
            self._vm = vm = self._build_manager(None)
            self._refresh_token(vm)
        return vm

    def _build_manager(self, token: Token | None) -> VehicleManager:
        self._token_from_store = token is not None
        s = self._settings
        return VehicleManager(
            region=s.kia_region,
            brand=s.kia_brand,
            username=s.kia_username,
            password=s.kia_password,
            pin=s.kia_pin,
            language=s.kia_language,
            token=token,
        )

    def _refresh_token(self, vm: VehicleManager) -> None:
        # check_and_refresh_token() logs in when there is no token, refreshes
        # an expired one, and populates vm.vehicles in either case — so it is
        # the only call needed before an update.
        vm.check_and_refresh_token()
        self._persist_token(vm)

    def _persist_token(self, vm: VehicleManager) -> None:
        """Write the token whenever its contents changed.

        Comparing by identity is not enough: the library swaps in a new
        Token on a refresh but also mutates the existing one in place —
        `_retry_on_device_id_error` rewrites `device_id` after Kia
        invalidates it, and a device id that never reaches disk costs
        every restart another rejected call and retry.
        """
        if vm.token is None:
            return
        current = vm.token.to_dict()
        snapshot = {k: v for k, v in current.items() if k not in TOKEN_SECRET_KEYS}
        if snapshot == self._saved_token:
            return
        self._store.save_token(current)
        self._saved_token = snapshot
        self._token_from_store = True

    def _stored_token(self) -> Token | None:
        stored = self._store.load_token()
        if stored is None:
            return None
        # The store strips password and pin; the library needs both to
        # re-login and to mint a control token.
        stored["password"] = self._settings.kia_password
        stored["pin"] = self._settings.kia_pin
        return Token.from_dict(stored)
