"""Shared enumerations used across models and schemas."""
import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    STOCK_MANAGER = "stock_manager"
    DELIVERY_AGENT = "delivery_agent"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"        # created, not yet assigned to vehicle + agent
    ASSIGNED = "assigned"      # vehicle + agent assigned, scheduled
    IN_TRANSIT = "in_transit"  # agent started / uploaded evidence
    COMPLETED = "completed"    # delivered, empties collected, evidence captured
    CANCELLED = "cancelled"


class StockTransactionType(str, enum.Enum):
    PURCHASE = "purchase"          # new full cylinders added to warehouse
    DELIVERY_OUT = "delivery_out"  # full delivered / empties collected on a delivery
    EMPTY_RETURN = "empty_return"  # empties sent out for refilling
    REFILL = "refill"              # empties refilled back to full
    RELEASE = "release"            # full cylinders manually released out of stock
    ADJUSTMENT = "adjustment"      # manual stock correction / stocktake
