#!/usr/bin/env bash
# =============================================================================
# City Agent Aria — one-command UPGRADE (data-safe)
#
#   sudo bash update.sh
#
# Backs up the database, pulls the new code, rebuilds the image, and recreates
# the app + worker. Your data is PRESERVED: Postgres lives in the docsensei_pg
# volume and page images in ./data — neither is touched by a rebuild. The DB
# schema auto-migrates on boot (idempotent). Nothing else to do.
#
# Detects which stack is running (npm vs built-in-nginx). Override: COMPOSE=... .
# =============================================================================
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "Run with sudo:  sudo bash update.sh"
[ -f .env.prod ] || die "No .env.prod — run install.sh first."

DC="docker compose"
# pick the compose file that's actually running (npm or built-in nginx)
COMPOSE="${COMPOSE:-}"
if [ -z "$COMPOSE" ]; then
  if $DC -f docker-compose.npm.yml --env-file .env.prod ps -q app >/dev/null 2>&1 && [ -n "$($DC -f docker-compose.npm.yml --env-file .env.prod ps -q app 2>/dev/null)" ]; then
    COMPOSE=docker-compose.npm.yml
  else
    COMPOSE=docker-compose.prod.yml
  fi
fi
say "Using $COMPOSE"

# 1) backup the database first (safety)
say "Backing up the database"
TS="$(date +%Y%m%d_%H%M%S)"; mkdir -p backups
$DC -f "$COMPOSE" --env-file .env.prod exec -T db pg_dump -U docsensei -Fc docsensei > "backups/db_${TS}.dump" \
  && echo "  saved backups/db_${TS}.dump" || echo "  (backup skipped — db not up yet)"

# 2) pull the new code (if this is a git checkout)
if [ -d .git ]; then say "Pulling latest code"; git pull --ff-only || echo "  (git pull skipped — resolve manually if needed)"; fi

# 3) rebuild + recreate app + worker (db + volumes untouched)
say "Rebuilding the image"
BUILD_SHA="$(date +%s | sha1sum | cut -c1-7)" BUILD_DATE="$(date +%F)" $DC -f "$COMPOSE" --env-file .env.prod build app
say "Restarting app + worker (data preserved, schema auto-migrates)"
$DC -f "$COMPOSE" --env-file .env.prod up -d --force-recreate app worker

# 4) wait + report version
say "Waiting for the app"
for i in $(seq 1 40); do
  if $DC -f "$COMPOSE" --env-file .env.prod exec -T app python -c "import urllib.request;urllib.request.urlopen('http://localhost:8077/api/version')" >/dev/null 2>&1; then
    V="$($DC -f "$COMPOSE" --env-file .env.prod exec -T app python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8077/api/version'))['version'])" 2>/dev/null)"
    printf '\n\033[1;32m✓ Upgraded — now running v%s\033[0m\n' "$V"
    printf '  Backup: backups/db_%s.dump   ·   Rollback: restore that dump if needed.\n\n' "$TS"; exit 0
  fi
  sleep 3
done
die "App did not come back up — check: $DC -f $COMPOSE logs app  (restore backups/db_${TS}.dump to roll back)"
