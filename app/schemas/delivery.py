from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings
from app.models.enums import DeliveryStatus


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
    items: list[OrderItemCreate] = Field(min_length=1)


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


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    delivery_id: int
    customer_id: int
    branch_id: int | None = None
    status: DeliveryStatus
    completed_at: datetime | None = None
    notes: str | None = None
    evidence_override_reason: str | None = None
    evidence_overridden_by: int | None = None
    evidence_overridden_at: datetime | None = None
    created_at: datetime
    items: list[OrderItemOut] = []
    evidences: list[OrderEvidenceOut] = []


# ----- deliveries (runs) -----
class DeliveryCreate(BaseModel):
    """A delivery run: a vehicle + agent on a date, with one or more customer orders."""

    scheduled_date: date
    vehicle_id: int | None = None
    delivery_agent_id: int | None = None
    notes: str | None = None
    orders: list[OrderCreate] = Field(min_length=1)


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
