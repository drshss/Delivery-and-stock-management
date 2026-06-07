"""Order workflow: order-first create & bulk import, list/filter, detail, evidence
upload, assign/unassign to a run, completion (stock update), cancel, move."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_admin_or_manager
from app.core.config import settings
from app.core.database import get_db
from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import (
    Delivery,
    Order,
    OrderAssignmentHistory,
    OrderEvidence,
    OrderItem,
)
from app.models.enums import DeliveryStatus, StockTransactionType, UserRole
from app.models.stock import Stock, StockTransaction
from app.models.user import User
from app.schemas.delivery import (
    OrderAssign,
    OrderComplete,
    OrderCreate,
    OrderEvidenceOut,
    OrderImportError,
    OrderImportResult,
    OrderMove,
    OrderOut,
    OrderUnassign,
)
from app.services.delivery import (
    generate_order_number,
    order_status_for_delivery,
    recompute_delivery_status,
)
from app.services.order_import import build_template_csv, parse_orders_csv

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_EVIDENCE_PER_ORDER = 2


def _get_order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _assert_agent_owns(order: Order, user: User) -> None:
    """A delivery agent may only act on orders within their own delivery run.

    An order with no run yet (order-first / pending pool) is never owned by an agent.
    """
    if user.role != UserRole.DELIVERY_AGENT:
        return
    if order.delivery is None or order.delivery.delivery_agent_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this order")


def _validate_customer_branch(db: Session, customer_id: int, branch_id: int | None) -> None:
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")
    if branch_id is not None:
        branch = db.get(CustomerBranch, branch_id)
        if not branch or branch.customer_id != customer.id:
            raise HTTPException(status_code=400, detail="Invalid branch for this customer")


def _build_unassigned_order(db: Session, payload: OrderCreate) -> Order:
    """Validate a customer/branch + items and return a new PENDING, un-assigned order."""
    _validate_customer_branch(db, payload.customer_id, payload.branch_id)
    for item in payload.items:
        if not db.get(CylinderType, item.cylinder_type_id):
            raise HTTPException(status_code=400, detail=f"Cylinder type {item.cylinder_type_id} not found")

    order = Order(
        order_number=generate_order_number(db),
        delivery_id=None,
        customer_id=payload.customer_id,
        branch_id=payload.branch_id,
        notes=payload.notes,
        status=DeliveryStatus.PENDING,
    )
    for item in payload.items:
        order.items.append(
            OrderItem(cylinder_type_id=item.cylinder_type_id, quantity_ordered=item.quantity_ordered)
        )
    return order


# ----------------------------- List / get -----------------------------
@router.get("", response_model=list[OrderOut])
def list_orders(
    status_filter: DeliveryStatus | None = None,
    customer_id: int | None = None,
    branch_id: int | None = None,
    delivery_id: int | None = None,
    unassigned: bool | None = None,
    vehicle_id: int | None = None,
    agent_id: int | None = None,
    scheduled_date: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List orders. Filter by status, customer, branch, delivery, vehicle, agent or date.

    Pass `unassigned=true` to see the pending pool (orders not yet on any run).
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
    if unassigned is True:
        query = query.filter(Order.delivery_id.is_(None))
    elif unassigned is False:
        query = query.filter(Order.delivery_id.is_not(None))
    if vehicle_id:
        query = query.filter(Delivery.vehicle_id == vehicle_id)
    if scheduled_date:
        query = query.filter(Delivery.scheduled_date == scheduled_date)

    return query.order_by(Order.id.desc()).all()


# ----------------------------- Standalone create (order-first) -----------------------------
@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a standalone order (un-assigned / pending pool) — order-first",
)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    """Create a single order that is not yet on any delivery run.

    The order starts PENDING with no `delivery_id`; assign it to a run later via
    `POST /orders/{id}/assign` (or bulk-create many via `POST /orders/import`).
    """
    order = _build_unassigned_order(db, payload)
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- Bulk import (CSV) -----------------------------
@router.get(
    "/import/template",
    summary="Download the CSV template for bulk order import",
)
def download_import_template(_: User = Depends(require_admin_or_manager)):
    """Return a ready-to-fill CSV (header + example rows) for `POST /orders/import`."""
    return Response(
        content=build_template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="orders_import_template.csv"'},
    )


@router.post(
    "/import",
    response_model=OrderImportResult,
    summary="Bulk-create un-assigned orders from a CSV file (order-first)",
)
def import_orders(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Validate only; do not persist anything."),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    """Upload a CSV (see `GET /orders/import/template`) to create many PENDING,
    un-assigned orders at once. Import is all-or-nothing: if any row fails
    validation nothing is committed and every error is reported."""
    if file.content_type not in ("text/csv", "application/vnd.ms-excel", "application/octet-stream", "text/plain"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit")

    parsed = parse_orders_csv(raw, db)
    errors = [OrderImportError(row=e.row, order_ref=e.order_ref, error=e.error) for e in parsed.errors]

    # Failed validation OR dry-run -> never persist.
    if errors or dry_run:
        return OrderImportResult(
            committed=False,
            dry_run=dry_run,
            total_rows=parsed.total_rows,
            orders_parsed=len(parsed.orders),
            orders_created=0,
            created_order_numbers=[],
            errors=errors,
        )

    for order in parsed.orders:
        db.add(order)
    db.commit()
    created_numbers = [o.order_number for o in parsed.orders]
    return OrderImportResult(
        committed=True,
        dry_run=False,
        total_rows=parsed.total_rows,
        orders_parsed=len(parsed.orders),
        orders_created=len(created_numbers),
        created_order_numbers=created_numbers,
        errors=[],
    )


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
    if order.delivery_id is None:
        raise HTTPException(
            status_code=400,
            detail="Assign this order to a delivery run before uploading evidence",
        )
    if order.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Order is already closed")
    if len(order.evidences) >= MAX_EVIDENCE_PER_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EVIDENCE_PER_ORDER} evidence photos are allowed per order",
        )
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG or WEBP images are allowed")

    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit")

    evidence = OrderEvidence(
        order_id=order.id,
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(contents),
        data=contents,
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


@router.get(
    "/{order_id}/evidence/{evidence_id}",
    summary="Download a proof-of-delivery photo (authenticated, RBAC-scoped)",
)
def download_order_evidence(
    order_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stream the stored image bytes. Delivery agents may only access evidence on
    their own runs; admins and stock managers may access any."""
    order = _get_order_or_404(db, order_id)
    _assert_agent_owns(order, current_user)
    evidence = db.get(OrderEvidence, evidence_id)
    if not evidence or evidence.order_id != order.id:
        raise HTTPException(status_code=404, detail="Evidence not found")

    safe_name = (evidence.filename or f"evidence-{evidence.id}").replace('"', "")
    return Response(
        content=evidence.data,
        media_type=evidence.content_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


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
    if order.delivery_id is None:
        raise HTTPException(
            status_code=400,
            detail="Assign this order to a delivery run before completing it",
        )
    if order.status == DeliveryStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Order already completed")
    if order.status == DeliveryStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Order is cancelled")

    # Business rule: proof-of-delivery photo evidence is mandatory before an order
    # can be completed. The one exception is an admin who explicitly justifies the
    # missing evidence with a written comment — recorded below as an audit trail.
    if not order.evidences:
        override_reason = (payload.evidence_override_reason or "").strip()
        if current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=400,
                detail="Please upload delivery evidence (photo) before completing the order",
            )
        if not override_reason:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Delivery evidence is missing. As an admin you may complete this order "
                    "by providing 'evidence_override_reason' explaining why."
                ),
            )
        order.evidence_override_reason = override_reason
        order.evidence_overridden_by = current_user.id
        order.evidence_overridden_at = datetime.now(timezone.utc)

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

        # Row-lock the stock record so concurrent completions can't corrupt counts
        # (FOR UPDATE on PostgreSQL; ignored harmlessly on SQLite).
        stock = (
            db.query(Stock)
            .filter(Stock.cylinder_type_id == completed.cylinder_type_id)
            .with_for_update()
            .first()
        )
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
    if order.delivery_id is None:
        raise HTTPException(
            status_code=400,
            detail="Order is not on any run; use POST /orders/{id}/assign instead",
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


# ----------------------------- Assign / unassign (order-first) -----------------------------
@router.post(
    "/{order_id}/assign",
    response_model=OrderOut,
    summary="Assign (or re-assign) a pending order to a delivery run",
)
def assign_order(
    order_id: int,
    payload: OrderAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_manager),
):
    """Attach a standalone/pending order to a delivery run. Works whether the order
    is currently un-assigned (the pending pool) or already on another run."""
    order = _get_order_or_404(db, order_id)
    if order.status not in (DeliveryStatus.PENDING, DeliveryStatus.ASSIGNED):
        raise HTTPException(
            status_code=400,
            detail="Only orders that have not started (pending/assigned) can be assigned",
        )

    target = db.get(Delivery, payload.delivery_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target delivery not found")
    if target.id == order.delivery_id:
        raise HTTPException(status_code=400, detail="Order is already on this delivery")
    if target.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Cannot assign into a closed delivery")

    source = order.delivery  # may be None (assigning from the pending pool)
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
    if source is not None:
        db.refresh(source)
        recompute_delivery_status(source)
    db.refresh(target)
    recompute_delivery_status(target)
    db.commit()
    db.refresh(order)
    return order


@router.post(
    "/{order_id}/unassign",
    response_model=OrderOut,
    summary="Detach an order from its delivery run (return it to the pending pool)",
)
def unassign_order(
    order_id: int,
    payload: OrderUnassign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_manager),
):
    order = _get_order_or_404(db, order_id)
    if order.delivery_id is None:
        raise HTTPException(status_code=400, detail="Order is not assigned to any delivery")
    if order.status not in (DeliveryStatus.PENDING, DeliveryStatus.ASSIGNED):
        raise HTTPException(
            status_code=400,
            detail="Only orders that have not started (pending/assigned) can be un-assigned",
        )

    source = order.delivery
    previous_delivery_id = order.delivery_id

    order.delivery_id = None
    order.status = DeliveryStatus.PENDING

    db.add(
        OrderAssignmentHistory(
            order_id=order.id,
            previous_delivery_id=previous_delivery_id,
            new_delivery_id=None,
            reason=payload.reason,
            changed_by=current_user.id,
        )
    )
    db.flush()
    db.refresh(source)
    recompute_delivery_status(source)
    db.commit()
    db.refresh(order)
    return order
