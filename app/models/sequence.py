"""Atomic, gapless per-period counters backing human-readable order/delivery numbers."""
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NumberSequence(Base):
    """A named, monotonically-increasing counter — one row per (scope, period).

    The ``key`` encodes both the scope and the reset period, e.g.
    ``"order:20260607"`` or ``"delivery:20260607"``. ``last_value`` is bumped with
    an atomic ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` upsert inside the
    caller's transaction, which makes the allocation both race-safe (a row lock is
    held until commit) and gapless (a rollback un-counts it).
    """

    __tablename__ = "number_sequences"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
