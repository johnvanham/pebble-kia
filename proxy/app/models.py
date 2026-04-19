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
    cabin_temp_c: int
    odo_km: int = Field(..., ge=0)
    is_climate_on: bool = False
    updated_at: datetime


class StatusResponse(BaseModel):
    id: str
    status: VehicleStatus
    fetched_at: datetime
    from_cache: bool
