from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PlugState = Literal["unplugged", "ac", "dc"]


class Vehicle(BaseModel):
    id: str
    vin: str
    nickname: str
    model: str


class VehicleList(BaseModel):
    vehicles: list[Vehicle]


class VehicleStatus(BaseModel):
    soc_pct: int = Field(..., ge=0, le=100)
    range_km: int = Field(..., ge=0)
    is_charging: bool
    charge_kw: float = Field(0.0, ge=0)
    charge_eta_min: int = Field(0, ge=0)
    plug: PlugState
    doors_locked: bool
    outside_temp_c: int
    odo_km: int = Field(..., ge=0)
    # The 12V battery, not the traction pack. Every wake draws on it, and
    # this is the one reading that shows whether the wakes are costing
    # anything.
    aux_battery_pct: int = Field(..., ge=0, le=100)
    is_climate_on: bool = False
    # Everything below defaults harmlessly so demo and scenario files
    # written before these fields existed keep loading.
    charge_limit_ac: int = Field(0, ge=0, le=100)
    charge_limit_dc: int = Field(0, ge=0, le=100)
    doors_open: int = Field(0, ge=0, le=4)
    windows_open: int = Field(0, ge=0, le=4)
    trunk_open: bool = False
    hood_open: bool = False
    sunroof_open: bool = False
    efficiency_kmpkwh: float = Field(0.0, ge=0)
    # None means "not reported" — unlike the other zero-defaulted
    # fields, 0 is a reading a real battery can give, so absence needs
    # its own encoding all the way to the watch (which renders "--").
    batt_temp_c: int | None = None
    updated_at: datetime


class ActionResponse(BaseModel):
    id: str
    action: str
    status: Literal["sent"] = "sent"


class StatusResponse(BaseModel):
    id: str
    status: VehicleStatus
    fetched_at: datetime
    from_cache: bool
    # Whether this response woke the vehicle: a forced read, or an
    # ordinary read the proxy upgraded because the car was charging and
    # the cached entry had aged past LIVE_CHARGING_REFRESH_SECONDS.
    forced: bool = False
