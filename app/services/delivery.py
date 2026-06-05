"""Shared delivery/order business helpers: number generation and status rollup."""
import uuid
from datetime import datetime, timezone

from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus


def generate_delivery_number() -> str:
    return f"DLV-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def generate_order_number() -> str:
    return f"ORD-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def order_status_for_delivery(delivery: Delivery) -> DeliveryStatus:
    """The status a fresh/un-started order should take given its delivery's assignment."""
    if delivery.vehicle_id and delivery.delivery_agent_id:
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
