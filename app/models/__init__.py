"""Import all models so SQLAlchemy's metadata & relationships are fully registered."""
from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import (
    Delivery,
    DeliveryAssignmentHistory,
    Order,
    OrderAssignmentHistory,
    OrderEvidence,
    OrderItem,
)
from app.models.stock import Stock, StockTransaction
from app.models.user import User
from app.models.vehicle import Vehicle

__all__ = [
    "User",
    "Customer",
    "CustomerBranch",
    "CylinderType",
    "Stock",
    "StockTransaction",
    "Vehicle",
    "Delivery",
    "Order",
    "OrderItem",
    "OrderEvidence",
    "DeliveryAssignmentHistory",
    "OrderAssignmentHistory",
]
