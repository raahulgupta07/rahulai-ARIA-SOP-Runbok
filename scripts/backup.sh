#!/usr/bin/env bash
# Backup DocSensei: Postgres dump + page images/uploads. Retains the last N.
# Run from anywhere: scripts/backup.sh   (cron-friendly)
#
# Env overrides: BACKUP_DIR, BACKUP_KEEP (default 7), DB_CONTAINER, POSTGRES_USER,
# POSTGRES_DB, BACKUP_S3_BUCKET (optional off-box push, needs aws cli).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${BACKUP_DIR:-$HERE/backups}"
KEEP="${BACKUP_KEEP:-7}"
DB_CONTAINER="${DB_CONTAINER:-docsensei-db}"
DB_USER="${POSTGRES_USER:-docsensei}"
DB_NAME="${POSTGRES_DB:-docsensei}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT"

echo "→ pg_dump $DB_NAME"
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$OUT/db-$STAMP.dump"

echo "→ data (page images + uploads)"
tar -czf "$OUT/data-$STAMP.tar.gz" -C "$HERE" data/pages data/uploads 2>/dev/null || \
  echo "  (no data dir — skipped)"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  echo "→ off-box push to s3://$BACKUP_S3_BUCKET"
  aws s3 cp "$OUT/db-$STAMP.dump"      "s3://$BACKUP_S3_BUCKET/" && \
  aws s3 cp "$OUT/data-$STAMP.tar.gz"  "s3://$BACKUP_S3_BUCKET/" || echo "  s3 push failed"
fi

# retention: keep newest $KEEP of each kind
ls -1t "$OUT"/db-*.dump      2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
ls -1t "$OUT"/data-*.tar.gz  2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f

echo "✓ backup $STAMP → $OUT (keeping last $KEEP)"
