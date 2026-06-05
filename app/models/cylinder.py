"""Cylinder types defined by their gas capacity (17kg, 21kg, 33kg, ...)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class CylinderType(Base):
    __tablename__ = "cylinder_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    capacity_kg: Mapped[float] = mapped_column(Float, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock = relationship(
        "Stock",
        back_populates="cylinder_type",
        uselist=False,
        cascade="all, delete-orphan",
    )
