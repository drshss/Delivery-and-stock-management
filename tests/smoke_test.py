"""End-to-end smoke test for the delivery & stock API.

Runs the whole app in-process against a throwaway SQLite DB, exercising auth,
RBAC, stock, customer/branch, delivery assignment, DB-backed evidence upload +
download, completion (with automatic stock update) and reporting.

Run with:  python -m tests.smoke_test
"""
import os
from datetime import date, timedelta

# Point the app at a throwaway database BEFORE importing it.
os.environ["DATABASE_URL"] = "sqlite:///./_smoke_test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.manage import reset_admin_password  # noqa: E402


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, password: str) -> str:
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == 200, f"login failed: {res.text}"
    return res.json()["access_token"]


def run() -> None:
    with TestClient(app) as client:
        # ---- health ----
        assert client.get("/health").json() == {"status": "ok"}

        # ---- admin login ----
        admin = _auth(_login(client, "admin@delivery.com", "admin123"))

        # ---- cylinder type + opening stock ----
        res = client.post(
            "/api/v1/cylinder-types",
            headers=admin,
            json={"capacity_kg": 17, "name": "17kg"},
        )
        assert res.status_code == 201, res.text
        cyl_id = res.json()["id"]

        res = client.post(
            "/api/v1/stock/adjust",
            headers=admin,
            json={"cylinder_type_id": cyl_id, "full_change": 100, "transaction_type": "purchase"},
        )
        assert res.status_code == 200 and res.json()["full_quantity"] == 100, res.text

        # ---- customer + branch ----
        res = client.post(
            "/api/v1/customers", headers=admin, json={"code": "C1", "name": "Test Cust"}
        )
        assert res.status_code == 201, res.text
        customer_id = res.json()["id"]

        res = client.post(
            f"/api/v1/customers/{customer_id}/branches",
            headers=admin,
            json={"branch_code": "C1-B1", "name": "Indiranagar"},
        )
        assert res.status_code == 201, res.text
        branch_id = res.json()["id"]

        # ---- vehicle + agent user ----
        res = client.post("/api/v1/vehicles", headers=admin, json={"vehicle_number": "V-1"})
        assert res.status_code == 201, res.text
        vehicle_id = res.json()["id"]

        res = client.post(
            "/api/v1/users",
            headers=admin,
            json={
                "full_name": "Agent A",
                "email": "smoke_agent@a.com",
                "role": "delivery_agent",
                "password": "agent123",
            },
        )
        assert res.status_code == 201, res.text
        agent_id = res.json()["id"]
        agent = _auth(_login(client, "smoke_agent@a.com", "agent123"))

        # ---- stock manager user ----
        res = client.post(
            "/api/v1/users",
            headers=admin,
            json={
                "full_name": "Manager M",
                "email": "smoke_mgr@a.com",
                "role": "stock_manager",
                "password": "manager123",
            },
        )
        assert res.status_code == 201, res.text
        manager = _auth(_login(client, "smoke_mgr@a.com", "manager123"))

        # ---- extra customers for multi-order + move tests ----
        res = client.post("/api/v1/customers", headers=admin, json={"code": "C2", "name": "Blue Hotel"})
        assert res.status_code == 201, res.text
        customer2_id = res.json()["id"]
        res = client.post("/api/v1/customers", headers=admin, json={"code": "C3", "name": "Placeholder Co"})
        assert res.status_code == 201, res.text
        customer3_id = res.json()["id"]

        # ---- stock: SET absolute (not additive) then RELEASE full out ----
        res = client.post(
            "/api/v1/stock/set",
            headers=admin,
            json={"cylinder_type_id": cyl_id, "full_quantity": 120, "empty_quantity": 10},
        )
        assert res.status_code == 200 and res.json()["full_quantity"] == 120, res.text
        res = client.post(
            "/api/v1/stock/release",
            headers=admin,
            json={"cylinder_type_id": cyl_id, "quantity": 20},
        )
        assert res.status_code == 200 and res.json()["full_quantity"] == 100, res.text
        # only admin can set stock
        res = client.post(
            "/api/v1/stock/set",
            headers=manager,
            json={"cylinder_type_id": cyl_id, "full_quantity": 5, "empty_quantity": 0},
        )
        assert res.status_code == 403, "manager must not be able to set stock"

        # ---- create a delivery run with TWO customer orders (manager allocates) ----
        res = client.post(
            "/api/v1/deliveries",
            headers=manager,
            json={
                "scheduled_date": str(date.today()),
                "orders": [
                    {
                        "customer_id": customer_id,
                        "branch_id": branch_id,
                        "items": [{"cylinder_type_id": cyl_id, "quantity_ordered": 10}],
                    },
                    {
                        "customer_id": customer2_id,
                        "items": [{"cylinder_type_id": cyl_id, "quantity_ordered": 4}],
                    },
                ],
            },
        )
        assert res.status_code == 201, res.text
        created = res.json()
        delivery_id = created["id"]
        assert created["status"] == "pending", created
        assert len(created["orders"]) == 2, created
        orders_by_customer = {o["customer_id"]: o for o in created["orders"]}
        order_a_id = orders_by_customer[customer_id]["id"]
        order_b_id = orders_by_customer[customer2_id]["id"]

        # ---- assign the run to a vehicle + agent ----
        res = client.post(
            f"/api/v1/deliveries/{delivery_id}/assign",
            headers=manager,
            json={"vehicle_id": vehicle_id, "delivery_agent_id": agent_id},
        )
        assert res.status_code == 200 and res.json()["status"] == "assigned", res.text

        # ---- a second run (tomorrow) to move an order into ----
        res = client.post(
            "/api/v1/deliveries",
            headers=manager,
            json={
                "scheduled_date": str(date.today() + timedelta(days=1)),
                "vehicle_id": vehicle_id,
                "delivery_agent_id": agent_id,
                "orders": [
                    {
                        "customer_id": customer3_id,
                        "items": [{"cylinder_type_id": cyl_id, "quantity_ordered": 1}],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        delivery2_id = res.json()["id"]

        # ---- move order B (C2) from run 1 to run 2 (re-schedule) ----
        res = client.post(
            f"/api/v1/orders/{order_b_id}/move",
            headers=manager,
            json={"target_delivery_id": delivery2_id, "reason": "rescheduled to next day"},
        )
        assert res.status_code == 200 and res.json()["delivery_id"] == delivery2_id, res.text

        # ---- agent sees only their runs/orders ----
        res = client.get("/api/v1/deliveries", headers=agent)
        assert {d["id"] for d in res.json()} == {delivery_id, delivery2_id}, res.text
        res = client.get("/api/v1/orders", headers=agent)
        assert len(res.json()) == 3, res.text  # A, B, placeholder
        # query orders by customer
        res = client.get("/api/v1/orders", headers=agent, params={"customer_id": customer2_id})
        assert [o["id"] for o in res.json()] == [order_b_id], res.text
        # query orders by date (run 2 is tomorrow)
        res = client.get(
            "/api/v1/orders", headers=agent, params={"scheduled_date": str(date.today() + timedelta(days=1))}
        )
        assert {o["id"] for o in res.json()} == {order_b_id} or order_b_id in {o["id"] for o in res.json()}

        # ---- completing an order without evidence is blocked ----
        res = client.post(
            f"/api/v1/orders/{order_a_id}/complete",
            headers=agent,
            json={"items": [{"cylinder_type_id": cyl_id, "quantity_delivered": 10, "quantity_empty_collected": 8}]},
        )
        assert res.status_code == 400, "evidence should be required before completion"

        # ---- upload evidence + complete order A (stock: full out, empties in) ----
        res = client.post(
            f"/api/v1/orders/{order_a_id}/evidence",
            headers=agent,
            files={"file": ("proofA.png", b"fake-image-bytes", "image/png")},
        )
        assert res.status_code == 201, res.text
        ev = res.json()
        # Evidence is stored in the DB and exposed via an authenticated download URL
        # (no public file_path).
        assert "file_path" not in ev, ev
        assert ev["content_type"] == "image/png" and ev["size_bytes"] == len(b"fake-image-bytes"), ev
        evidence_url = ev["download_url"]
        assert evidence_url == f"/api/v1/orders/{order_a_id}/evidence/{ev['id']}", ev

        # download returns the exact bytes, and requires authentication
        res = client.get(evidence_url, headers=agent)
        assert res.status_code == 200 and res.content == b"fake-image-bytes", res.status_code
        assert res.headers["content-type"].startswith("image/png"), res.headers
        assert client.get(evidence_url).status_code == 401, "evidence download must require auth"

        # a second evidence is allowed (max 2), but a third is rejected
        res = client.post(
            f"/api/v1/orders/{order_a_id}/evidence",
            headers=agent,
            files={"file": ("proofA2.png", b"fake-image-bytes-2", "image/png")},
        )
        assert res.status_code == 201, res.text
        res = client.post(
            f"/api/v1/orders/{order_a_id}/evidence",
            headers=agent,
            files={"file": ("proofA3.png", b"fake-image-bytes-3", "image/png")},
        )
        assert res.status_code == 400, "third evidence upload should be rejected"

        res = client.post(
            f"/api/v1/orders/{order_a_id}/complete",
            headers=agent,
            json={"items": [{"cylinder_type_id": cyl_id, "quantity_delivered": 10, "quantity_empty_collected": 8}]},
        )
        assert res.status_code == 200 and res.json()["status"] == "completed", res.text

        # run 1 now has only the completed order A -> run is completed
        res = client.get(f"/api/v1/deliveries/{delivery_id}", headers=admin)
        assert res.json()["status"] == "completed", res.text

        # ---- complete order B in run 2 ----
        res = client.post(
            f"/api/v1/orders/{order_b_id}/evidence",
            headers=agent,
            files={"file": ("proofB.png", b"fake-image-bytes", "image/png")},
        )
        assert res.status_code == 201, res.text
        res = client.post(
            f"/api/v1/orders/{order_b_id}/complete",
            headers=agent,
            json={"items": [{"cylinder_type_id": cyl_id, "quantity_delivered": 4, "quantity_empty_collected": 3}]},
        )
        assert res.status_code == 200 and res.json()["status"] == "completed", res.text

        # ---- stock reflects both completions: 100 - 10 - 4 = 86 full; 10 + 8 + 3 = 21 empty ----
        res = client.get("/api/v1/stock/summary", headers=admin)
        item = next(i for i in res.json()["items"] if i["cylinder_type_id"] == cyl_id)
        assert item["full_quantity"] == 86 and item["empty_quantity"] == 21, item

        # ---- reports (today..tomorrow) ----
        res = client.get(
            "/api/v1/reports/date-range",
            headers=admin,
            params={"start_date": str(date.today()), "end_date": str(date.today() + timedelta(days=1))},
        )
        report = res.json()
        assert report["completed"] == 2, report
        assert report["total_full_delivered"] == 14 and report["total_empty_collected"] == 11, report

        res = client.get("/api/v1/reports/by-customer", headers=admin)
        by_customer = {r["customer_id"]: r for r in res.json()}
        assert by_customer[customer_id]["completed"] == 1, by_customer
        assert by_customer[customer_id]["total_full_delivered"] == 10, by_customer
        assert by_customer[customer2_id]["total_full_delivered"] == 4, by_customer

        # ===== Q1: a single order with MULTIPLE cylinder types completes correctly =====
        res = client.post("/api/v1/cylinder-types", headers=admin, json={"capacity_kg": 21, "name": "21kg"})
        assert res.status_code == 201, res.text
        cyl21_id = res.json()["id"]
        res = client.post(
            "/api/v1/stock/set",
            headers=admin,
            json={"cylinder_type_id": cyl21_id, "full_quantity": 50, "empty_quantity": 0},
        )
        assert res.status_code == 200, res.text

        # one delivery -> one order -> TWO cylinder types (17kg + 21kg)
        res = client.post(
            "/api/v1/deliveries",
            headers=manager,
            json={
                "scheduled_date": str(date.today()),
                "vehicle_id": vehicle_id,
                "delivery_agent_id": agent_id,
                "orders": [
                    {
                        "customer_id": customer3_id,
                        "items": [
                            {"cylinder_type_id": cyl_id, "quantity_ordered": 6},
                            {"cylinder_type_id": cyl21_id, "quantity_ordered": 4},
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        multi_order_id = res.json()["orders"][0]["id"]
        assert len(res.json()["orders"][0]["items"]) == 2, res.text

        res = client.post(
            f"/api/v1/orders/{multi_order_id}/evidence",
            headers=agent,
            files={"file": ("multi.png", b"img", "image/png")},
        )
        assert res.status_code == 201, res.text
        res = client.post(
            f"/api/v1/orders/{multi_order_id}/complete",
            headers=agent,
            json={
                "items": [
                    {"cylinder_type_id": cyl_id, "quantity_delivered": 6, "quantity_empty_collected": 5},
                    {"cylinder_type_id": cyl21_id, "quantity_delivered": 4, "quantity_empty_collected": 2},
                ]
            },
        )
        assert res.status_code == 200 and res.json()["status"] == "completed", res.text

        # stock for BOTH types updated: 17kg 86-6=80 / 21+5=26; 21kg 50-4=46 / 0+2=2
        res = client.get("/api/v1/stock/summary", headers=admin)
        by_type = {i["cylinder_type_id"]: i for i in res.json()["items"]}
        assert by_type[cyl_id]["full_quantity"] == 80 and by_type[cyl_id]["empty_quantity"] == 26, by_type
        assert by_type[cyl21_id]["full_quantity"] == 46 and by_type[cyl21_id]["empty_quantity"] == 2, by_type

        # ===== Q2: cylinders held by a customer, per cylinder type =====
        res = client.get(f"/api/v1/customers/{customer3_id}/cylinder-balance", headers=admin)
        bal = res.json()
        bal_by_type = {i["cylinder_type_id"]: i for i in bal["items"]}
        assert bal_by_type[cyl_id]["balance"] == 1, bal      # 6 delivered - 5 collected
        assert bal_by_type[cyl21_id]["balance"] == 2, bal    # 4 delivered - 2 collected
        assert bal["total_balance"] == 3, bal

        # customer A held: order A delivered 10 - collected 8 = 2 of 17kg
        res = client.get(f"/api/v1/customers/{customer_id}/cylinder-balance", headers=admin)
        assert res.json()["total_balance"] == 2, res.text

        # global holdings report lists every customer currently holding cylinders
        res = client.get("/api/v1/reports/customer-cylinder-balance", headers=admin)
        held = {c["customer_id"]: c["total_balance"] for c in res.json()}
        assert held.get(customer3_id) == 3 and held.get(customer_id) == 2, held

        # ===== Q3: per-customer, per-date delivery breakdown =====
        # C1 already has one completed delivery today (order A: 10 full / 8 empty).
        # Add a second completed delivery for C1 on a later date to get a 2nd date row.
        future = date.today() + timedelta(days=3)
        res = client.post(
            "/api/v1/deliveries",
            headers=manager,
            json={
                "scheduled_date": str(future),
                "vehicle_id": vehicle_id,
                "delivery_agent_id": agent_id,
                "orders": [
                    {
                        "customer_id": customer_id,
                        "branch_id": branch_id,
                        "items": [{"cylinder_type_id": cyl_id, "quantity_ordered": 7}],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        future_order_id = res.json()["orders"][0]["id"]
        res = client.post(
            f"/api/v1/orders/{future_order_id}/evidence",
            headers=agent,
            files={"file": ("future.png", b"img", "image/png")},
        )
        assert res.status_code == 201, res.text
        res = client.post(
            f"/api/v1/orders/{future_order_id}/complete",
            headers=agent,
            json={"items": [{"cylinder_type_id": cyl_id, "quantity_delivered": 7, "quantity_empty_collected": 6}]},
        )
        assert res.status_code == 200 and res.json()["status"] == "completed", res.text

        # daily breakdown for C1 spanning both delivery dates
        res = client.get(
            "/api/v1/reports/customer-daily",
            headers=admin,
            params={
                "customer_id": customer_id,
                "start_date": str(date.today()),
                "end_date": str(future),
            },
        )
        assert res.status_code == 200, res.text
        daily = res.json()
        assert daily["customer_id"] == customer_id, daily
        assert daily["total_full_delivered"] == 17, daily       # 10 + 7
        assert daily["total_empty_collected"] == 14, daily      # 8 + 6
        days = {d["delivery_date"]: d for d in daily["days"]}
        assert len(days) == 2, daily
        assert days[str(date.today())]["total_full_delivered"] == 10, daily
        assert days[str(date.today())]["total_empty_collected"] == 8, daily
        assert days[str(future)]["total_full_delivered"] == 7, daily
        assert days[str(future)]["total_empty_collected"] == 6, daily

        # a narrower range returns only the matching day
        res = client.get(
            "/api/v1/reports/customer-daily",
            headers=admin,
            params={
                "customer_id": customer_id,
                "start_date": str(future),
                "end_date": str(future),
            },
        )
        assert res.status_code == 200, res.text
        narrow = res.json()
        assert len(narrow["days"]) == 1, narrow
        assert narrow["total_full_delivered"] == 7, narrow

        # unknown customer -> 404
        res = client.get(
            "/api/v1/reports/customer-daily",
            headers=admin,
            params={"customer_id": 999999, "start_date": str(date.today()), "end_date": str(future)},
        )
        assert res.status_code == 404, res.text

        # ---- RBAC: agent cannot onboard customers ----
        res = client.post("/api/v1/customers", headers=agent, json={"code": "X", "name": "Nope"})
        assert res.status_code == 403, "delivery agent must not be allowed to create customers"

        # ===== Q4: refresh tokens + logout (revocation) =====
        res = client.post(
            "/api/v1/auth/login",
            data={"username": "smoke_mgr@a.com", "password": "manager123"},
        )
        assert res.status_code == 200, res.text
        tokens = res.json()
        assert tokens.get("access_token") and tokens.get("refresh_token"), tokens
        acc, ref = tokens["access_token"], tokens["refresh_token"]

        # access token authenticates
        assert client.get("/api/v1/auth/me", headers=_auth(acc)).status_code == 200

        # refresh -> brand new access token that also works
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": ref})
        assert res.status_code == 200, res.text
        new_acc = res.json()["access_token"]
        assert client.get("/api/v1/auth/me", headers=_auth(new_acc)).status_code == 200

        # token types are not interchangeable
        assert client.get("/api/v1/auth/me", headers=_auth(ref)).status_code == 401, \
            "a refresh token must not authenticate as an access token"
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": acc}).status_code == 401, \
            "an access token must not be accepted at /refresh"

        # logout revokes EVERY token for the user
        assert client.post("/api/v1/auth/logout", headers=_auth(new_acc)).status_code == 204
        assert client.get("/api/v1/auth/me", headers=_auth(acc)).status_code == 401, \
            "old access token must be revoked after logout"
        assert client.get("/api/v1/auth/me", headers=_auth(new_acc)).status_code == 401, \
            "refreshed access token must be revoked after logout"
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": ref}).status_code == 401, \
            "refresh token must be revoked after logout"

        # ===== Break-glass admin recovery (python -m app.manage reset-admin-password) =====
        # An existing admin session must die after a reset, the old password must
        # stop working, and the new one must work.
        old_admin_token = _login(client, "admin@delivery.com", "admin123")
        assert client.get("/api/v1/auth/me", headers=_auth(old_admin_token)).status_code == 200

        reset_admin_password(email="admin@delivery.com", password="NewBreakGlassPass!1")

        assert client.get("/api/v1/auth/me", headers=_auth(old_admin_token)).status_code == 401, \
            "existing admin sessions must be revoked after a password reset"
        res = client.post(
            "/api/v1/auth/login",
            data={"username": "admin@delivery.com", "password": "admin123"},
        )
        assert res.status_code == 401, "old admin password must stop working after a reset"
        assert _login(client, "admin@delivery.com", "NewBreakGlassPass!1"), \
            "admin must be able to log in with the reset password"

        # Restore the original admin password so later steps see a clean state.
        # The trailing successful login also clears the rate-limit counter before Q5.
        reset_admin_password(email="admin@delivery.com", password="admin123")
        assert _login(client, "admin@delivery.com", "admin123")

        # ===== Q5: login brute-force rate limiting (run LAST — it blocks the IP) =====
        for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
            r = client.post(
                "/api/v1/auth/login",
                data={"username": "admin@delivery.com", "password": "wrong-password"},
            )
            assert r.status_code == 401, r.text
        # Next attempt is blocked even though it's still a bad password.
        r = client.post(
            "/api/v1/auth/login",
            data={"username": "admin@delivery.com", "password": "wrong-password"},
        )
        assert r.status_code == 429, f"expected rate-limit 429, got {r.status_code}"

    print("ALL SMOKE TESTS PASSED \u2705")


def _cleanup() -> None:
    if os.path.exists("_smoke_test.db"):
        os.remove("_smoke_test.db")


if __name__ == "__main__":
    try:
        run()
    finally:
        _cleanup()
