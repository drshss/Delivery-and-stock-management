"""Delivery-run workflow: create (attach orders by id), list/filter, assign/re-assign, add order, cancel."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_admin, require_admin_or_manager
from app.core.database import get_db
from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import (
    Delivery,
    DeliveryAssignmentHistory,
    Order,
    OrderAssignmentHistory,
    OrderItem,
)
from app.models.enums import DeliveryStatus, UserRole
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.delivery import (
    DeliveryAssign,
    DeliveryCreate,
    DeliveryOut,
    OrderCreate,
    OrderOut,
)
from app.services.delivery import (
    generate_delivery_number,
    generate_order_number,
    order_status_for_delivery,
    recompute_delivery_status,
)

router = APIRouter()

OPEN_STATUSES = [
    DeliveryStatus.PENDING,
    DeliveryStatus.ASSIGNED,
    DeliveryStatus.IN_TRANSIT,
]


def _validate_agent(db: Session, agent_id: int) -> User:
    agent = db.get(User, agent_id)
    if not agent or agent.role != UserRole.DELIVERY_AGENT:
        raise HTTPException(status_code=400, detail="Invalid delivery agent")
    return agent


def _build_order(db: Session, payload: OrderCreate, delivery: Delivery) -> Order:
    """Validate a customer/branch + items and return a new Order (not yet committed)."""
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer {payload.customer_id} not found")
    if payload.branch_id is not None:
        branch = db.get(CustomerBranch, payload.branch_id)
        if not branch or branch.customer_id != customer.id:
            raise HTTPException(status_code=400, detail="Invalid branch for this customer")
    for item in payload.items:
        if not db.get(CylinderType, item.cylinder_type_id):
            raise HTTPException(
                status_code=400, detail=f"Cylinder type {item.cylinder_type_id} not found"
            )

    order = Order(
        order_number=generate_order_number(db),
        customer_id=payload.customer_id,
        branch_id=payload.branch_id,
        notes=payload.notes,
        status=order_status_for_delivery(delivery),
    )
    for item in payload.items:
        order.items.append(
            OrderItem(
                cylinder_type_id=item.cylinder_type_id,
                quantity_ordered=item.quantity_ordered,
            )
        )
    return order


# ----------------------------- Create -----------------------------
@router.post("", response_model=DeliveryOut, status_code=status.HTTP_201_CREATED)
def create_delivery(
    payload: DeliveryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_manager),
):
    """Create a delivery run (vehicle + agent + date) and attach existing orders by id.

    Order-first: orders are created beforehand (`POST /orders` or
    `POST /orders/import`) and live in the pending pool until attached here (or via
    `POST /orders/{id}/assign`). Pass an empty `order_ids` to create an empty run.
    Only orders that have not started (pending/assigned) can be attached; an order
    already on another open run is moved here and its previous run is rolled up.
    """
    if payload.vehicle_id is not None and not db.get(Vehicle, payload.vehicle_id):
        raise HTTPException(status_code=400, detail="Vehicle not found")
    if payload.delivery_agent_id is not None:
        _validate_agent(db, payload.delivery_agent_id)

    delivery = Delivery(
        delivery_number=generate_delivery_number(db),
        vehicle_id=payload.vehicle_id,
        delivery_agent_id=payload.delivery_agent_id,
        scheduled_date=payload.scheduled_date,
        notes=payload.notes,
        created_by=current_user.id,
    )
    db.add(delivery)
    db.flush()  # assign delivery.id for the FK + assignment history below

    # De-duplicate ids while preserving the caller's order.
    order_ids = list(dict.fromkeys(payload.order_ids))
    source_run_ids: set[int] = set()
    for order_id in order_ids:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        if order.status not in (DeliveryStatus.PENDING, DeliveryStatus.ASSIGNED):
            raise HTTPException(
                status_code=400,
                detail=f"Order {order_id} has started or is closed and cannot be assigned",
            )

        previous_delivery_id = order.delivery_id
        if previous_delivery_id is not None:
            source_run_ids.add(previous_delivery_id)

        order.status = order_status_for_delivery(delivery)
        delivery.orders.append(order)  # sets order.delivery_id; moves it off any prior run

        db.add(
            OrderAssignmentHistory(
                order_id=order.id,
                previous_delivery_id=previous_delivery_id,
                new_delivery_id=delivery.id,
                reason="Assigned on delivery creation",
                changed_by=current_user.id,
            )
        )

    db.flush()
    # Roll up the status of any runs these orders were pulled away from.
    for run_id in source_run_ids:
        if run_id == delivery.id:
            continue
        source = db.get(Delivery, run_id)
        if source is not None:
            db.refresh(source)
            recompute_delivery_status(source)

    recompute_delivery_status(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


# ----------------------------- Add an order to a delivery -----------------------------
@router.post(
    "/{delivery_id}/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add another customer order to an existing delivery run",
)
def add_order_to_delivery(
    delivery_id: int,
    payload: OrderCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    delivery = db.get(Delivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if delivery.status in (DeliveryStatus.COMPLETED, DeliveryStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Cannot add orders to a closed delivery")

    order = _build_order(db, payload, delivery)
    delivery.orders.append(order)
    recompute_delivery_status(delivery)
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- List / get -----------------------------
@router.get("", response_model=list[DeliveryOut])
def list_deliveries(
    status_filter: DeliveryStatus | None = None,
    vehicle_id: int | None = None,
    agent_id: int | None = None,
    customer_id: int | None = None,
    scheduled_date: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List delivery runs. Filter by status, vehicle, agent, customer or date.

    Delivery agents only ever see runs assigned to themselves.
    """
    query = db.query(Delivery).options(
        selectinload(Delivery.orders).selectinload(Order.items),
        selectinload(Delivery.orders).selectinload(Order.evidences),
    )

    if current_user.role == UserRole.DELIVERY_AGENT:
        query = query.filter(Delivery.delivery_agent_id == current_user.id)
    elif agent_id is not None:
        query = query.filter(Delivery.delivery_agent_id == agent_id)

    if status_filter:
        query = query.filter(Delivery.status == status_filter)
    if vehicle_id:
        query = query.filter(Delivery.vehicle_id == vehicle_id)
    if customer_id:
        query = query.filter(Delivery.orders.any(Order.customer_id == customer_id))
    if scheduled_date:
        query = query.filter(Delivery.scheduled_date == scheduled_date)

    return query.order_by(Delivery.scheduled_date.desc(), Delivery.id.desc()).all()


@router.get("/{delivery_id}", response_model=DeliveryOut)
def get_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delivery = db.get(Delivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if current_user.role == UserRole.DELIVERY_AGENT and delivery.delivery_agent_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this delivery")
    return delivery


# ----------------------------- Assign / re-assign -----------------------------
@router.post(
    "/{delivery_id}/assign",
    response_model=DeliveryOut,
    summary="Assign or re-assign a delivery run to a vehicle / agent / date",
)
def assign_delivery(
    delivery_id: int,
    payload: DeliveryAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_manager),
):
    delivery = db.get(Delivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if delivery.status == DeliveryStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot re-assign a completed delivery")

    prev_vehicle = delivery.vehicle_id
    prev_agent = delivery.delivery_agent_id
    prev_date = delivery.scheduled_date

    if payload.vehicle_id is not None:
        if not db.get(Vehicle, payload.vehicle_id):
            raise HTTPException(status_code=400, detail="Vehicle not found")
        delivery.vehicle_id = payload.vehicle_id
    if payload.delivery_agent_id is not None:
        _validate_agent(db, payload.delivery_agent_id)
        delivery.delivery_agent_id = payload.delivery_agent_id
    if payload.scheduled_date is not None:
        delivery.scheduled_date = payload.scheduled_date

    # Promote not-yet-started orders to ASSIGNED once vehicle + agent are set.
    if delivery.vehicle_id and delivery.delivery_agent_id:
        for order in delivery.orders:
            if order.status == DeliveryStatus.PENDING:
                order.status = DeliveryStatus.ASSIGNED

    recompute_delivery_status(delivery)

    db.add(
        DeliveryAssignmentHistory(
            delivery_id=delivery.id,
            previous_vehicle_id=prev_vehicle,
            previous_agent_id=prev_agent,
            previous_date=prev_date,
            new_vehicle_id=delivery.vehicle_id,
            new_agent_id=delivery.delivery_agent_id,
            new_date=delivery.scheduled_date,
            reason=payload.reason,
            changed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(delivery)
    return delivery


# ----------------------------- Cancel -----------------------------
@router.post("/{delivery_id}/cancel", response_model=DeliveryOut)
def cancel_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Cancel the whole delivery run and all of its still-open orders."""
    delivery = db.get(Delivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if delivery.status == DeliveryStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed delivery")
    for order in delivery.orders:
        if order.status != DeliveryStatus.COMPLETED:
            order.status = DeliveryStatus.CANCELLED
    delivery.status = DeliveryStatus.CANCELLED
    db.commit()
    db.refresh(delivery)
    return delivery
