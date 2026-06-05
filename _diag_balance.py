"""Demonstrate cumulative cylinder-balance tracking across REPEAT deliveries
to the same customer. Uses a throwaway DB so nothing real is touched.

Run:  python _diag_balance.py
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./_diag_balance.db"
os.environ["UPLOAD_DIR"] = "_diag_balance_uploads"

import shutil
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def login(c, email, pw):
    return c.post("/api/v1/auth/login", data={"username": email, "password": pw}).json()["access_token"]


def do_delivery(c, admin, agent_hdr, agent_id, vehicle_id, customer_id, cyl_id, day, deliver, collect):
    """Create a 1-order delivery, assign, upload evidence, complete it."""
    d = c.post("/api/v1/deliveries", headers=admin, json={
        "scheduled_date": str(day),
        "vehicle_id": vehicle_id,
        "delivery_agent_id": agent_id,
        "orders": [{"customer_id": customer_id,
                    "items": [{"cylinder_type_id": cyl_id, "quantity_ordered": deliver}]}],
    }).json()
    order_id = d["orders"][0]["id"]
    c.post(f"/api/v1/orders/{order_id}/evidence", headers=agent_hdr,
           files={"file": ("p.png", b"img", "image/png")})
    c.post(f"/api/v1/orders/{order_id}/complete", headers=agent_hdr,
           json={"items": [{"cylinder_type_id": cyl_id,
                            "quantity_delivered": deliver, "quantity_empty_collected": collect}]})


def balance(c, admin, customer_id):
    b = c.get(f"/api/v1/customers/{customer_id}/cylinder-balance", headers=admin).json()
    return b["total_balance"], b["items"]


with TestClient(app) as c:
    admin = auth(login(c, "admin@delivery.com", "admin123"))

    cyl = c.post("/api/v1/cylinder-types", headers=admin, json={"capacity_kg": 17, "name": "17kg"}).json()
    cyl_id = cyl["id"]
    c.post("/api/v1/stock/set", headers=admin,
           json={"cylinder_type_id": cyl_id, "full_quantity": 100, "empty_quantity": 0})

    cust = c.post("/api/v1/customers", headers=admin, json={"code": "ACME", "name": "Acme"}).json()
    cust_id = cust["id"]
    veh = c.post("/api/v1/vehicles", headers=admin, json={"vehicle_number": "V-1"}).json()["id"]
    c.post("/api/v1/users", headers=admin, json={"full_name": "Agent", "email": "a@a.com",
                                                 "role": "delivery_agent", "password": "agent123"})
    ag = c.post("/api/v1/users", headers=admin, json={"full_name": "Agent2", "email": "a2@a.com",
                                                      "role": "delivery_agent", "password": "agent123"}).json()
    ag_id = ag["id"]
    ag_hdr = auth(login(c, "a2@a.com", "agent123"))

    print("Start: customer holds", balance(c, admin, cust_id)[0], "cylinders\n")

    # ---- Delivery #1 (today): first visit, no empties to collect yet ----
    do_delivery(c, admin, ag_hdr, ag_id, veh, cust_id, cyl_id, date.today(), deliver=10, collect=0)
    tot, items = balance(c, admin, cust_id)
    print(f"After delivery #1 (delivered 10, collected 0): balance = {tot}")
    print("   detail:", [(i['total_delivered'], i['total_empty_collected'], i['balance']) for i in items])

    # ---- Delivery #2 (next day): bring 12 full, take back 9 empties ----
    do_delivery(c, admin, ag_hdr, ag_id, veh, cust_id, cyl_id, date.today() + timedelta(days=1), deliver=12, collect=9)
    tot, items = balance(c, admin, cust_id)
    print(f"After delivery #2 (delivered 12, collected 9): balance = {tot}")
    print("   detail (cumulative delivered, collected, balance):",
          [(i['total_delivered'], i['total_empty_collected'], i['balance']) for i in items])

    print("\nPhysical check: 10 - 9 returned + 12 new = 13  ->", "OK" if tot == 13 else "MISMATCH")

if os.path.exists("_diag_balance.db"):
    os.remove("_diag_balance.db")
shutil.rmtree("_diag_balance_uploads", ignore_errors=True)
