"""In-memory brute-force protection for the login endpoint.

Tracks **failed** login attempts per client IP within a sliding window. A
successful login clears the counter. Once the threshold is reached the IP is
blocked until the window elapses.

NOTE: state is per-process, so with multiple workers/instances the effective
limit is multiplied by the number of processes. That is still a meaningful
brute-force deterrent and requires no external services. For exact,
cluster-wide limiting, back this with Redis (swap out the `_failures` store).
"""
import threading
import time

from fastapi import Request

from app.core.config import settings


class _LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        timestamps = [ts for ts in self._failures.get(key, []) if ts > cutoff]
        if timestamps:
            self._failures[key] = timestamps
        else:
            self._failures.pop(key, None)
        return timestamps

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            timestamps = self._prune(key, now)
            timestamps.append(now)
            self._failures[key] = timestamps

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_rate_limiter = _LoginRateLimiter(
    max_attempts=settings.LOGIN_MAX_FAILED_ATTEMPTS,
    window_seconds=settings.LOGIN_ATTEMPT_WINDOW_SECONDS,
)


def get_client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting.

    We intentionally trust only ``request.client.host`` (which Uvicorn populates
    from ``X-Forwarded-For`` only when the immediate sender is in
    ``--forwarded-allow-ips``). Reading raw forwarding headers here would bypass
    that trust boundary and allow client-controlled spoofing.
    """
    return request.client.host if request.client else "unknown"
