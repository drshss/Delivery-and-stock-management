from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StockTransactionType


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cylinder_type_id: int
    full_quantity: int
    empty_quantity: int
    updated_at: datetime | None = None


class StockSummaryItem(BaseModel):
    cylinder_type_id: int
    capacity_kg: float
    name: str
    full_quantity: int
    empty_quantity: int
    total_quantity: int


class StockSummary(BaseModel):
    items: list[StockSummaryItem]
    total_full: int
    total_empty: int
    total_cylinders: int


class StockAdjustment(BaseModel):
    cylinder_type_id: int
    full_change: int = 0
    empty_change: int = 0
    transaction_type: StockTransactionType = StockTransactionType.ADJUSTMENT
    notes: str | None = None


class StockSet(BaseModel):
    """Set the absolute on-hand quantities (stocktake / correction), not a delta."""

    cylinder_type_id: int
    full_quantity: int = Field(ge=0)
    empty_quantity: int = Field(ge=0)
    notes: str | None = None


class StockRelease(BaseModel):
    """Release (take out) a number of FULL cylinders from stock, e.g. loan/write-off."""

    cylinder_type_id: int
    quantity: int = Field(gt=0)
    notes: str | None = None


class StockTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cylinder_type_id: int
    transaction_type: StockTransactionType
    full_change: int
    empty_change: int
    reference: str | None = None
    notes: str | None = None
    created_at: datetime
