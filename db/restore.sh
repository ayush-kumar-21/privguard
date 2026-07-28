#!/usr/bin/env bash
# PrivGuard database restore script.
# Usage: ./db/restore.sh <dump_file> [database_name]
set -euo pipefail

DUMP_FILE="${1:?Usage: restore.sh <dump_file> [database_name]}"
DB_NAME="${2:-safehealth}"

echo "==> Restoring ${DUMP_FILE} into ${DB_NAME} ..."
pg_restore --clean --if-exists --no-owner -d "$DB_NAME" "$DUMP_FILE"

echo "==> Running Django migrations (in case the dump is older than the code)..."
cd "$(dirname "$0")/../backend"
python manage.py migrate --noinput

echo "==> Verifying ledger hash chain..."
python manage.py verify_budgets --strict

echo "==> Restore complete."
