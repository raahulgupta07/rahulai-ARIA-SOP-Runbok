#!/usr/bin/env bash
# Restore DocSensei from a backup produced by backup.sh.
#   scripts/restore.sh <db-YYYYMMDD-HHMMSS.dump> [data-YYYYMMDD-HHMMSS.tar.gz]
# Drops existing objects and reloads. Restart the app container afterwards.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DB_CONTAINER="${DB_CONTAINER:-docsensei-db}"
DB_USER="${POSTGRES_USER:-docsensei}"
DB_NAME="${POSTGRES_DB:-docsensei}"

DUMP="${1:?usage: restore.sh <db.dump> [data.tar.gz]}"
[ -f "$DUMP" ] || { echo "no such dump: $DUMP"; exit 1; }

echo "→ restoring $DUMP into $DB_NAME (existing objects dropped)"
docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" \
  --clean --if-exists --no-owner < "$DUMP"

if [ -n "${2:-}" ]; then
  echo "→ restoring data $2"
  tar -xzf "$2" -C "$HERE"
fi

echo "✓ restore done — run: docker compose up -d --force-recreate app"
