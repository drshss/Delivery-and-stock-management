"""Demonstrate the auth flow for the protected /users endpoints (in-process)."""
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    # 1) Calling a protected endpoint WITHOUT a token -> "Not authenticated"
    r = client.get("/api/v1/users")
    print("1) GET /users  no token  ->", r.status_code, r.json())

    # 2) Log in as ADMIN (form-encoded; username = email)
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "admin@delivery.com", "password": "admin123"},
    )
    print("2) login admin          ->", r.status_code)
    admin_token = r.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3) Same call WITH the admin token -> works
    r = client.get("/api/v1/users", headers=admin_headers)
    print("3) GET /users  + token   ->", r.status_code, f"({len(r.json())} users)")

    # 4) Adding a user as admin -> works
    r = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "full_name": "Demo Agent",
            "email": "demo_agent@example.com",
            "role": "delivery_agent",
            "password": "demo1234",
        },
    )
    print("4) POST /users + admin   ->", r.status_code, r.json().get("email", r.json()))

    # 5) Logging in as MANAGER works, but managers are NOT allowed to add users -> 403
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "manager@delivery.com", "password": "manager123"},
    )
    print("5) login manager        ->", r.status_code)
    mgr_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.post(
        "/api/v1/users",
        headers=mgr_headers,
        json={
            "full_name": "X",
            "email": "x@example.com",
            "role": "delivery_agent",
            "password": "x1234567",
        },
    )
    print("6) POST /users + manager ->", r.status_code, r.json())
