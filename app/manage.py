"""Operational CLI for the delivery & stock API.

Run as a one-off command **inside a container** — works the same on every
platform, so you don't have to commit to one yet:

    # Docker / Compose
    docker compose run --rm -e RESET_ADMIN_PASSWORD='new-strong-pass' api \
        python -m app.manage reset-admin-password

    # Kubernetes
    kubectl exec deploy/api -- env RESET_ADMIN_PASSWORD='new-strong-pass' \
        python -m app.manage reset-admin-password

    # Cloud Run Jobs / ECS run-task: set the container command to the above.

Nothing here runs automatically on startup, so there is no risk of silently
re-resetting the admin password on every restart.
"""
import argparse
import getpass
import logging
import os
import sys

from app import models  # noqa: F401  (register all ORM models for mapper config)
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User

logger = logging.getLogger(__name__)

# Minimum password length accepted without --force (matches the production
# policy enforced in app/core/config.py).
_MIN_PASSWORD_LENGTH = 10


class AdminNotFoundError(LookupError):
    """Raised when the target account does not exist."""


def reset_admin_password(*, email: str, password: str) -> User:
    """Reset a user's password, reactivate the account and revoke every existing
    session for it (by bumping ``token_version``).

    This is the break-glass recovery primitive. It performs no policy validation
    so it can be reused and unit-tested directly; the CLI layer enforces password
    strength.

    Raises:
        AdminNotFoundError: if no user has the given email.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise AdminNotFoundError(email)
        user.hashed_password = hash_password(password)
        user.is_active = True
        user.token_version += 1  # revokes all outstanding access/refresh tokens
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _resolve_password(arg_password: str | None) -> str:
    """Resolve the new password, in priority order: the ``--password`` flag, the
    ``RESET_ADMIN_PASSWORD`` env var, or an interactive prompt (when on a TTY)."""
    if arg_password:
        return arg_password
    env_password = os.environ.get("RESET_ADMIN_PASSWORD")
    if env_password:
        return env_password
    if sys.stdin.isatty():
        first = getpass.getpass("New admin password: ")
        second = getpass.getpass("Confirm new password: ")
        if first != second:
            raise SystemExit("error: passwords did not match.")
        return first
    raise SystemExit(
        "error: no password provided. Pass --password, set RESET_ADMIN_PASSWORD, "
        "or run the command interactively."
    )


def _cmd_reset_admin_password(args: argparse.Namespace) -> int:
    password = _resolve_password(args.password)
    if not args.force and len(password) < _MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"error: password must be at least {_MIN_PASSWORD_LENGTH} characters "
            "(use --force to override)."
        )
    try:
        user = reset_admin_password(email=args.email, password=password)
    except AdminNotFoundError as exc:
        raise SystemExit(f"error: no user found with email {args.email!r}.") from exc
    print(
        f"\u2713 Password reset for {user.email} (role={user.role.value}). "
        "All of its existing sessions have been revoked."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.manage",
        description="Operational commands for the delivery & stock API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser(
        "reset-admin-password",
        help="Reset an admin's password and revoke its sessions (break-glass recovery).",
        description=(
            "Reset the password for the account identified by --email (defaults to "
            "FIRST_ADMIN_EMAIL), reactivate it, and revoke all of its existing "
            "sessions. The new password comes from --password, the "
            "RESET_ADMIN_PASSWORD env var, or an interactive prompt."
        ),
    )
    reset.add_argument(
        "--email",
        default=settings.FIRST_ADMIN_EMAIL,
        help="Email of the account to reset (default: FIRST_ADMIN_EMAIL).",
    )
    reset.add_argument(
        "--password",
        default=None,
        help=(
            "New password. Prefer the RESET_ADMIN_PASSWORD env var or the "
            "interactive prompt to keep it out of shell history."
        ),
    )
    reset.add_argument(
        "--force",
        action="store_true",
        help=f"Skip the minimum-length check ({_MIN_PASSWORD_LENGTH} chars).",
    )
    reset.set_defaults(func=_cmd_reset_admin_password)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
