#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

# Deliberately one worker. Inbound deduplication, per-conversation turn
# serialisation and the scheduler all live in process memory, so a second worker
# does not error - it silently answers Cordia twice and sends the morning brief
# twice. app/main.py refuses to boot if this is overridden by env var.
echo "Starting Cordia..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
