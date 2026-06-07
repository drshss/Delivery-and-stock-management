"""Stock levels (full / empty) per cylinder type and an audit trail of movements."""
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import StockTransactionType


class Stock(Base):
    """Current on-hand quantities for one cylinder type."""

    __tablename__ = "stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cylinder_type_id: Mapped[int] = mapped_column(
        ForeignKey("cylinder_types.id"), unique=True, nullable=False, index=True
    )
    full_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    empty_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    cylinder_type = relationship("CylinderType", back_populates="stock")


class StockTransaction(Base):
    """Immutable record of every change to stock for full auditability."""

    __tablename__ = "stock_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cylinder_type_id: Mapped[int] = mapped_column(
        ForeignKey("cylinder_types.id"), nullable=False, index=True
    )
    transaction_type: Mapped[StockTransactionType] = mapped_column(
        Enum(StockTransactionType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    full_change: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    empty_change: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. order number
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cylinder_type = relationship("CylinderType")
