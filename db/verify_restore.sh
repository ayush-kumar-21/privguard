#!/usr/bin/env bash
# Prove a backup is usable: restore into a scratch database, verify, drop it.
# Usage: ./db/verify_restore.sh <dump_file>
set -euo pipefail

DUMP_FILE="${1:?Usage: verify_restore.sh <dump_file>}"
VERIFY_DB="safehealth_verify"

cleanup() { dropdb --if-exists "$VERIFY_DB" 2>/dev/null; }
trap cleanup EXIT

echo "==> Creating scratch database ${VERIFY_DB} ..."
createdb "$VERIFY_DB"

echo "==> Restoring dump ..."
pg_restore --no-owner -d "$VERIFY_DB" "$DUMP_FILE"

echo "==> Running migrations ..."
cd "$(dirname "$0")/../backend"
DATABASE_URL="postgres://localhost:5432/${VERIFY_DB}" python manage.py migrate --check

echo "==> Verifying budget ledger integrity ..."
DATABASE_URL="postgres://localhost:5432/${VERIFY_DB}" python manage.py verify_budgets --strict

echo "==> Verification PASSED. Cleaning up ..."
# cleanup runs via trap
