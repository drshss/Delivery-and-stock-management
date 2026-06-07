"""Authentication: login, token refresh, logout (revoke), and current-user lookup."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limit import get_client_ip, login_rate_limiter
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.user import User
from app.schemas.token import AccessTokenOut, RefreshRequest, Token
from app.schemas.user import UserOut

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/login", response_model=Token, summary="Login and get access + refresh tokens")
def login(
    request: Request,
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Use `username` = email and `password`. Returns a Bearer access token and a
    refresh token. Repeated failed attempts from one IP are rate-limited."""
    client_ip = get_client_ip(request)
    if login_rate_limiter.is_blocked(client_ip):
        logger.warning("login blocked: too many failed attempts", extra={"client_ip": client_ip})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        login_rate_limiter.record_failure(client_ip)
        logger.info("login failed", extra={"client_ip": client_ip, "email": form_data.username})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    # Successful login clears the failure counter for this IP.
    login_rate_limiter.reset(client_ip)
    logger.info("login succeeded", extra={"user_id": user.id})
    return Token(
        access_token=create_access_token(
            subject=user.id, role=user.role.value, token_version=user.token_version
        ),
        refresh_token=create_refresh_token(
            subject=user.id, token_version=user.token_version
        ),
    )


@router.post("/refresh", response_model=AccessTokenOut, summary="Exchange a refresh token for a new access token")
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = decode_token(payload.refresh_token)
        user_id = claims.get("sub")
        if user_id is None or claims.get("type") != REFRESH_TOKEN_TYPE:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.get(User, int(user_id))
    if user is None or not user.is_active or claims.get("ver") != user.token_version:
        raise credentials_exception

    return AccessTokenOut(
        access_token=create_access_token(
            subject=user.id, role=user.role.value, token_version=user.token_version
        )
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all of the current user's tokens (logout everywhere)",
)
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bumps the user's token_version, immediately invalidating every existing
    access and refresh token issued to them."""
    current_user.token_version += 1
    db.commit()
    logger.info("user logged out (tokens revoked)", extra={"user_id": current_user.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, summary="Get the current authenticated user")
def read_me(current_user: User = Depends(get_current_user)):
    return current_user

