#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# SharePoint → DocSensei ingest inbox sync (NO Azure app registration).
#
# Uses rclone's built-in OneDrive/SharePoint client — you authenticate once with
# your own M365 account (see SETUP below), no app/client_secret/tenant needed.
# Copies new files from a SharePoint document library into the inbox folder the
# DocSensei watcher scans → they auto-ingest (PHASE-1 answerable in seconds).
#
# Run on the SERVER that hosts DocSensei (data/inbox is on that host).
#
# SETUP (one time):
#   1. Install rclone on the server:        curl https://rclone.org/install.sh | sudo bash
#   2. Headless auth (server has no browser):
#        - on the SERVER:  rclone config         # new remote, name it: sp
#            Storage = onedrive
#            client_id / client_secret = BLANK   (this is the "no app" part)
#            region   = Microsoft Cloud Global
#            "Use auto config?" = n  (NO — headless)
#            it prints a command to run on a machine WITH a browser ↓
#        - on your LAPTOP: rclone authorize "onedrive"   # logs in, prints a token
#            paste that token back into the server prompt
#            connection type = SharePoint site URL → paste your site URL
#            pick the document library → confirm
#   3. Find the library path:  rclone lsd sp:
#   4. Set SP_PATH below to the folder you want, then test:  ./sharepoint-sync.sh
#   5. Schedule it (every 10 min) — see CRON at the bottom.
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- config (edit these) ----
RCLONE_REMOTE="sp"                                   # remote name from rclone config
SP_PATH="Shared Documents"                            # library/folder to sync (e.g. "Shared Documents/SOPs")
INBOX="$(cd "$(dirname "$0")" && pwd)/data/inbox"     # DocSensei watcher inbox (host side)
INCLUDE='*.{pdf,docx,doc,xlsx,pptx,txt,md}'           # file types to pull
LOG="$(cd "$(dirname "$0")" && pwd)/data/sharepoint-sync.log"
LOCK="/tmp/docsensei-sp-sync.lock"

# ---- run (single instance via flock; copy = additive, never deletes inbox) ----
mkdir -p "$INBOX"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') skip — previous sync still running" >> "$LOG"
  exit 0
fi

echo "$(date '+%F %T') sync start  ${RCLONE_REMOTE}:${SP_PATH} → ${INBOX}" >> "$LOG"
rclone copy "${RCLONE_REMOTE}:${SP_PATH}" "$INBOX" \
  --include "$INCLUDE" \
  --ignore-existing \
  --transfers 4 \
  --log-file "$LOG" --log-level INFO
echo "$(date '+%F %T') sync done" >> "$LOG"

# ---------------------------------------------------------------------------
# CRON (every 10 minutes) — add with `crontab -e` on the server:
#   */10 * * * * /ABSOLUTE/PATH/TO/sharepoint-sync.sh >/dev/null 2>&1
#
# Or systemd timer (more robust). Ask and I'll generate the .service + .timer.
# ---------------------------------------------------------------------------
