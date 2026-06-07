"""Shared delivery/order business helpers: number generation and status rollup."""
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus
from app.models.sequence import NumberSequence


def _next_sequence_value(db: Session, key: str) -> int:
    """Atomically increment (creating it if absent) the counter `key`; return the new value.

    Implemented as a single ``INSERT ... ON CONFLICT (key) DO UPDATE
    SET last_value = last_value + 1 RETURNING last_value`` upsert. Because it is one
    statement that holds a row lock until the caller commits, concurrent callers can
    never observe the same value (race-safe); and because the bump lives in the
    caller's transaction, a rollback un-counts it (gapless — no burned numbers).
    """
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = (
        insert(NumberSequence)
        .values(key=key, last_value=1)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"last_value": NumberSequence.last_value + 1},
        )
        .returning(NumberSequence.last_value)
    )
    return db.execute(stmt).scalar_one()


def _generate_number(db: Session, prefix: str, scope: str) -> str:
    """Build a daily-reset, 4-digit zero-padded number, e.g. ``ORD-20260607-0001``."""
    period = f"{datetime.now(timezone.utc):%Y%m%d}"
    seq = _next_sequence_value(db, f"{scope}:{period}")
    return f"{prefix}-{period}-{seq:04d}"


def generate_delivery_number(db: Session) -> str:
    return _generate_number(db, "DLV", "delivery")


def generate_order_number(db: Session) -> str:
    return _generate_number(db, "ORD", "order")


def order_status_for_delivery(delivery: Delivery | None) -> DeliveryStatus:
    """The status a fresh/un-started order should take given its delivery's assignment.

    An order with no delivery run yet (order-first / un-scheduled) is PENDING.
    """
    if delivery is not None and delivery.vehicle_id and delivery.delivery_agent_id:
        return DeliveryStatus.ASSIGNED
    return DeliveryStatus.PENDING


def recompute_delivery_status(delivery: Delivery) -> None:
    """Roll the delivery (run) status up from the status of its orders.

    - all orders cancelled            -> CANCELLED
    - all active orders completed      -> COMPLETED
    - any order in transit/completed   -> IN_TRANSIT
    - otherwise vehicle+agent set      -> ASSIGNED
    - otherwise                        -> PENDING
    """
    orders = list(delivery.orders)
    if orders and all(o.status == DeliveryStatus.CANCELLED for o in orders):
        delivery.status = DeliveryStatus.CANCELLED
        delivery.completed_at = None
        return

    active = [o for o in orders if o.status != DeliveryStatus.CANCELLED]
    if active and all(o.status == DeliveryStatus.COMPLETED for o in active):
        delivery.status = DeliveryStatus.COMPLETED
        delivery.completed_at = datetime.now(timezone.utc)
        return

    if any(o.status in (DeliveryStatus.IN_TRANSIT, DeliveryStatus.COMPLETED) for o in active):
        delivery.status = DeliveryStatus.IN_TRANSIT
        delivery.completed_at = None
        return

    delivery.status = order_status_for_delivery(delivery)
    delivery.completed_at = None
