#!/usr/bin/env bash
# Container entrypoint: optionally apply DB migrations, then start the API server.
set -euo pipefail

# Migrations run on startup by default (RUN_MIGRATIONS=1). For multi-replica
# deploys against a shared external database, set RUN_MIGRATIONS=0 and run
# migrations ONCE as a separate one-off step so replicas don't race:
#   docker compose run --rm api alembic upgrade head
if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
    echo "==> Applying database migrations (alembic upgrade head)..."
    alembic upgrade head
else
    echo "==> Skipping migrations (RUN_MIGRATIONS=${RUN_MIGRATIONS:-0})."
fi

echo "==> Starting Uvicorn on 0.0.0.0:${PORT:-8000} with ${WEB_CONCURRENCY:-4} workers..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-4}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
