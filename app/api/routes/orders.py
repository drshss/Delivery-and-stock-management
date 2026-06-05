"""Order workflow: list/filter, detail, evidence upload, completion (stock update), cancel, move."""
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_admin_or_manager
from app.core.config import settings
from app.core.database import get_db
from app.models.delivery import (
    Delivery,
    Order,
    OrderAssignmentHistory,
    OrderEvidence,
)
from app.models.enums import DeliveryStatus, StockTransactionType, UserRole
from app.models.stock import Stock, StockTransaction
from app.models.user import User
from app.schemas.delivery import OrderComplete, OrderMove, OrderEvidenceOut, OrderOut
from app.services.delivery import order_status_for_delivery, recompute_delivery_status

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_EVIDENCE_PER_ORDER = 2


def _get_order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _assert_agent_owns(order: Order, user: User) -> None:
    """A delivery agent may only act on orders within their own delivery run."""
    if user.role == UserRole.DELIVERY_AGENT and order.delivery.delivery_agent_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this order")


# ----------------------------- List / get -----------------------------
@router.get("", response_model=list[OrderOut])
def list_orders(
    status_filter: DeliveryStatus | None = None,
    customer_id: int | None = None,
    branch_id: int | None = None,
    delivery_id: int | None = None,
    vehicle_id: int | None = None,
    agent_id: int | None = None,
    scheduled_date: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List orders. Filter by status, customer, branch, delivery, vehicle, agent or date.

    Delivery agents only ever see orders within runs assigned to themselves.
    """
    query = db.query(Order).options(
        selectinload(Order.items), selectinload(Order.evidences)
    )

    needs_delivery_join = bool(vehicle_id or scheduled_date) or (
        current_user.role == UserRole.DELIVERY_AGENT or agent_id is not None
    )
    if needs_delivery_join:
        query = query.join(Delivery, Order.delivery_id == Delivery.id)

    if current_user.role == UserRole.DELIVERY_AGENT:
        query = query.filter(Delivery.delivery_agent_id == current_user.id)
    elif agent_id is not None:
        query = query.filter(Delivery.delivery_agent_id == agent_id)

    if status_filter:
        query = query.filter(Order.status == status_filter)
    if customer_id:
        query = query.filter(Order.customer_id == customer_id)
    if branch_id:
        query = query.filter(Order.branch_id == branch_id)
    if delivery_id:
        query = query.filter(Order.delivery_id == delivery_id)
    if vehicle_id:
        query = query.filter(Delivery.vehicle_id == vehicle_id)
    if scheduled_date:
        query = query.filter(Delivery.scheduled_date == scheduled_date)

    return query.order_by(Order.id.desc()).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = _get_order_or_404(db, order_id)
    _assert_agent_owns(order, current_user)
    return order


# ----------------------------- Evidence upload (per order) -----------------------------
@router.post(
    "/{order_id}/evidence",
    response_model=OrderEvidenceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a proof-of-delivery photo for this order (delivery agent / admin)",
)
def upload_order_evidence(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = _get_order_or_404(db, order_id)
    if current_user.role == UserRole.STOCK_MANAGER:
        raise HTTPException(status_code=403, detail="Stock managers cannot upload evidence")
    _assert_agent_owns(order, current_user)
    if order.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Order is already closed")
    if len(order.evidences) >= MAX_EVIDENCE_PER_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EVIDENCE_PER_ORDER} evidence photos are allowed per order",
        )
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG or WEBP images are allowed")

    folder = os.path.join(settings.UPLOAD_DIR, "orders", str(order.id))
    os.makedirs(folder, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    filepath = os.path.join(folder, f"{uuid.uuid4().hex}{ext}")

    contents = file.file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit")
    with open(filepath, "wb") as out:
        out.write(contents)

    evidence = OrderEvidence(
        order_id=order.id,
        file_path=filepath.replace("\\", "/"),
        uploaded_by=current_user.id,
    )
    # First evidence marks the order (and its run) as in transit.
    if order.status == DeliveryStatus.ASSIGNED:
        order.status = DeliveryStatus.IN_TRANSIT
        recompute_delivery_status(order.delivery)
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


# ----------------------------- Complete (per order) -----------------------------
@router.post(
    "/{order_id}/complete",
    response_model=OrderOut,
    summary="Submit delivered/empty quantities for this order and mark it complete",
)
def complete_order(
    order_id: int,
    payload: OrderComplete,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = _get_order_or_404(db, order_id)
    if current_user.role == UserRole.STOCK_MANAGER:
        raise HTTPException(status_code=403, detail="Stock managers cannot complete orders")
    _assert_agent_owns(order, current_user)
    if order.status == DeliveryStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Order already completed")
    if order.status == DeliveryStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Order is cancelled")

    # Business rule: a delivery agent must capture photo evidence before submitting.
    if current_user.role == UserRole.DELIVERY_AGENT and not order.evidences:
        raise HTTPException(
            status_code=400,
            detail="Please upload delivery evidence (photo) before completing the order",
        )

    items_by_type = {item.cylinder_type_id: item for item in order.items}
    for completed in payload.items:
        item = items_by_type.get(completed.cylinder_type_id)
        if not item:
            raise HTTPException(
                status_code=400,
                detail=f"Cylinder type {completed.cylinder_type_id} is not part of this order",
            )
        item.quantity_delivered = completed.quantity_delivered
        item.quantity_empty_collected = completed.quantity_empty_collected

        stock = db.query(Stock).filter(Stock.cylinder_type_id == completed.cylinder_type_id).first()
        if not stock:
            stock = Stock(cylinder_type_id=completed.cylinder_type_id, full_quantity=0, empty_quantity=0)
            db.add(stock)
            db.flush()
        # Full cylinders leave the warehouse; empties come back in.
        stock.full_quantity -= completed.quantity_delivered
        stock.empty_quantity += completed.quantity_empty_collected

        db.add(
            StockTransaction(
                cylinder_type_id=completed.cylinder_type_id,
                transaction_type=StockTransactionType.DELIVERY_OUT,
                full_change=-completed.quantity_delivered,
                empty_change=completed.quantity_empty_collected,
                reference=order.order_number,
                notes=f"Order {order.order_number} completed",
                created_by=current_user.id,
            )
        )

    order.status = DeliveryStatus.COMPLETED
    order.completed_at = datetime.now(timezone.utc)
    if payload.notes:
        order.notes = payload.notes

    recompute_delivery_status(order.delivery)
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- Cancel (single order) -----------------------------
@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    order = _get_order_or_404(db, order_id)
    if order.status == DeliveryStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed order")
    order.status = DeliveryStatus.CANCELLED
    recompute_delivery_status(order.delivery)
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- Move to another delivery run -----------------------------
@router.post(
    "/{order_id}/move",
    response_model=OrderOut,
    summary="Move an incomplete order to a different delivery run (re-schedule vehicle/agent/date)",
)
def move_order(
    order_id: int,
    payload: OrderMove,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_manager),
):
    order = _get_order_or_404(db, order_id)
    if order.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED, DeliveryStatus.IN_TRANSIT):
        raise HTTPException(
            status_code=400,
            detail="Only orders that have not started (pending/assigned) can be moved",
        )

    target = db.get(Delivery, payload.target_delivery_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target delivery not found")
    if target.id == order.delivery_id:
        raise HTTPException(status_code=400, detail="Order is already on this delivery")
    if target.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Cannot move into a closed delivery")

    source = order.delivery
    previous_delivery_id = order.delivery_id

    order.delivery_id = target.id
    order.status = order_status_for_delivery(target)

    db.add(
        OrderAssignmentHistory(
            order_id=order.id,
            previous_delivery_id=previous_delivery_id,
            new_delivery_id=target.id,
            reason=payload.reason,
            changed_by=current_user.id,
        )
    )
    db.flush()
    # Refresh both runs' relationship state so status rollup is accurate.
    db.refresh(source)
    db.refresh(target)
    recompute_delivery_status(source)
    recompute_delivery_status(target)
    db.commit()
    db.refresh(order)
    return order
