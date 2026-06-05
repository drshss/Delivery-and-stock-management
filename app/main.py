"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload
Docs at:   http://127.0.0.1:8000/docs
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User


def _create_first_admin() -> None:
    """Create the bootstrap admin account if it does not exist yet."""
    db = SessionLocal()
    try:
        exists = db.query(User).filter(User.email == settings.FIRST_ADMIN_EMAIL).first()
        if not exists:
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
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables, upload dir and bootstrap admin on startup.
    Base.metadata.create_all(bind=engine)
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded delivery evidence (front a CDN / signed URLs in production).
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

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
