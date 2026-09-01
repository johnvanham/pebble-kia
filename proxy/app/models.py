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
    # The 12V battery, not the traction pack. It rides the wire because
    # the force-refresh rate limit exists to keep telematics wake-ups from
    # flattening it, and this is the only reading that shows whether that
    # is working.
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
    # Whether this response actually woke the vehicle. A forced refresh
    # inside LIVE_FORCE_MIN_SECONDS comes back with forced=False, which
    # is the only way a client can tell the difference.
    forced: bool = False
