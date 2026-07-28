#!/usr/bin/env bash
# PrivGuard database backup script.
# Usage: ./db/backup.sh [database_name]
set -euo pipefail

DB_NAME="${1:-safehealth}"
BACKUP_DIR="$(dirname "$0")/../backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%F_%H%M)
DUMP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.dump"

echo "==> Backing up ${DB_NAME} to ${DUMP_FILE} ..."
pg_dump -Fc -f "$DUMP_FILE" "$DB_NAME"

echo "==> Backup complete: $(du -h "$DUMP_FILE" | cut -f1)"
echo "    Verify with: ./db/verify_restore.sh $DUMP_FILE"
