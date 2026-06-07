"""Structured logging setup.

Logs are written to **stdout** so any container platform (Cloud Run, Azure App
Service, AWS ECS/App Runner, a plain VM under systemd, …) captures them
automatically. In production we emit one JSON object per line — including a
GCP-friendly ``severity`` field and a per-request ``request_id`` — so the logs
are searchable/indexable. In development we use a compact human-readable format.
"""
import json
import logging
import sys
from contextvars import ContextVar

from app.core.config import settings

# Per-request correlation id, populated by the request-logging middleware.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Standard LogRecord attributes we don't want to duplicate as "extra" fields.
_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal, dependency-free JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,  # GCP Cloud Logging reads this field
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Include any structured `extra={...}` fields passed by callers.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    """Configure root + uvicorn loggers to write structured logs to stdout."""
    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # Route uvicorn/gunicorn logs through our handler (no duplicate lines).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
