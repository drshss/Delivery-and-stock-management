"""Password hashing (bcrypt) and JWT token helpers."""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt

from app.core.config import settings

# bcrypt only uses the first 72 bytes of a password.
_BCRYPT_MAX_BYTES = 72

# JWT "type" claim values, used to keep access and refresh tokens distinct.
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def _to_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain_password), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def _create_token(
    subject: str | int,
    token_type: str,
    token_version: int,
    expires_delta: timedelta,
    extra_claims: dict | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": str(subject),
        "type": token_type,
        "ver": token_version,  # must match User.token_version, else token is revoked
        "exp": expire,
        **(extra_claims or {}),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(
    subject: str | int,
    role: str,
    token_version: int,
    expires_delta: timedelta | None = None,
) -> str:
    return _create_token(
        subject,
        ACCESS_TOKEN_TYPE,
        token_version,
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"role": role},
    )


def create_refresh_token(
    subject: str | int,
    token_version: int,
    expires_delta: timedelta | None = None,
) -> str:
    return _create_token(
        subject,
        REFRESH_TOKEN_TYPE,
        token_version,
        expires_delta or timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    )


def decode_token(token: str) -> dict:
    """Decode & verify a JWT (signature + expiry). Raises jose.JWTError if invalid."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

