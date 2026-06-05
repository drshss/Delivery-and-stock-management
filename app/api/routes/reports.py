"""Reporting endpoints (admin & stock manager): date-wise and customer-wise.

Reports are based on **orders** (each order is one customer delivery). The
scheduling date lives on the parent delivery run, so the queries join
Order -> Delivery to filter by date.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_admin_or_manager
from app.core.database import get_db
from app.models.customer import Customer
from app.models.cylinder import CylinderType
from app.models.delivery import Delivery, Order, OrderItem
from app.models.enums import DeliveryStatus
from app.models.user import User
from app.schemas.customer import CustomerCylinderBalance, CustomerCylinderBalanceItem
from app.schemas.delivery import OrderOut
from app.schemas.report import CustomerDeliveryReport, DateRangeReport

router = APIRouter()


@router.get("/date-range", response_model=DateRangeReport, summary="Order report for a date range")
def date_range_report(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    base = (
        db.query(Order)
        .join(Delivery, Order.delivery_id == Delivery.id)
        .filter(
            Delivery.scheduled_date >= start_date,
            Delivery.scheduled_date <= end_date,
        )
    )
    total = base.count()
    completed = base.filter(Order.status == DeliveryStatus.COMPLETED).count()
    cancelled = base.filter(Order.status == DeliveryStatus.CANCELLED).count()
    pending = total - completed - cancelled

    totals = (
        db.query(
            func.coalesce(func.sum(OrderItem.quantity_delivered), 0),
            func.coalesce(func.sum(OrderItem.quantity_empty_collected), 0),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .join(Delivery, Order.delivery_id == Delivery.id)
        .filter(
            Delivery.scheduled_date >= start_date,
            Delivery.scheduled_date <= end_date,
        )
        .first()
    )

    return DateRangeReport(
        start_date=start_date,
        end_date=end_date,
        total_deliveries=total,
        completed=completed,
        pending=pending,
        cancelled=cancelled,
        total_full_delivered=int(totals[0]),
        total_empty_collected=int(totals[1]),
    )


@router.get(
    "/by-customer",
    response_model=list[CustomerDeliveryReport],
    summary="Per-customer order report (optional date range)",
)
def by_customer_report(
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    # Order-level counts grouped per customer.
    order_query = (
        db.query(
            Customer.id.label("customer_id"),
            Customer.name.label("customer_name"),
            func.count(Order.id).label("total"),
            func.coalesce(
                func.sum(case((Order.status == DeliveryStatus.COMPLETED, 1), else_=0)), 0
            ).label("completed"),
        )
        .join(Order, Order.customer_id == Customer.id)
        .join(Delivery, Order.delivery_id == Delivery.id)
    )

    # Item-level sums grouped per customer.
    item_query = (
        db.query(
            Order.customer_id.label("customer_id"),
            func.coalesce(func.sum(OrderItem.quantity_delivered), 0).label("full"),
            func.coalesce(func.sum(OrderItem.quantity_empty_collected), 0).label("empty"),
        )
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(Delivery, Order.delivery_id == Delivery.id)
    )

    if start_date:
        order_query = order_query.filter(Delivery.scheduled_date >= start_date)
        item_query = item_query.filter(Delivery.scheduled_date >= start_date)
    if end_date:
        order_query = order_query.filter(Delivery.scheduled_date <= end_date)
        item_query = item_query.filter(Delivery.scheduled_date <= end_date)

    order_query = order_query.group_by(Customer.id, Customer.name)
    item_query = item_query.group_by(Order.customer_id)

    item_map = {row.customer_id: (int(row.full), int(row.empty)) for row in item_query.all()}

    results: list[CustomerDeliveryReport] = []
    for row in order_query.all():
        full, empty = item_map.get(row.customer_id, (0, 0))
        completed = int(row.completed)
        results.append(
            CustomerDeliveryReport(
                customer_id=row.customer_id,
                customer_name=row.customer_name,
                total_deliveries=row.total,
                completed=completed,
                pending=row.total - completed,
                total_full_delivered=full,
                total_empty_collected=empty,
            )
        )
    return results


@router.get(
    "/pending-orders",
    response_model=list[OrderOut],
    summary="Incomplete orders (to re-schedule / move to another run)",
)
def pending_orders(
    before_date: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    query = (
        db.query(Order)
        .options(selectinload(Order.items), selectinload(Order.evidences))
        .join(Delivery, Order.delivery_id == Delivery.id)
        .filter(
            Order.status.in_(
                [DeliveryStatus.PENDING, DeliveryStatus.ASSIGNED, DeliveryStatus.IN_TRANSIT]
            )
        )
    )
    if before_date:
        query = query.filter(Delivery.scheduled_date <= before_date)
    return query.order_by(Delivery.scheduled_date).all()


@router.get(
    "/customer-cylinder-balance",
    response_model=list[CustomerCylinderBalance],
    summary="Cylinders held by every customer, broken down by cylinder type",
)
def customer_cylinder_balance_report(
    only_outstanding: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    """Per-customer, per-cylinder-type holdings across all completed orders.

    balance = full delivered − empties collected back. With `only_outstanding=true`
    (default) only customers currently holding cylinders are returned.
    """
    rows = (
        db.query(
            Customer.id,
            Customer.name,
            CylinderType.id,
            CylinderType.capacity_kg,
            CylinderType.name,
            func.coalesce(func.sum(OrderItem.quantity_delivered), 0),
            func.coalesce(func.sum(OrderItem.quantity_empty_collected), 0),
        )
        .join(Order, Order.customer_id == Customer.id)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(CylinderType, OrderItem.cylinder_type_id == CylinderType.id)
        .filter(Order.status == DeliveryStatus.COMPLETED)
        .group_by(
            Customer.id, Customer.name,
            CylinderType.id, CylinderType.capacity_kg, CylinderType.name,
        )
        .order_by(Customer.name, CylinderType.capacity_kg)
        .all()
    )

    by_customer: dict[int, CustomerCylinderBalance] = {}
    for cust_id, cust_name, type_id, capacity, type_name, delivered, empty in rows:
        delivered, empty = int(delivered), int(empty)
        balance = delivered - empty
        bucket = by_customer.get(cust_id)
        if bucket is None:
            bucket = CustomerCylinderBalance(
                customer_id=cust_id, customer_name=cust_name, items=[], total_balance=0
            )
            by_customer[cust_id] = bucket
        bucket.items.append(
            CustomerCylinderBalanceItem(
                cylinder_type_id=type_id,
                capacity_kg=capacity,
                name=type_name,
                total_delivered=delivered,
                total_empty_collected=empty,
                balance=balance,
            )
        )
        bucket.total_balance += balance

    result = list(by_customer.values())
    if only_outstanding:
        result = [c for c in result if c.total_balance != 0]
    return result
