"""Cylinder type catalogue (17kg, 21kg, 33kg, ...). Admin manages; all can read."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.models.cylinder import CylinderType
from app.models.stock import Stock
from app.models.user import User
from app.schemas.cylinder import CylinderTypeCreate, CylinderTypeOut, CylinderTypeUpdate

router = APIRouter()


@router.post("", response_model=CylinderTypeOut, status_code=status.HTTP_201_CREATED)
def create_cylinder_type(
    payload: CylinderTypeCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(CylinderType).filter(CylinderType.capacity_kg == payload.capacity_kg).first():
        raise HTTPException(status_code=400, detail="A cylinder type with this capacity already exists")
    cylinder_type = CylinderType(**payload.model_dump())
    db.add(cylinder_type)
    db.flush()
    # Create the matching stock row so it can be tracked immediately.
    db.add(Stock(cylinder_type_id=cylinder_type.id, full_quantity=0, empty_quantity=0))
    db.commit()
    db.refresh(cylinder_type)
    return cylinder_type


@router.get("", response_model=list[CylinderTypeOut])
def list_cylinder_types(
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(CylinderType)
    if active_only:
        query = query.filter(CylinderType.is_active.is_(True))
    return query.order_by(CylinderType.capacity_kg).all()


@router.patch("/{cylinder_type_id}", response_model=CylinderTypeOut)
def update_cylinder_type(
    cylinder_type_id: int,
    payload: CylinderTypeUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    cylinder_type = db.get(CylinderType, cylinder_type_id)
    if not cylinder_type:
        raise HTTPException(status_code=404, detail="Cylinder type not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(cylinder_type, key, value)
    db.commit()
    db.refresh(cylinder_type)
    return cylinder_type
