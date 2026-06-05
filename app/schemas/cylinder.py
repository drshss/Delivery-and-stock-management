from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CylinderTypeBase(BaseModel):
    capacity_kg: float = Field(gt=0, description="Cylinder capacity in kg, e.g. 17, 21, 33")
    name: str
    description: str | None = None


class CylinderTypeCreate(CylinderTypeBase):
    pass


class CylinderTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class CylinderTypeOut(CylinderTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
