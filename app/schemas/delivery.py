from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.core.config import settings
from app.models.enums import DeliveryStatus


def _normalize_invoice_number(value: str | None) -> str | None:
    """Trim surrounding whitespace and treat a blank string as 'no invoice'."""
    if value is None:
        return None
    value = value.strip()
    return value or None



# ----- order items -----
class OrderItemCreate(BaseModel):
    cylinder_type_id: int
    quantity_ordered: int = Field(ge=0)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cylinder_type_id: int
    quantity_ordered: int
    quantity_delivered: int
    quantity_empty_collected: int


class OrderItemComplete(BaseModel):
    cylinder_type_id: int
    quantity_delivered: int = Field(ge=0)
    quantity_empty_collected: int = Field(ge=0)


# ----- order evidence -----
class OrderEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    filename: str | None = None
    content_type: str
    size_bytes: int
    uploaded_by: int | None = None
    uploaded_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def download_url(self) -> str:
        """Authenticated endpoint to fetch the image bytes."""
        return f"{settings.API_V1_PREFIX}/orders/{self.order_id}/evidence/{self.id}"


# ----- orders -----
class OrderCreate(BaseModel):
    """One customer stop (branch optional) with its cylinder line items."""

    customer_id: int
    branch_id: int | None = None
    notes: str | None = None
    # Optional billing reference, set by an admin / stock manager at creation time.
    invoice_number: str | None = Field(default=None, max_length=100)
    items: list[OrderItemCreate] = Field(min_length=1)

    @field_validator("invoice_number")
    @classmethod
    def _clean_invoice_number(cls, v: str | None) -> str | None:
        return _normalize_invoice_number(v)


class OrderUpdate(BaseModel):
    """Patch editable order metadata. Only fields that are present are applied,
    so an admin / stock manager can attach (or clear) the invoice number after
    the order has been created without touching anything else."""

    invoice_number: str | None = Field(default=None, max_length=100)

    @field_validator("invoice_number")
    @classmethod
    def _clean_invoice_number(cls, v: str | None) -> str | None:
        return _normalize_invoice_number(v)



class OrderComplete(BaseModel):
    items: list[OrderItemComplete] = Field(min_length=1)
    notes: str | None = None
    # Admin-only: justification for completing the order when no photo evidence
    # was uploaded. Ignored for non-admins (who must always provide evidence).
    evidence_override_reason: str | None = Field(default=None, max_length=1000)


class OrderMove(BaseModel):
    """Move an order to a different delivery run (re-schedule to another vehicle/agent/date)."""

    target_delivery_id: int
    reason: str | None = None


class OrderAssign(BaseModel):
    """Assign (or re-assign) a standalone/pending order to a delivery run."""

    delivery_id: int
    reason: str | None = None


class OrderUnassign(BaseModel):
    """Return an order to the pending pool (detach it from its delivery run)."""

    reason: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    delivery_id: int | None = None
    customer_id: int
    branch_id: int | None = None
    status: DeliveryStatus
    completed_at: datetime | None = None
    notes: str | None = None
    invoice_number: str | None = None
    evidence_override_reason: str | None = None
    evidence_overridden_by: int | None = None
    evidence_overridden_at: datetime | None = None
    created_at: datetime
    items: list[OrderItemOut] = []
    evidences: list[OrderEvidenceOut] = []


# ----- bulk order import (CSV) -----
class OrderImportError(BaseModel):
    """A single row/group that failed validation during a bulk import."""

    row: int | None = None
    order_ref: str | None = None
    error: str


class OrderImportResult(BaseModel):
    """Outcome of a bulk CSV import.

    When `committed` is False either it was a dry-run or validation failed, so no
    orders were persisted (the import is all-or-nothing).
    """

    committed: bool
    dry_run: bool
    total_rows: int
    orders_parsed: int
    orders_created: int
    created_order_numbers: list[str] = []
    errors: list[OrderImportError] = []


# ----- deliveries (runs) -----
class DeliveryCreate(BaseModel):
    """A delivery run: a vehicle + agent on a date.

    Order-first: orders are created beforehand (`POST /orders` or
    `POST /orders/import`) and live in the pending pool. Attach them to the run by
    id via `order_ids`. Pass an empty list to create an empty run and assign orders
    later through `POST /orders/{id}/assign`.
    """

    scheduled_date: date
    vehicle_id: int | None = None
    delivery_agent_id: int | None = None
    notes: str | None = None
    order_ids: list[int] = Field(default_factory=list)


class DeliveryAssign(BaseModel):
    """Assign or re-assign a delivery run to a vehicle / agent / date."""

    vehicle_id: int | None = None
    delivery_agent_id: int | None = None
    scheduled_date: date | None = None
    reason: str | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    delivery_number: str
    vehicle_id: int | None = None
    delivery_agent_id: int | None = None
    status: DeliveryStatus
    scheduled_date: date
    completed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    orders: list[OrderOut] = []
