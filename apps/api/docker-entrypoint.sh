#!/usr/bin/env bash
#
# Container entrypoint for the hosted API.
#
# Render's free plan has no pre-deploy hook, so the migration runs here, at container start,
# immediately before the server binds. `alembic upgrade head` is idempotent — on every deploy
# after the first it finds nothing to do and exits — so this is safe to run unconditionally.
#
# This is sound because the free plan runs a single instance. With several instances starting at
# once they would race to take the migration lock, and the migration would belong in a deploy
# hook instead. That is a real limit of this arrangement rather than something to paper over.
set -euo pipefail

echo "==> Applying database migrations"
alembic upgrade head

echo "==> Starting the API on port ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
