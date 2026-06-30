"""Unified SharePoint connector — ONE config, ONE schedule, ONE status, three methods.

A single SharePoint source can be synced into the normal ingest pipeline by any of
three methods, all landing files in `storage.save_to_inbox` -> docs 'queued' -> the
async worker ingests them (exactly like the upload / S3 / Graph lanes):

  1. push   — Desktop Sync (★ zero-admin): a tiny folder-watch script on the user's
              PC pushes new files to POST /api/connector/push with a connector token.
              Nothing to pull; the daemon tick is a no-op for this method.
  2. device — Device-code delegated login (no Azure app, no admin consent). The
              server holds a refresh token and PULLS new files from a SharePoint
              library on a schedule. Graph plumbing lives in app/sp_graph.py.
  3. app    — Azure app-only (tenant + client_id + secret). Reuses the proven
              app/sharepoint.py engine (token + walk + download + dedup).

Config + status live in integration_config[key='sp_connector'] (table already
exists — no migration). Secrets (push token, device refresh token, app secret) are
NOT returned to the UI. Everything is fail-soft: public functions return a status
dict and never raise.

Flag CONNECTOR_SP_ENABLED gates the daemon (default OFF; dev ON).
"""
import io
import json
import os
import secrets
import threading
import time

import httpx

from .db import get_conn
from . import storage

_KEY = "sp_connector"
_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
_METHODS = ("push", "device", "app")
_TIMEOUT = 60.0
_MAX_LOG = 40            # rolling status log lines kept


# ----------------------------- config store -----------------------------
def _raw() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM integration_config WHERE key=%s", (_KEY,)
            ).fetchone()
        return (row["data"] if row else {}) or {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (_KEY, json.dumps(data)),
        )


def _flag() -> bool:
    try:
        from .config import CONNECTOR_SP_ENABLED
        return bool(CONNECTOR_SP_ENABLED)
    except Exception:
        return False


def _norm_method(m: str) -> str:
    return m if m in _METHODS else "push"


# ----------------------------- hardening helpers -----------------------------
def valid_site_url(u: str) -> bool:
    """SSRF guard: a SharePoint site URL must be https and on a Microsoft host.
    Empty is allowed (not yet configured). Blocks pointing the puller at arbitrary
    internal hosts."""
    u = (u or "").strip()
    if not u:
        return True
    try:
        from urllib.parse import urlparse
        p = urlparse(u)
        if p.scheme != "https" or not p.hostname:
            return False
        host = p.hostname.lower()
        return host.endswith(".sharepoint.com") or host.endswith(".sharepoint.us") \
            or host.endswith(".sharepoint-df.com")
    except Exception:
        return False


# Push-endpoint rate limit (in-process; the push token is one trusted desktop
# agent, not a public surface — a coarse per-minute cap is enough).
_PUSH_MAX_PER_MIN = 240
_push_bucket = {"minute": 0, "n": 0}
_push_lock = threading.Lock()


def push_rate_ok() -> bool:
    with _push_lock:
        m = int(time.time() // 60)
        if _push_bucket["minute"] != m:
            _push_bucket["minute"] = m
            _push_bucket["n"] = 0
        _push_bucket["n"] += 1
        return _push_bucket["n"] <= _PUSH_MAX_PER_MIN


def config() -> dict:
    """Public, NON-SECRET config for the UI. Never returns the push token / device
    refresh token / app secret."""
    c = _raw()
    method = _norm_method(c.get("method") or "push")
    out = {
        "enabled": _flag(),
        "method": method,
        "site_url": c.get("site_url", ""),
        "folder": c.get("folder", ""),
        "recursive": bool(c.get("recursive", True)),
        "globs": c.get("globs", ".pdf,.png,.jpg,.jpeg"),
        "interval_h": int(c.get("interval_h") or 6),
        "push_token_set": bool(c.get("push_token")),
        "device": {},
        "app": {},
    }
    try:
        from . import sp_graph
        out["device"] = sp_graph.status()
    except Exception:
        out["device"] = {"connected": False, "account": "", "connected_at": ""}
    try:
        from . import sharepoint
        out["app"] = {
            "creds_ready": sharepoint.creds_ready(),
            "site_path": (sharepoint.config("sharepoint") or {}).get("site_path", ""),
        }
    except Exception:
        out["app"] = {"creds_ready": False, "site_path": ""}
    return out


def save_config(patch: dict) -> dict:
    patch = patch or {}
    c = _raw()
    if "method" in patch:
        c["method"] = _norm_method(patch["method"])
    for k in ("site_url", "folder", "globs"):
        if k in patch:
            c[k] = (patch[k] or "").strip()
    if "recursive" in patch:
        c["recursive"] = bool(patch["recursive"])
    if "interval_h" in patch:
        try:
            c["interval_h"] = min(168, max(1, int(patch["interval_h"] or 6)))
        except Exception:
            c["interval_h"] = 6
    _write(c)
    return config()


# ----------------------------- status + rolling log -----------------------------
def _status_raw() -> dict:
    return (_raw().get("status") or {})


def _set_status(**fields) -> None:
    c = _raw()
    st = c.get("status") or {}
    st.update(fields)
    c["status"] = st
    _write(c)


def _log(level: str, msg: str) -> None:
    """Append a line to the rolling status log (kept in config blob, capped)."""
    try:
        c = _raw()
        st = c.get("status") or {}
        lines = st.get("log") or []
        lines.append({"ts": _now(), "level": level, "msg": str(msg)[:300]})
        st["log"] = lines[-_MAX_LOG:]
        c["status"] = st
        _write(c)
    except Exception:
        pass


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def status() -> dict:
    c = _raw()
    method = _norm_method(c.get("method") or "push")
    st = c.get("status") or {}
    connected = False
    if method == "device":
        try:
            from . import sp_graph
            connected = sp_graph.connected()
        except Exception:
            connected = False
    elif method == "app":
        try:
            from . import sharepoint
            connected = sharepoint.enabled("sharepoint")
        except Exception:
            connected = False
    elif method == "push":
        connected = bool(c.get("push_token"))
    return {
        "method": method,
        "running": bool(_RUNNING.is_set()),
        "last_sync": st.get("last_sync"),
        "count": int(st.get("count") or 0),
        "error": st.get("error"),
        "next_run": st.get("next_run"),
        "connected": connected,
        "log": (st.get("log") or [])[-20:],
    }


# ----------------------------- push token (Desktop Sync) -----------------------------
def rotate_token() -> dict:
    """Generate a fresh connector push token (shown to the admin ONCE)."""
    tok = "spc_" + secrets.token_urlsafe(24)
    c = _raw()
    c["push_token"] = tok
    _write(c)
    _log("info", "Push token rotated")
    return {"token": tok}


def verify_push_token(tok: str) -> bool:
    if not tok:
        return False
    stored = (_raw().get("push_token") or "")
    if not stored:
        return False
    return secrets.compare_digest(tok, stored)


def accept_push(fileobj, filename: str) -> dict:
    """Desktop agent pushed a file — save to inbox + queue (dedup by name)."""
    name = (filename or "").strip() or "upload.pdf"
    ext = os.path.splitext(name)[1].lower()
    if ext not in _EXTS:
        return {"ok": False, "detail": f"unsupported type {ext}"}
    try:
        with get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM docs WHERE name=%s LIMIT 1", (name,)
            ).fetchone()
        if exists:
            return {"ok": True, "queued": 0, "skipped": 1, "detail": "duplicate name"}
        skey = storage.save_to_inbox(fileobj, name)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, updated_at) "
                "VALUES (%s, 'queued', 0, %s, %s, now())",
                (name, skey, "sp-desktop"),
            )
        _set_status(last_sync=_now())
        _bump_count(1)
        _log("info", f"Desktop push queued {name}")
        return {"ok": True, "queued": 1, "skipped": 0}
    except Exception as e:
        _log("error", f"push {name} failed: {e!r}")
        return {"ok": False, "detail": str(e)[:200]}


def _bump_count(n: int) -> None:
    c = _raw()
    st = c.get("status") or {}
    st["count"] = int(st.get("count") or 0) + int(n)
    c["status"] = st
    _write(c)


# ----------------------------- agent script generator -----------------------------
def agent_script(os_kind: str, base_url: str, token: str) -> dict:
    """Return a small folder-watch script for the Desktop Sync method. Dedups by
    name+mtime in a local state file and pushes new/changed files to /connector/push."""
    os_kind = "win" if (os_kind or "").startswith("win") else "mac"
    url = base_url.rstrip("/") + "/api/connector/push"
    if os_kind == "win":
        fn = "aria-sync.ps1"
        body = _PS1.format(url=url, token=token)
    else:
        fn = "aria-sync.sh"
        body = _SH.format(url=url, token=token)
    return {"script": body, "filename": fn}


_SH = r"""#!/usr/bin/env bash
# Aria Desktop Sync — watch a folder, push NEW/CHANGED files to your agent.
# Usage:  ./aria-sync.sh /path/to/your/SharePoint/folder
# Re-run on a schedule (cron) or leave running in a loop (set LOOP=1).
set -euo pipefail
URL="{url}"
TOKEN="{token}"
WATCH="${{1:?Pass the folder to watch as the first argument}}"
STATE="$HOME/.aria-sync-state"
touch "$STATE"
LOOP="${{LOOP:-0}}"
push_new() {{
  find "$WATCH" -type f \( -iname '*.pdf' -o -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) -print0 |
  while IFS= read -r -d '' f; do
    key="$f|$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f")"
    if ! grep -qF "$key" "$STATE"; then
      echo "push: $f"
      if curl -fsS -X POST "$URL" -H "X-Connector-Token: $TOKEN" -F "file=@$f"; then
        echo "$key" >> "$STATE"
      fi
    fi
  done
}}
if [ "$LOOP" = "1" ]; then while true; do push_new; sleep 60; done; else push_new; fi
echo "done."
"""

_PS1 = r"""# Aria Desktop Sync (Windows PowerShell) — watch a folder, push NEW/CHANGED files.
# Usage:  powershell -ExecutionPolicy Bypass -File aria-sync.ps1 "C:\path\to\folder"
param([Parameter(Mandatory=$true)][string]$Watch)
$Url   = "{url}"
$Token = "{token}"
$State = Join-Path $env:USERPROFILE ".aria-sync-state.txt"
if (!(Test-Path $State)) {{ New-Item -ItemType File -Path $State | Out-Null }}
$seen = Get-Content $State -ErrorAction SilentlyContinue
Get-ChildItem -Path $Watch -Recurse -File -Include *.pdf,*.png,*.jpg,*.jpeg | ForEach-Object {{
  $key = "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)"
  if ($seen -notcontains $key) {{
    Write-Host "push: $($_.FullName)"
    try {{
      $form = @{{ file = Get-Item $_.FullName }}
      Invoke-RestMethod -Uri $Url -Method Post -Headers @{{ "X-Connector-Token" = $Token }} -Form $form | Out-Null
      Add-Content -Path $State -Value $key
    }} catch {{ Write-Warning $_ }}
  }}
}}
Write-Host "done."
"""


# ----------------------------- pull dispatch (device / app) -----------------------------
def _exts_from_globs() -> set:
    c = _raw()
    raw = (c.get("globs") or ".pdf,.png,.jpg,.jpeg")
    out = set()
    for part in raw.split(","):
        p = part.strip().lower()
        if p and not p.startswith("."):
            p = "." + p
        if p:
            out.add(p)
    return out or set(_EXTS)


def _queue_files(files: list, uploaded_by: str) -> dict:
    """Download each NEW file (dedup by name + size) and queue for ingest. Shared by
    every pull method."""
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM docs").fetchall()
        existing = {r["name"] for r in rows}
    found = len(files)
    queued = skipped = 0
    for f in files:
        name = f.get("name") or ""
        if not name or name in existing:
            skipped += 1
            continue
        url = f.get("download_url")
        if not url:
            skipped += 1
            continue
        try:
            data = httpx.get(url, timeout=_TIMEOUT).content
            skey = storage.save_to_inbox(io.BytesIO(data), name)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, updated_at) "
                    "VALUES (%s, 'queued', 0, %s, %s, now())",
                    (name, skey, uploaded_by),
                )
            existing.add(name)
            queued += 1
        except Exception as e:
            _log("error", f"{name} download failed: {e!r}")
            skipped += 1
    return {"found": found, "queued": queued, "skipped": skipped}


def sync_now() -> dict:
    """Run one sync cycle for the configured method. Fail-soft; writes status."""
    c = _raw()
    method = _norm_method(c.get("method") or "push")
    _set_status(error=None)
    try:
        if method == "push":
            _log("info", "Desktop Sync is push-based — files arrive via the desktop agent")
            _set_status(last_sync=_now())
            return {"ok": True, **status()}
        if method == "device":
            from . import sp_graph
            if not sp_graph.connected():
                _set_status(error="device not connected")
                return {"ok": False, "detail": "device not connected", **status()}
            files = sp_graph.list_files(c.get("site_url", ""), c.get("folder", ""),
                                        _exts_from_globs())
            res = _queue_files(files, "sp-device")
        elif method == "app":
            from . import sharepoint
            if not sharepoint.enabled("sharepoint"):
                _set_status(error="azure app location/creds not ready")
                return {"ok": False, "detail": "app not configured", **status()}
            r = sharepoint.import_all(uploaded_by="sp-app", kind="sharepoint")
            res = {"found": r.get("found", 0), "queued": r.get("queued", 0),
                   "skipped": r.get("skipped", 0)}
        else:
            return {"ok": False, "detail": "unknown method"}
        _bump_count(res.get("queued", 0))
        _set_status(last_sync=_now())
        if res.get("queued"):
            _log("info", f"{method} synced {res['queued']} new file(s)")
        else:
            _log("info", f"{method} sync: no new files ({res.get('found',0)} seen)")
        return {"ok": True, **res, **status()}
    except Exception as e:
        _set_status(error=str(e)[:200])
        _log("error", f"{method} sync failed: {e!r}")
        return {"ok": False, "detail": str(e)[:200], **status()}


# ----------------------------- daemon -----------------------------
_started = False
_RUNNING = threading.Event()


def _interval_s() -> int:
    return max(1, int(_raw().get("interval_h") or 6)) * 3600


def _loop():
    time.sleep(120)                       # let boot settle
    last = 0.0
    while True:
        try:
            c = _raw()
            method = _norm_method(c.get("method") or "push")
            due = time.time() - last >= _interval_s()
            # push needs no pull; device/app pull on schedule when ready
            pullable = method in ("device", "app")
            if pullable and due:
                _RUNNING.set()
                sync_now()
                _RUNNING.clear()
                last = time.time()
                import datetime
                nxt = datetime.datetime.now(datetime.timezone.utc) + \
                    datetime.timedelta(seconds=_interval_s())
                _set_status(next_run=nxt.isoformat())
        except Exception as e:
            _RUNNING.clear()
            print(f"[sp_connector] loop skipped: {e!r}")
        time.sleep(60)                    # 1-min check tick — interval changes apply fast


def start() -> None:
    """Launch the connector daemon once (leader only). No-op unless the flag is on."""
    global _started
    if _started or not _flag():
        if not _flag():
            print("[sp_connector] CONNECTOR_SP_ENABLED off — daemon not started")
        return
    _started = True
    threading.Thread(target=_loop, name="sp-connector", daemon=True).start()
    print("[sp_connector] unified SharePoint connector daemon on (method + interval in UI)")
