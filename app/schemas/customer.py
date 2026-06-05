from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class CustomerBranchBase(BaseModel):
    branch_code: str
    name: str
    contact_person: str | None = None
    phone: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class CustomerBranchCreate(CustomerBranchBase):
    pass


class CustomerBranchUpdate(BaseModel):
    name: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    is_active: bool | None = None


class CustomerBranchOut(CustomerBranchBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    is_active: bool
    created_at: datetime


class CustomerBase(BaseModel):
    code: str
    name: str
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    gst_number: str | None = None


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    gst_number: str | None = None
    is_active: bool | None = None


class CustomerOut(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    branches: list[CustomerBranchOut] = []


# ----- cylinder holdings / balance with the customer -----
class CustomerCylinderBalanceItem(BaseModel):
    cylinder_type_id: int
    capacity_kg: float
    name: str
    total_delivered: int
    total_empty_collected: int
    balance: int  # cylinders currently held by the customer (delivered - empties returned)


class CustomerCylinderBalance(BaseModel):
    customer_id: int
    customer_name: str
    branch_id: int | None = None
    items: list[CustomerCylinderBalanceItem]
    total_balance: int
