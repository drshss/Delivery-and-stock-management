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

FastAPI · SQLAlchemy 2.0 · Alembic (migrations) · Pydantic v2 · JWT (python-jose) · bcrypt · SQLite (dev) / PostgreSQL (prod) · Docker.

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
  main.py      FastAPI app (dev: auto-creates tables; prod: Alembic-managed)
alembic/       database migrations (env.py + versions/)
alembic.ini    Alembic config (DB URL injected from app settings)
Dockerfile           container image (runs migrations, then Uvicorn)
docker-compose.yml   local prod-like stack: PostgreSQL + API
.env.example   environment template — copy to .env
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
| `POST /api/v1/auth/login`                  | public          | Login → access + refresh tokens (rate-limited) |
| `POST /api/v1/auth/refresh`                 | public          | Exchange a refresh token for a new access token |
| `POST /api/v1/auth/logout`                  | any             | Revoke all of the current user's tokens |
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
| `POST /api/v1/orders/{id}/evidence`         | agent/admin     | Upload proof photo (stored in DB)    |
| `GET  /api/v1/orders/{id}/evidence/{evidence_id}` | any (scoped) | Download a proof photo (authenticated) |
| `POST /api/v1/orders/{id}/complete`         | agent/admin     | Submit counts, complete, update stock|
| `POST /api/v1/orders/{id}/move`             | admin/manager   | Move an order to another run         |
| `GET  /api/v1/reports/date-range`           | admin/manager   | Date-wise report                     |
| `GET  /api/v1/reports/by-customer`          | admin/manager   | Customer-wise report                 |
| `GET  /api/v1/reports/pending-orders`       | admin/manager   | Incomplete orders to re-schedule     |

## Production deployment (external PostgreSQL)

In production the schema is managed by **Alembic migrations** (not auto-created),
secrets are **required**, and the app runs under multiple Uvicorn workers. The app
connects to an **external** PostgreSQL (managed RDS / Supabase / Neon / Cloud SQL,
or a DB on another host) — only `DATABASE_URL` changes, no code changes needed.

### Option A — Docker Compose (API only, external DB)

```bash
# 1. Create a .env from the template and fill in REAL values
cp .env.example .env
#    generate a strong secret:
python -c "import secrets; print(secrets.token_urlsafe(64))"
#    set DATABASE_URL to your external Postgres, e.g.
#    postgresql+psycopg://user:pass@db-host:5432/gas_delivery?sslmode=require

# 2. Build & start the API. It runs `alembic upgrade head` automatically
#    against the external DB before serving (see RUN_MIGRATIONS below).
docker compose up --build -d

# 3. Check health
curl http://localhost:8000/health
```

The `docker-compose.yml` runs **only the API container** and reads `DATABASE_URL`
straight from `.env`. Ensure the DB host is reachable from the container and that
your provider's firewall allowlists the server's egress IP.

### Option B — Manual / VM

```bash
pip install -r requirements.txt

export ENVIRONMENT=production
export SECRET_KEY="<64-char random string>"
export DATABASE_URL="postgresql+psycopg://user:pass@db-host:5432/gas_delivery?sslmode=require"
export BACKEND_CORS_ORIGINS="https://app.example.com,https://admin.example.com"
export FIRST_ADMIN_PASSWORD="<strong password>"

# Apply migrations, then serve
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
```

### Database migrations (Alembic)

```bash
alembic upgrade head                          # apply all migrations
alembic revision --autogenerate -m "message"  # create a migration after model changes
alembic downgrade -1                           # roll back one migration
```

> ⚠️ After **any** change to `app/models/`, generate a new migration and commit it.

**Multiple replicas:** by default each container runs migrations on startup
(`RUN_MIGRATIONS=1`). If you run more than one API replica against the same
external DB, set `RUN_MIGRATIONS=0` and apply migrations once as a one-off step
before rolling out, so replicas don't race:

```bash
docker compose run --rm api alembic upgrade head
```

### Connecting to external PostgreSQL

- **TLS:** append `?sslmode=require` to `DATABASE_URL` (most managed providers
  require it); use `verify-full` with a CA cert for stricter setups.
- **Password encoding:** URL-encode special characters (`@` → `%40`, `:` → `%3A`).
- **Connection pooling:** serverless Postgres (Supabase, Neon) expose a separate
  pooled endpoint (e.g. Supabase port `6543`, pgbouncer transaction mode). If you
  use it, keep `DB_POOL_SIZE` modest.


## ✅ Before you deploy — checklist

These are the changes/decisions to make before going live:

1. **Secrets (required in production)** — set in `.env` / your secret manager:
   - `ENVIRONMENT=production` (this **enforces** the checks below at startup).
   - `SECRET_KEY` — strong 64-char random value (`secrets.token_urlsafe(64)`).
   - `FIRST_ADMIN_PASSWORD` — strong; **change the bootstrap admin password** after first login.
   The app refuses to start in production if these are weak/default — see
   [`app/core/config.py`](app/core/config.py) `_enforce_production_safety`.

2. **Database (external PostgreSQL)** — set `DATABASE_URL`
   (`postgresql+psycopg://…?sslmode=require`). Ensure the DB host is reachable
   from the app and **allowlist the server's egress IP** in the provider's
   firewall / security group. Tune `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` for your
   load. Run `alembic upgrade head` on every deploy (or once for multi-replica —
   see `RUN_MIGRATIONS`).

3. **CORS** — set `BACKEND_CORS_ORIGINS` to your **explicit** web/app origins
   (comma-separated). Wildcard `*` is rejected in production.

4. **Rotate leaked credentials** — the old `creds` file and `delivery_app.db`
   were untracked from git; **rotate any passwords** that were committed previously.

5. **Delivery evidence storage** — proof-of-delivery photos are stored **in the
   database** (`order_evidences.data`) and served only via the authenticated
   endpoint `GET /orders/{id}/evidence/{evidence_id}`; nothing is written to
   local/ephemeral disk and there is no public `/uploads` path. For very high
   media volumes you may later externalise this to object storage (S3/GCS) with
   signed URLs, but it is not required to go live.

6. **TLS & reverse proxy** — terminate HTTPS at a proxy (nginx / cloud LB) in
   front of the app; the server already honours `--proxy-headers`.

7. **Backups & monitoring** — most managed Postgres providers handle automated
   backups (confirm it's enabled and set retention); otherwise schedule your own.
   Wire up the `/health` endpoint to your uptime/monitoring checks.


8. **Do not seed prod** — `seed.py` and the dev seed users are for development only.

## Security & observability

- **Structured logging** — logs go to **stdout** so Cloud Run / Azure App Service /
  AWS / a plain VM capture them automatically. In production each line is **JSON**
  (with a GCP-friendly `severity` field and a per-request `request_id`); in dev it's
  human-readable. Configure via `LOG_LEVEL` / `LOG_FORMAT`. See
  [`app/core/logging_config.py`](app/core/logging_config.py).
- **Request correlation** — every request gets an `X-Request-ID` (honouring an
  inbound `X-Request-ID` / `X-Cloud-Trace-Context`), logged with method, path,
  status and duration.
- **Optional error tracking** — set `SENTRY_DSN` (and `pip install sentry-sdk`) to
  enable Sentry; otherwise it's completely inert.
- **Login brute-force protection** — failed logins are rate-limited per client IP
  (`LOGIN_MAX_FAILED_ATTEMPTS` / `LOGIN_ATTEMPT_WINDOW_SECONDS`), returning **429**
  once exceeded. In-memory by default; back it with Redis for exact cluster-wide
  limits. See [`app/core/rate_limit.py`](app/core/rate_limit.py).
- **Token revocation & refresh** — login issues a short-lived **access token** plus
  a longer-lived **refresh token** (`/auth/refresh`). Each token carries the user's
  `token_version`; `/auth/logout` (and any password change) bumps it, instantly
  revoking all outstanding tokens for that user.
- **Admin account recovery (break-glass)** — locked out of the admin account? Run a
  one-off command **inside a container** (no email/SMTP needed). It resets the
  password, reactivates the account and revokes all of its sessions:
  ```bash
  # Provide the new password out-of-band so it stays out of shell history.
  # Docker / Compose:
  docker compose run --rm -e RESET_ADMIN_PASSWORD='new-strong-password' \
      api python -m app.manage reset-admin-password --email admin@yourcompany.com
  # Kubernetes:
  kubectl exec deploy/api -- env RESET_ADMIN_PASSWORD='new-strong-password' \
      python -m app.manage reset-admin-password --email admin@yourcompany.com
  # Cloud Run Jobs / ECS run-task: set the container command to the same line.
  ```
  Nothing runs automatically on restart, so there's no risk of silently
  re-resetting the password. See [`app/manage.py`](app/manage.py).

## Scaling notes (future-ready)

- Branch `latitude`/`longitude` are stored already, ready for automatic route mapping.
- Stateless JWT auth scales horizontally behind a load balancer.
- Stock completion row-locks the stock record (`SELECT … FOR UPDATE`) so
  concurrent deliveries can't corrupt counts on PostgreSQL.
- The status state-machine and assignment history support customer-request and payment modules later.

