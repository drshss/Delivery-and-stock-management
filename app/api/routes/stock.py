"""Stock: full/empty quantities per cylinder type, summary and manual adjustments."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_or_manager
from app.core.database import get_db
from app.models.cylinder import CylinderType
from app.models.enums import StockTransactionType
from app.models.stock import Stock, StockTransaction
from app.models.user import User
from app.schemas.stock import (
    StockAdjustment,
    StockOut,
    StockRelease,
    StockSet,
    StockSummary,
    StockSummaryItem,
    StockTransactionOut,
)

router = APIRouter()


@router.get("/summary", response_model=StockSummary, summary="Stock summary by cylinder type")
def stock_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    types = db.query(CylinderType).filter(CylinderType.is_active.is_(True)).order_by(CylinderType.capacity_kg).all()
    items: list[StockSummaryItem] = []
    total_full = 0
    total_empty = 0
    for cylinder_type in types:
        stock = db.query(Stock).filter(Stock.cylinder_type_id == cylinder_type.id).first()
        full = stock.full_quantity if stock else 0
        empty = stock.empty_quantity if stock else 0
        total_full += full
        total_empty += empty
        items.append(
            StockSummaryItem(
                cylinder_type_id=cylinder_type.id,
                capacity_kg=cylinder_type.capacity_kg,
                name=cylinder_type.name,
                full_quantity=full,
                empty_quantity=empty,
                total_quantity=full + empty,
            )
        )
    return StockSummary(
        items=items,
        total_full=total_full,
        total_empty=total_empty,
        total_cylinders=total_full + total_empty,
    )


@router.post("/adjust", response_model=StockOut, summary="Adjust stock (admin only)")
def adjust_stock(
    payload: StockAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    cylinder_type = db.get(CylinderType, payload.cylinder_type_id)
    if not cylinder_type:
        raise HTTPException(status_code=404, detail="Cylinder type not found")

    stock = db.query(Stock).filter(Stock.cylinder_type_id == payload.cylinder_type_id).first()
    if not stock:
        stock = Stock(cylinder_type_id=payload.cylinder_type_id, full_quantity=0, empty_quantity=0)
        db.add(stock)
        db.flush()

    new_full = stock.full_quantity + payload.full_change
    new_empty = stock.empty_quantity + payload.empty_change
    if new_full < 0 or new_empty < 0:
        raise HTTPException(status_code=400, detail="Adjustment would make stock negative")

    stock.full_quantity = new_full
    stock.empty_quantity = new_empty

    db.add(
        StockTransaction(
            cylinder_type_id=payload.cylinder_type_id,
            transaction_type=payload.transaction_type,
            full_change=payload.full_change,
            empty_change=payload.empty_change,
            notes=payload.notes,
            created_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(stock)
    return stock


def _get_or_create_stock(db: Session, cylinder_type_id: int) -> Stock:
    if not db.get(CylinderType, cylinder_type_id):
        raise HTTPException(status_code=404, detail="Cylinder type not found")
    stock = db.query(Stock).filter(Stock.cylinder_type_id == cylinder_type_id).first()
    if not stock:
        stock = Stock(cylinder_type_id=cylinder_type_id, full_quantity=0, empty_quantity=0)
        db.add(stock)
        db.flush()
    return stock


@router.post(
    "/set",
    response_model=StockOut,
    summary="Set absolute full/empty quantities (stocktake / correction, admin only)",
)
def set_stock(
    payload: StockSet,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Set the exact on-hand counts. Unlike /adjust, this does NOT add to existing —
    it overwrites the values and records the implied change as an adjustment."""
    stock = _get_or_create_stock(db, payload.cylinder_type_id)
    full_change = payload.full_quantity - stock.full_quantity
    empty_change = payload.empty_quantity - stock.empty_quantity

    stock.full_quantity = payload.full_quantity
    stock.empty_quantity = payload.empty_quantity

    db.add(
        StockTransaction(
            cylinder_type_id=payload.cylinder_type_id,
            transaction_type=StockTransactionType.ADJUSTMENT,
            full_change=full_change,
            empty_change=empty_change,
            notes=payload.notes or "Absolute stock set",
            created_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(stock)
    return stock


@router.post(
    "/release",
    response_model=StockOut,
    summary="Release (remove) full cylinders from stock (admin only)",
)
def release_stock(
    payload: StockRelease,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Take a number of FULL cylinders out of stock (e.g. loaned out, damaged, transferred)."""
    stock = _get_or_create_stock(db, payload.cylinder_type_id)
    if stock.full_quantity < payload.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Only {stock.full_quantity} full cylinders available to release",
        )
    stock.full_quantity -= payload.quantity

    db.add(
        StockTransaction(
            cylinder_type_id=payload.cylinder_type_id,
            transaction_type=StockTransactionType.RELEASE,
            full_change=-payload.quantity,
            empty_change=0,
            notes=payload.notes or "Full cylinders released from stock",
            created_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(stock)
    return stock


@router.get("/transactions", response_model=list[StockTransactionOut])
def list_transactions(
    cylinder_type_id: int | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_manager),
):
    query = db.query(StockTransaction)
    if cylinder_type_id:
        query = query.filter(StockTransaction.cylinder_type_id == cylinder_type_id)
    return query.order_by(StockTransaction.id.desc()).limit(limit).all()
