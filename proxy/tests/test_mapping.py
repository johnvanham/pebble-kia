from datetime import datetime, timezone

import pytest

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
    outside_temperature = None
    _outside_temperature_unit = None
    odometer = None
    odometer_unit = None
    car_battery_percentage = None
    air_control_is_on = None
    last_updated_at = None
    last_scanned_at = None
    ev_charge_limits_ac = None
    ev_charge_limits_dc = None
    front_left_door_is_open = None
    front_right_door_is_open = None
    back_left_door_is_open = None
    back_right_door_is_open = None
    front_left_window_is_open = None
    front_right_window_is_open = None
    back_left_window_is_open = None
    back_right_window_is_open = None
    trunk_is_open = None
    hood_is_open = None
    ev_battery_temperature_max = None
    ev_battery_temperature_max_unit = None
    defrost_is_on = None
    back_window_heater_is_on = None
    steering_wheel_heater_is_on = None
    ev_battery_heating_state = None
    ev_v2l_discharge_limit = None
    ev_target_range_charge_AC = None
    ev_target_range_charge_AC_unit = None
    ev_target_range_charge_DC = None
    ev_target_range_charge_DC_unit = None
    data = None

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
        outside_temperature=21.0,
        _outside_temperature_unit="°C",
        odometer=1234.5,
        odometer_unit="km",
        car_battery_percentage=83,
        air_control_is_on=False,
        last_updated_at=UPDATED,
        ev_charge_limits_ac=80,
        ev_charge_limits_dc=100,
        ev_battery_temperature_max=15,
        ev_battery_temperature_max_unit="°C",
        # Raw values off the owner's real PV5 dump: sunroof closed reads
        # 2, and economy unit 6 is mi/kWh.
        data={
            "Body": {"Sunroof": {"Glass": {"Open": 2}}},
            "Drivetrain": {"FuelSystem": {
                "AverageFuelEconomy": {"Drive": 3.2, "Unit": 6},
            }},
        },
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
    assert status.outside_temp_c == 21
    assert status.odo_km == 1234
    assert status.aux_battery_pct == 83
    assert status.is_climate_on is False
    assert status.charge_limit_ac == 80
    assert status.charge_limit_dc == 100
    assert status.doors_open == 0
    assert status.windows_open == 0
    assert status.trunk_open is False
    assert status.hood_open is False
    assert status.sunroof_open is False
    assert status.efficiency_kmpkwh == pytest.approx(5.15, abs=0.01)
    assert status.batt_temp_c == 15
    assert status.updated_at == UPDATED


def test_imperial_normalises_to_metric():
    status = map_status(metric_charging(
        ev_driving_range=100.0,
        ev_driving_range_unit="mi",
        odometer=1000.0,
        odometer_unit="mi",
        outside_temperature=68.0,
        _outside_temperature_unit="°F",
    ))

    assert status.range_km == 161
    assert status.odo_km == 1609
    assert status.outside_temp_c == 20


def test_outside_temperature_survives_an_absent_cabin_reading():
    # The real PV5: its only cabin figure is the HVAC setpoint, which reads
    # 'OFF' with the climate off, so the library never sets air_temperature.
    # Mapping the outside reading instead is the whole point of the field.
    status = map_status(metric_charging(
        air_temperature=None,
        outside_temperature=15.0,
        _outside_temperature_unit="°C",
    ))

    assert status.outside_temp_c == 15


def test_sub_zero_outside_temperature_is_not_clamped():
    assert map_status(metric_charging(outside_temperature=-4.0)).outside_temp_c == -4


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
    assert status.aux_battery_pct == 0
    assert status.charge_limit_ac == 0
    assert status.charge_limit_dc == 0
    assert status.doors_open == 0
    assert status.windows_open == 0
    assert status.trunk_open is False
    assert status.hood_open is False
    assert status.sunroof_open is False
    assert status.efficiency_kmpkwh == 0.0
    assert status.batt_temp_c is None
    assert status.updated_at is not None


def test_aux_battery_absent_on_an_otherwise_reporting_car():
    # Absent reads as 0 rather than being dropped: the wire model carries no
    # "unknown", by the same rule every other field here follows.
    status = map_status(metric_charging(car_battery_percentage=None))

    assert status.aux_battery_pct == 0
    assert status.soc_pct == 62


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


def test_v2l_discharge_is_reported_as_its_own_rate():
    # The same reading charge_kw truncates to zero.
    status = map_status(metric_charging(ev_charging_power=-1.2))

    assert status.v2l_kw == 1.2


def test_charging_is_not_mistaken_for_v2l():
    assert map_status(metric_charging(ev_charging_power=7.4)).v2l_kw == 0.0


def test_v2l_discharge_limit_passes_through():
    status = map_status(metric_charging(ev_v2l_discharge_limit=20))

    assert status.v2l_limit_pct == 20


def test_heaters_report_individually():
    status = map_status(metric_charging(
        defrost_is_on=True,
        back_window_heater_is_on=False,
        steering_wheel_heater_is_on=True,
    ))

    assert (status.defrost_on, status.rear_defrost_on, status.wheel_heat_on) == (
        True, False, True)


def test_unreported_heaters_read_as_off():
    status = map_status(metric_charging())

    assert (status.defrost_on, status.rear_defrost_on, status.wheel_heat_on) == (
        False, False, False)


def test_battery_conditioning_from_the_runtime_flag():
    status = map_status(metric_charging(data={
        "Green": {"BatteryManagement": {"BatteryConditioning": 1}},
    }))

    assert status.batt_conditioning is True


def test_battery_conditioning_flag_value_2_is_off():
    # 0 and 2 both mean off across the CCS2 nodes; only 1 is on.
    status = map_status(metric_charging(data={
        "Green": {"BatteryManagement": {"BatteryConditioning": 2}},
    }))

    assert status.batt_conditioning is False


def test_battery_cooling_counts_as_conditioning():
    # The runtime flag is not the only thing that moves: a spinning
    # chiller is the pack being conditioned whatever the flag says.
    status = map_status(metric_charging(data={
        "Green": {"BatteryManagement": {"BatteryConditioning": 0,
                                        "ChillerRPM": 1800}},
    }))

    assert status.batt_conditioning is True


def test_battery_heating_counts_as_conditioning():
    status = map_status(metric_charging(ev_battery_heating_state=True))

    assert status.batt_conditioning is True


def test_target_ranges_normalise_to_km():
    status = map_status(metric_charging(
        ev_target_range_charge_AC=289,
        ev_target_range_charge_AC_unit="mi",
        ev_target_range_charge_DC=365,
        ev_target_range_charge_DC_unit="mi",
    ))

    assert (status.target_range_ac_km, status.target_range_dc_km) == (465, 587)


def test_open_body_parts_are_counted():
    # The library mixes bools and raw 0/1 ints across these fields;
    # both must count.
    status = map_status(metric_charging(
        front_left_door_is_open=1,
        back_right_door_is_open=True,
        front_left_window_is_open=True,
        trunk_is_open=1,
        hood_is_open=True,
    ))

    assert status.doors_open == 2
    assert status.windows_open == 1
    assert status.trunk_open is True
    assert status.hood_open is True


def test_sunroof_raw_value_2_reads_closed():
    # The library's sunroof_is_open truthies Body.Sunroof.Glass.Open,
    # which is 2 on the owner's PV5 while closed. Only 1 means open.
    status = map_status(metric_charging(
        data={"Body": {"Sunroof": {"Glass": {"Open": 2}}}},
    ))

    assert status.sunroof_open is False


def test_sunroof_raw_value_1_reads_open():
    status = map_status(metric_charging(
        data={"Body": {"Sunroof": {"Glass": {"Open": 1}}}},
    ))

    assert status.sunroof_open is True


def test_a_car_without_a_sunroof_node_reads_closed():
    assert map_status(metric_charging(data={"Body": {}})).sunroof_open is False


def test_efficiency_in_mi_per_kwh_normalises_to_km():
    status = map_status(metric_charging(data={
        "Drivetrain": {"FuelSystem": {
            "AverageFuelEconomy": {"Drive": 3.2, "Unit": 6},
        }},
    }))

    assert status.efficiency_kmpkwh == pytest.approx(3.2 * 1.609344)


def test_efficiency_in_other_units_passes_through():
    status = map_status(metric_charging(data={
        "Drivetrain": {"FuelSystem": {
            "AverageFuelEconomy": {"Drive": 5.0, "Unit": 1},
        }},
    }))

    assert status.efficiency_kmpkwh == 5.0


def test_efficiency_defaults_when_the_node_is_absent():
    assert map_status(metric_charging(data={})).efficiency_kmpkwh == 0.0


def test_battery_temperature_in_fahrenheit_normalises():
    status = map_status(metric_charging(
        ev_battery_temperature_max=59,
        ev_battery_temperature_max_unit="°F",
    ))

    assert status.batt_temp_c == 15
