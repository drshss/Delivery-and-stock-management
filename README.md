# Gas Cylinder Delivery & Stock Management — FastAPI Backend

Backend API for a commercial gas-cylinder distribution business. It powers both a
web app and an Android app, and is designed to scale toward automatic route
mapping, customer self-service requests and payments.

## Features

- **Authentication & roles** — JWT login with three roles:
  - **Admin** — full control: stock, cylinder types, customers, vehicles, users, reports.
  - **Stock manager** — allocates deliveries (customer, cylinder type, date, vehicle, agent) and re-assigns/moves them.
  - **Delivery agent** — sees only their own deliveries, uploads proof photos and marks each order complete.
- **Stock management** — full/empty quantities tracked **per cylinder type** (17kg, 21kg, 33kg…), with a full transaction audit trail and a summary endpoint (total / full / empty). Supports **adjust** (delta), **set** (absolute stocktake) and **release** (take full cylinders out).
- **Customers & branches** — onboard customers, each with multiple sub-branches that have unique codes and geo-coordinates (reserved for future map routing).
- **Deliveries → Orders** — a **delivery run** is one **vehicle + agent + date** and contains **many orders** (one per customer/branch stop). Each order has its own cylinder line items, its own photo evidence, and is completed independently — completing an order updates stock automatically (full out, empties in).
- **Re-assignment & re-scheduling** — re-assign a whole run to a different vehicle/agent/date, or **move a single order** to a different run, both with full audit history.
- **Querying** — list deliveries and orders filtered by status, customer, vehicle, agent or date.
- **Reports** — date-range and per-customer summaries, plus a pending-orders list for re-scheduling.

## Tech stack

FastAPI · SQLAlchemy 2.0 · Pydantic v2 · JWT (python-jose) · passlib/bcrypt · SQLite (dev) / PostgreSQL (prod).

## Project structure

```
app/
  core/        config, database, security (JWT & hashing)
  models/      SQLAlchemy ORM models
  schemas/     Pydantic request/response models
  services/    business helpers (delivery numbering & status rollup)
  api/
    deps.py    auth & role dependencies
    routes/    auth, users, customers, cylinders, stock, vehicles, deliveries, orders, reports
  main.py      FastAPI app (startup creates tables + bootstrap admin)
seed.py        sample data for development
```

### Data model

```
Delivery (run: vehicle + agent + date)
└── Order (one stop per customer / branch)   ← order_number, evidence & completion live here
    └── OrderItem (cylinder type + quantities: ordered / delivered / empties collected)
```

A delivery's status is rolled up automatically from its orders
(pending → assigned → in_transit → completed, or cancelled).

## Getting started

```bash
# 1. Create & activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (optional) copy env file and adjust secrets
cp .env.example .env

# 4. Seed sample data (optional but recommended)
python seed.py

# 5. Run the API
uvicorn app.main:app --reload
```

Then open the interactive docs at **http://127.0.0.1:8000/docs**.

A bootstrap admin is created automatically on first run using the `FIRST_ADMIN_*`
values in `.env` (default `admin@delivery.com` / `admin123`).

### Seed users

| Role          | Email                  | Password    |
|---------------|------------------------|-------------|
| Admin         | admin@delivery.com     | admin123    |
| Stock manager | manager@delivery.com   | manager123  |
| Delivery agent| agent@delivery.com     | agent123    |

## Typical workflow

1. **Admin** adds cylinder types, sets opening stock, onboards customers/branches, adds vehicles, creates users.
2. **Stock manager** creates a **delivery run** (`POST /deliveries`) containing one or more **orders** (one per customer/branch), then assigns a vehicle + delivery agent (`POST /deliveries/{id}/assign`). More orders can be added with `POST /deliveries/{id}/orders`.
3. **Delivery agent** opens each order, uploads a photo (`POST /orders/{id}/evidence`), then submits delivered/empty counts (`POST /orders/{id}/complete`). Stock updates automatically (full out, empties in). When all orders are done, the run is marked completed.
4. If an order is not completed, the **stock manager** moves it to another run with a different date/vehicle/agent (`POST /orders/{id}/move`), or re-assigns the whole run (`POST /deliveries/{id}/assign`).
5. **Admin/manager** review `/reports/date-range`, `/reports/by-customer` and `/reports/pending-orders`.

## Key API endpoints

| Method & path                              | Role            | Purpose                              |
|--------------------------------------------|-----------------|--------------------------------------|
| `POST /api/v1/auth/login`                  | public          | Get JWT token (username = email)     |
| `GET  /api/v1/auth/me`                      | any             | Current user                         |
| `POST /api/v1/users`                        | admin           | Create user                          |
| `GET  /api/v1/users/delivery-agents`        | admin/manager   | List agents for assignment           |
| `POST /api/v1/customers`                    | admin           | Onboard customer                     |
| `POST /api/v1/customers/{id}/branches`      | admin           | Add sub-branch                       |
| `POST /api/v1/cylinder-types`               | admin           | Add cylinder type (e.g. 17kg)        |
| `GET  /api/v1/stock/summary`                | admin/manager   | Full/empty totals by type            |
| `POST /api/v1/stock/adjust`                 | admin           | Adjust stock by a delta (e.g. purchase) |
| `POST /api/v1/stock/set`                    | admin           | Set absolute full/empty (stocktake)  |
| `POST /api/v1/stock/release`                | admin           | Release full cylinders out of stock  |
| `POST /api/v1/vehicles`                     | admin           | Add vehicle                          |
| `POST /api/v1/deliveries`                   | admin/manager   | Create a run with one or more orders |
| `POST /api/v1/deliveries/{id}/orders`       | admin/manager   | Add another customer order to a run  |
| `POST /api/v1/deliveries/{id}/assign`       | admin/manager   | Assign / re-assign vehicle+agent+date|
| `GET  /api/v1/deliveries`                   | any (scoped)    | List runs; filter status/vehicle/agent/customer/date |
| `GET  /api/v1/orders`                       | any (scoped)    | List orders; filter status/customer/branch/vehicle/agent/date |
| `POST /api/v1/orders/{id}/evidence`         | agent/admin     | Upload proof photo for an order      |
| `POST /api/v1/orders/{id}/complete`         | agent/admin     | Submit counts, complete, update stock|
| `POST /api/v1/orders/{id}/move`             | admin/manager   | Move an order to another run         |
| `GET  /api/v1/reports/date-range`           | admin/manager   | Date-wise report                     |
| `GET  /api/v1/reports/by-customer`          | admin/manager   | Customer-wise report                 |
| `GET  /api/v1/reports/pending-orders`       | admin/manager   | Incomplete orders to re-schedule     |

## Scaling notes (future-ready)

- Switch `DATABASE_URL` to PostgreSQL (`postgresql+psycopg://…`) — no code changes needed.
- Branch `latitude`/`longitude` are stored already, ready for automatic route mapping.
- Stateless JWT auth scales horizontally behind a load balancer.
- The status state-machine and assignment history support customer-request and payment modules later.
- Move evidence uploads to object storage (S3/GCS) and serve via signed URLs in production.
