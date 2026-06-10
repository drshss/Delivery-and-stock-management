"""Delivery runs, the customer orders under them, line items, evidence and history.

Structure:
    Delivery  -> a run/trip: one vehicle + one agent on a scheduled date
      Order   -> one stop per customer (and optional branch); has its own evidence
        OrderItem -> a cylinder-type line (ordered / delivered / empties collected)

A delivery's status is an aggregate derived from the status of its orders.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import DeliveryStatus


class Delivery(Base):
    """A delivery run/trip: a vehicle + agent + date, visiting many customer stops."""

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    delivery_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    delivery_agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, values_callable=lambda x: [e.value for e in x]),
        default=DeliveryStatus.PENDING,
        nullable=False,
        index=True,
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vehicle = relationship("Vehicle", back_populates="deliveries")
    delivery_agent = relationship(
        "User", foreign_keys=[delivery_agent_id], back_populates="assigned_deliveries"
    )
    creator = relationship("User", foreign_keys=[created_by])
    orders = relationship(
        "Order",
        back_populates="delivery",
        # NOTE: intentionally NOT "delete-orphan". In the order-first model an order
        # outlives its run: un-assigning (delivery_id -> NULL) or cancelling a run must
        # return the order to the pending pool, never delete it.
        cascade="save-update, merge",
        foreign_keys="Order.delivery_id",
    )
    assignment_history = relationship(
        "DeliveryAssignmentHistory", back_populates="delivery", cascade="all, delete-orphan"
    )


class Order(Base):
    """A single customer (and optional branch) stop within a delivery run."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    # Nullable: in the order-first model an order exists on its own (status PENDING)
    # and is later assigned to a delivery run. NULL means "not yet scheduled".
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("customer_branches.id"), nullable=True, index=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, values_callable=lambda x: [e.value for e in x]),
        default=DeliveryStatus.PENDING,
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional billing reference (e.g. the Zoho invoice number) attached by an
    # admin or stock manager, either at creation time or later once it's issued.
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Audit trail for an admin completing an order without the mandatory photo evidence.
    evidence_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_overridden_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    evidence_overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    delivery = relationship("Delivery", back_populates="orders", foreign_keys=[delivery_id])
    customer = relationship("Customer", back_populates="orders")
    branch = relationship("CustomerBranch")
    evidence_overrider = relationship("User", foreign_keys=[evidence_overridden_by])
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    evidences = relationship("OrderEvidence", back_populates="order", cascade="all, delete-orphan")
    move_history = relationship(
        "OrderAssignmentHistory", back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    """One cylinder-type line on an order: ordered / delivered / empties collected."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    cylinder_type_id: Mapped[int] = mapped_column(ForeignKey("cylinder_types.id"), nullable=False)
    quantity_ordered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity_empty_collected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    order = relationship("Order", back_populates="items")
    cylinder_type = relationship("CylinderType")


class OrderEvidence(Base):
    """Photo evidence uploaded by the delivery agent as proof of delivery for an order.

    The image bytes are stored directly in the database (BYTEA on PostgreSQL,
    BLOB on SQLite) and served only through an authenticated download endpoint —
    no files are written to local/ephemeral disk.
    """

    __tablename__ = "order_evidences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="evidences")


class DeliveryAssignmentHistory(Base):
    """Audit of every delivery-run (re)assignment — vehicle / agent / date changes."""

    __tablename__ = "delivery_assignment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), nullable=False, index=True)
    previous_vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    previous_agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    previous_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    new_agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    new_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    delivery = relationship("Delivery", back_populates="assignment_history")


class OrderAssignmentHistory(Base):
    """Audit of moving an order from one delivery run to another (re-scheduling)."""

    __tablename__ = "order_assignment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    previous_delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"), nullable=True)
    new_delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="move_history")
