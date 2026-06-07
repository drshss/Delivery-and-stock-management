"""Customer & branch onboarding. Admins manage; everyone authenticated can read."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import Order, OrderItem
from app.models.enums import DeliveryStatus
from app.models.user import User
from app.schemas.customer import (
    CustomerBranchCreate,
    CustomerBranchOut,
    CustomerBranchUpdate,
    CustomerCreate,
    CustomerCylinderBalance,
    CustomerCylinderBalanceItem,
    CustomerOut,
    CustomerUpdate,
)

router = APIRouter()


# --------------------------- Customers ---------------------------
@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(Customer).filter(Customer.code == payload.code).first():
        raise HTTPException(status_code=400, detail="Customer code already exists")
    customer = Customer(**payload.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=list[CustomerOut])
def list_customers(
    search: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(Customer)
    if active_only:
        query = query.filter(Customer.is_active.is_(True))
    if search:
        like = f"%{search}%"
        query = query.filter((Customer.name.ilike(like)) | (Customer.code.ilike(like)))
    return query.order_by(Customer.name).all()


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, key, value)
    db.commit()
    db.refresh(customer)
    return customer


# --------------------------- Branches ---------------------------
@router.post(
    "/{customer_id}/branches",
    response_model=CustomerBranchOut,
    status_code=status.HTTP_201_CREATED,
)
def add_branch(
    customer_id: int,
    payload: CustomerBranchCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if db.query(CustomerBranch).filter(CustomerBranch.branch_code == payload.branch_code).first():
        raise HTTPException(status_code=400, detail="Branch code already exists")
    branch = CustomerBranch(customer_id=customer_id, **payload.model_dump())
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/{customer_id}/branches", response_model=list[CustomerBranchOut])
def list_branches(
    customer_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return (
        db.query(CustomerBranch)
        .filter(CustomerBranch.customer_id == customer_id)
        .order_by(CustomerBranch.name)
        .all()
    )


@router.patch("/branches/{branch_id}", response_model=CustomerBranchOut)
def update_branch(
    branch_id: int,
    payload: CustomerBranchUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    branch = db.get(CustomerBranch, branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(branch, key, value)
    db.commit()
    db.refresh(branch)
    return branch


# --------------------------- Cylinder holdings ---------------------------
@router.get(
    "/{customer_id}/cylinder-balance",
    response_model=CustomerCylinderBalance,
    summary="Cylinders currently held by a customer, broken down by cylinder type",
)
def customer_cylinder_balance(
    customer_id: int,
    branch_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Balance per cylinder type = full cylinders delivered − empties collected back,
    summed over the customer's completed orders. Optionally scoped to one branch."""
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if branch_id is not None:
        branch = db.get(CustomerBranch, branch_id)
        if not branch or branch.customer_id != customer_id:
            raise HTTPException(status_code=400, detail="Invalid branch for this customer")

    query = (
        db.query(
            CylinderType.id,
            CylinderType.capacity_kg,
            CylinderType.name,
            func.coalesce(func.sum(OrderItem.quantity_delivered), 0),
            func.coalesce(func.sum(OrderItem.quantity_empty_collected), 0),
        )
        .join(OrderItem, OrderItem.cylinder_type_id == CylinderType.id)
        .join(Order, OrderItem.order_id == Order.id)
        .filter(Order.customer_id == customer_id, Order.status == DeliveryStatus.COMPLETED)
    )
    if branch_id is not None:
        query = query.filter(Order.branch_id == branch_id)
    query = query.group_by(
        CylinderType.id, CylinderType.capacity_kg, CylinderType.name
    ).order_by(CylinderType.capacity_kg)

    items: list[CustomerCylinderBalanceItem] = []
    total_balance = 0
    for type_id, capacity, name, delivered, empty in query.all():
        delivered, empty = int(delivered), int(empty)
        balance = delivered - empty
        total_balance += balance
        items.append(
            CustomerCylinderBalanceItem(
                cylinder_type_id=type_id,
                capacity_kg=capacity,
                name=name,
                total_delivered=delivered,
                total_empty_collected=empty,
                balance=balance,
            )
        )

    return CustomerCylinderBalance(
        customer_id=customer_id,
        customer_name=customer.name,
        branch_id=branch_id,
        items=items,
        total_balance=total_balance,
    )
