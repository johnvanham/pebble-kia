from datetime import datetime, timezone

from app.sources.live import map_status


class StubVehicle:
    """Only the attributes map_status() reads, all unset by default.

    Mirrors a library Vehicle whose fields the car never populated, which is
    exactly the sleeping-car case.
    """

    ev_battery_percentage = None
    ev_driving_range = None
    ev_driving_range_unit = None
    ev_battery_is_charging = None
    ev_charging_power = None
    ev_estimated_current_charge_duration = None
    ev_battery_is_plugged_in = None
    is_locked = None
    air_temperature = None
    _air_temperature_unit = None
    odometer = None
    odometer_unit = None
    air_control_is_on = None
    last_updated_at = None
    last_scanned_at = None

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


UPDATED = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def metric_charging(**overrides) -> StubVehicle:
    fields = dict(
        ev_battery_percentage=62,
        ev_driving_range=248.0,
        ev_driving_range_unit="km",
        ev_battery_is_charging=True,
        ev_charging_power=7.4,
        ev_estimated_current_charge_duration=95,
        ev_battery_is_plugged_in=1,
        is_locked=True,
        air_temperature=21.0,
        _air_temperature_unit="°C",
        odometer=1234.5,
        odometer_unit="km",
        air_control_is_on=False,
        last_updated_at=UPDATED,
    )
    fields.update(overrides)
    return StubVehicle(**fields)


def test_nominal_charging():
    status = map_status(metric_charging())

    assert status.soc_pct == 62
    assert status.range_km == 248
    assert status.is_charging is True
    assert status.charge_kw == 7.4
    assert status.charge_eta_min == 95
    assert status.plug == "ac"
    assert status.doors_locked is True
    assert status.cabin_temp_c == 21
    assert status.odo_km == 1234
    assert status.is_climate_on is False
    assert status.updated_at == UPDATED


def test_imperial_normalises_to_metric():
    status = map_status(metric_charging(
        ev_driving_range=100.0,
        ev_driving_range_unit="mi",
        odometer=1000.0,
        odometer_unit="mi",
        air_temperature=68.0,
        _air_temperature_unit="°F",
    ))

    assert status.range_km == 161
    assert status.odo_km == 1609
    assert status.cabin_temp_c == 20


def test_sleeping_car_still_renders():
    status = map_status(StubVehicle())

    assert status.soc_pct == 0
    assert status.range_km == 0
    assert status.is_charging is False
    assert status.charge_kw == 0.0
    assert status.charge_eta_min == 0
    assert status.plug == "unplugged"
    assert status.doors_locked is False
    assert status.odo_km == 0
    assert status.updated_at is not None


def test_falls_back_to_scan_time_when_car_reports_no_timestamp():
    status = map_status(StubVehicle(last_scanned_at=UPDATED))

    assert status.updated_at == UPDATED


def test_plug_unplugged():
    assert map_status(metric_charging(
        ev_battery_is_plugged_in=0,
        ev_battery_is_charging=False,
        ev_charging_power=0.0,
    )).plug == "unplugged"


def test_plug_dc_above_onboard_charger_limit():
    assert map_status(metric_charging(ev_charging_power=118.0)).plug == "dc"


def test_plug_ac_when_plugged_but_not_drawing():
    assert map_status(metric_charging(
        ev_battery_is_charging=False,
        ev_charging_power=0.0,
    )).plug == "ac"


def test_v2l_discharge_does_not_produce_negative_power():
    assert map_status(metric_charging(ev_charging_power=-1.2)).charge_kw == 0.0
