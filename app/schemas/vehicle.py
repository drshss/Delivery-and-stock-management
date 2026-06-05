from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VehicleBase(BaseModel):
    vehicle_number: str
    description: str | None = None
    capacity: int | None = None


class VehicleCreate(VehicleBase):
    pass


class VehicleUpdate(BaseModel):
    description: str | None = None
    capacity: int | None = None
    is_active: bool | None = None


class VehicleOut(VehicleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
