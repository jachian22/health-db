#!/bin/sh
set -eu

PORT="${PORT:-8000}"
echo "starting health-db on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
