"""Seed the database with sample data for development & testing.

Run with:  python seed.py
"""
from datetime import date

from app import models  # noqa: F401  (register all models)
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models.customer import Customer, CustomerBranch
from app.models.cylinder import CylinderType
from app.models.delivery import Delivery, Order, OrderItem
from app.models.enums import DeliveryStatus, UserRole
from app.models.stock import Stock
from app.models.user import User
from app.models.vehicle import Vehicle


def get_or_create(db, model, defaults=None, **kwargs):
    instance = db.query(model).filter_by(**kwargs).first()
    if instance:
        return instance
    instance = model(**{**kwargs, **(defaults or {})})
    db.add(instance)
    db.flush()
    return instance


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # ---- Users (one per role) ----
        get_or_create(
            db, User, email="admin@delivery.com",
            defaults=dict(full_name="System Admin", role=UserRole.ADMIN,
                          hashed_password=hash_password("admin123")),
        )
        manager = get_or_create(
            db, User, email="manager@delivery.com",
            defaults=dict(full_name="Stock Manager", role=UserRole.STOCK_MANAGER,
                          phone="9000000001", hashed_password=hash_password("manager123")),
        )
        agent = get_or_create(
            db, User, email="agent@delivery.com",
            defaults=dict(full_name="Delivery Agent", role=UserRole.DELIVERY_AGENT,
                          phone="9000000002", hashed_password=hash_password("agent123")),
        )

        # ---- Cylinder types + opening stock ----
        cylinder_types = {}
        for capacity, full, empty in [(17, 100, 20), (21, 80, 15), (33, 60, 10)]:
            cyl = get_or_create(
                db, CylinderType, capacity_kg=capacity,
                defaults=dict(name=f"{capacity}kg Cylinder",
                              description=f"Commercial {capacity}kg LPG cylinder"),
            )
            get_or_create(
                db, Stock, cylinder_type_id=cyl.id,
                defaults=dict(full_quantity=full, empty_quantity=empty),
            )
            cylinder_types[capacity] = cyl

        # ---- Vehicle ----
        vehicle = get_or_create(
            db, Vehicle, vehicle_number="KA01AB1234",
            defaults=dict(description="Tata Ace", capacity=120),
        )

        # ---- Customers + branches ----
        customer = get_or_create(
            db, Customer, code="CUST001",
            defaults=dict(name="Acme Restaurants", contact_person="Ravi",
                          phone="9812345678", address="MG Road, Bengaluru"),
        )
        branch = get_or_create(
            db, CustomerBranch, branch_code="CUST001-B1",
            defaults=dict(customer_id=customer.id, name="Acme - Indiranagar",
                          phone="9812300000", address="Indiranagar, Bengaluru",
                          latitude=12.9719, longitude=77.6412),
        )
        customer2 = get_or_create(
            db, Customer, code="CUST002",
            defaults=dict(name="Blue Hotel", contact_person="Meera",
                          phone="9898989898", address="Koramangala, Bengaluru"),
        )
        db.commit()

        # ---- Sample delivery run with two customer orders ----
        if not db.query(Delivery).first():
            delivery = Delivery(
                delivery_number="DLV-SEED-0001",
                vehicle_id=vehicle.id,
                delivery_agent_id=agent.id,
                scheduled_date=date.today(),
                status=DeliveryStatus.ASSIGNED,
                created_by=manager.id,
                notes="Seed sample delivery run",
            )
            # Order 1 — Acme, Indiranagar branch
            order1 = Order(
                order_number="ORD-SEED-0001",
                customer_id=customer.id,
                branch_id=branch.id,
                status=DeliveryStatus.ASSIGNED,
            )
            order1.items.append(OrderItem(cylinder_type_id=cylinder_types[17].id, quantity_ordered=10))
            order1.items.append(OrderItem(cylinder_type_id=cylinder_types[21].id, quantity_ordered=5))
            # Order 2 — Blue Hotel (no branch)
            order2 = Order(
                order_number="ORD-SEED-0002",
                customer_id=customer2.id,
                status=DeliveryStatus.ASSIGNED,
            )
            order2.items.append(OrderItem(cylinder_type_id=cylinder_types[33].id, quantity_ordered=4))

            delivery.orders.append(order1)
            delivery.orders.append(order2)
            db.add(delivery)
            db.commit()

        print("Seed complete. Login credentials:")
        print("  Admin    -> admin@delivery.com    / admin123")
        print("  Manager  -> manager@delivery.com  / manager123")
        print("  Agent    -> agent@delivery.com    / agent123")
    finally:
        db.close()


if __name__ == "__main__":
    run()
