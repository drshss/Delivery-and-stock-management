"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload
Docs at:   http://127.0.0.1:8000/docs
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError

from app import models  # noqa: F401  (ensures all models are registered)
from app.api.routes import (
    auth,
    customers,
    cylinders,
    deliveries,
    orders,
    reports,
    stock,
    users,
    vehicles,
)
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.logging_config import request_id_ctx, setup_logging
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User

# Configure structured logging to stdout before anything else logs.
setup_logging()
logger = logging.getLogger(__name__)

# Optional Sentry error tracking — inert unless SENTRY_DSN is set AND sentry-sdk
# is installed, so it never gets in the way of plain console-log deployments.
if settings.SENTRY_DSN:
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.0,
        )
        logger.info("Sentry error tracking enabled")
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping Sentry init")



def _create_first_admin() -> None:
    """Create the bootstrap admin account if it does not exist yet.

    Idempotent and concurrency-safe. With multiple Uvicorn workers this runs once
    per worker process, so they can race to insert the same email on a fresh DB.
    The UNIQUE(email) constraint lets exactly one win; the losers catch the
    IntegrityError and roll back instead of crashing the whole app on startup.
    """
    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.email == settings.FIRST_ADMIN_EMAIL).first()
        if exists:
            return
        db.add(
            User(
                full_name=settings.FIRST_ADMIN_NAME,
                email=settings.FIRST_ADMIN_EMAIL,
                role=UserRole.ADMIN,
                hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
                is_active=True,
            )
        )
        db.commit()
        logger.info("Bootstrap admin %s created", settings.FIRST_ADMIN_EMAIL)
    except IntegrityError:
        # Another worker won the race and created the admin first — expected on a
        # fresh database under multiple workers; not an error.
        db.rollback()
        logger.info("Bootstrap admin already exists (created concurrently); skipping")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # In development we auto-create tables for convenience. In production the
    # schema is owned by Alembic migrations (run `alembic upgrade head` on deploy).
    if not settings.is_production:
        Base.metadata.create_all(bind=engine)
    _create_first_admin()
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Backend API for a commercial gas-cylinder distribution business: "
        "stock management (full/empty by type), customer & branch onboarding, "
        "vehicle/agent delivery assignment, proof-of-delivery capture and reporting."
    ),
    lifespan=lifespan,
)

_cors_origins = settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Credentialed CORS is invalid with the "*" wildcard, so only enable
    # credentials when explicit origins are configured.
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Assign a correlation id to each request and log method/path/status/timing."""
    request_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-cloud-trace-context", "").split("/")[0]
        or uuid.uuid4().hex
    )
    ctx_token = request_id_ctx.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.exception(
            "request failed",
            extra={"method": request.method, "path": request.url.path, "duration_ms": duration_ms},
        )
        request_id_ctx.reset(ctx_token)
        raise

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    # Skip noisy health-check access logs.
    if request.url.path != "/health":
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
    response.headers["X-Request-ID"] = request_id
    request_id_ctx.reset(ctx_token)
    return response


api = settings.API_V1_PREFIX
app.include_router(auth.router, prefix=f"{api}/auth", tags=["Auth"])
app.include_router(users.router, prefix=f"{api}/users", tags=["Users"])
app.include_router(customers.router, prefix=f"{api}/customers", tags=["Customers"])
app.include_router(cylinders.router, prefix=f"{api}/cylinder-types", tags=["Cylinder Types"])
app.include_router(stock.router, prefix=f"{api}/stock", tags=["Stock"])
app.include_router(vehicles.router, prefix=f"{api}/vehicles", tags=["Vehicles"])
app.include_router(deliveries.router, prefix=f"{api}/deliveries", tags=["Deliveries"])
app.include_router(orders.router, prefix=f"{api}/orders", tags=["Orders"])
app.include_router(reports.router, prefix=f"{api}/reports", tags=["Reports"])


@app.get("/", tags=["Meta"])
def root():
    return {"name": settings.PROJECT_NAME, "version": settings.VERSION, "docs": "/docs"}


@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok"}
