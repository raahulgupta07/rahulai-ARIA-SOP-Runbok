"""SharePoint / OneDrive document ingest via Microsoft Graph (app-only OAuth).

A third ingest lane (alongside upload + S3 import): pull every supported file
from a SharePoint document library into the normal pipeline. Mirrors s3_import —
list -> dedup by docs.name -> download -> storage.save_to_inbox -> docs row
'queued' -> the async worker ingests it.

CONFIG (super-admin): tenant_id, client_id, site_host, site_path, optional
drive_id / folder live in app_config['sharepoint']. The client SECRET is read
from the SHAREPOINT_CLIENT_SECRET env var (never stored in the DB), mirroring how
OPENROUTER_API_KEY is handled. Fail-soft: every public entry point returns a
status dict, never raises into the caller.
"""
import io
import json
import os
import threading
import time

import httpx

from .db import get_conn
from . import storage

_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
_GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 60.0
_FIELDS = ("tenant_id", "client_id", "site_host", "site_path", "drive_id", "folder")


def _raw() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM integration_config WHERE key='sharepoint'"
            ).fetchone()
        return (row["data"] if row else {}) or {}
    except Exception:
        return {}


def config() -> dict:
    """Non-secret SharePoint config from integration_config. Secret comes from env."""
    cfg = _raw()
    out = {
        "tenant_id": cfg.get("tenant_id", ""),
        "client_id": cfg.get("client_id", ""),
        "site_host": cfg.get("site_host", ""),     # e.g. contoso.sharepoint.com
        "site_path": cfg.get("site_path", ""),     # e.g. /sites/IT
        "drive_id": cfg.get("drive_id", ""),       # optional — default drive if blank
        "folder": cfg.get("folder", ""),           # optional sub-folder path filter
        "has_secret": bool(_secret()),
    }
    return out


def save_config(patch: dict) -> dict:
    cur = _raw()
    for k in _FIELDS:
        if k in (patch or {}):
            cur[k] = (patch[k] or "").strip()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES ('sharepoint', %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (json.dumps(cur),),
        )
    return config()


def _secret() -> str:
    return os.getenv("SHAREPOINT_CLIENT_SECRET", "")


def enabled() -> bool:
    c = config()
    return bool(c["tenant_id"] and c["client_id"] and c["site_host"]
               and c["site_path"] and c["has_secret"])


def _token() -> str:
    c = config()
    url = f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token"
    data = {
        "client_id": c["client_id"],
        "client_secret": _secret(),
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = httpx.post(url, data=data, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _site_id(tok: str, c: dict) -> str:
    path = c["site_path"].strip("/")
    url = f"{_GRAPH}/sites/{c['site_host']}:/{path}"
    r = httpx.get(url, headers=_headers(tok), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["id"]


def _drive_id(tok: str, site_id: str, c: dict) -> str:
    if c["drive_id"]:
        return c["drive_id"]
    r = httpx.get(f"{_GRAPH}/sites/{site_id}/drive", headers=_headers(tok), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["id"]


def _walk(tok: str, drive_id: str, item_url: str, out: list) -> None:
    """Recurse a drive folder, collecting supported files. Handles pagination."""
    url = item_url
    while url:
        r = httpx.get(url, headers=_headers(tok), timeout=_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        for it in body.get("value", []):
            name = it.get("name") or ""
            if it.get("folder"):
                child = f"{_GRAPH}/drives/{drive_id}/items/{it['id']}/children"
                _walk(tok, drive_id, child, out)
            elif it.get("file"):
                if os.path.splitext(name)[1].lower() in _EXTS:
                    out.append({
                        "id": it["id"],
                        "name": name,
                        "size": it.get("size", 0),
                        "download_url": it.get("@microsoft.graph.downloadUrl"),
                    })
        url = body.get("@odata.nextLink")


def _list_files() -> list[dict]:
    tok = _token()
    c = config()
    site_id = _site_id(tok, c)
    drive_id = _drive_id(tok, site_id, c)
    root = f"{_GRAPH}/drives/{drive_id}/root"
    if c["folder"].strip("/"):
        root += f":/{c['folder'].strip('/')}:"
    start = f"{root}/children"
    out: list[dict] = []
    _walk(tok, drive_id, start, out)
    return out


def test_connection() -> dict:
    """Verify creds + site resolve. Returns {ok, detail/site}."""
    if not enabled():
        return {"ok": False, "configured": False, "detail": "SharePoint not configured"}
    try:
        tok = _token()
        c = config()
        sid = _site_id(tok, c)
        return {"ok": True, "configured": True, "site_id": sid}
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200]}


def preview() -> dict:
    """What an import WOULD queue (no writes)."""
    if not enabled():
        return {"ok": False, "configured": False, "found": 0, "new": 0, "skipped": 0}
    try:
        files = _list_files()
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200],
                "found": 0, "new": 0, "skipped": 0}
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    new = sum(1 for f in files if f["name"] not in existing)
    return {"ok": True, "configured": True, "found": len(files),
            "new": new, "skipped": len(files) - new}


def import_all(uploaded_by: str | None = None) -> dict:
    """Download every NEW SharePoint file and queue it for ingest (dedup by name)."""
    if not enabled():
        return {"ok": False, "configured": False, "found": 0, "queued": 0, "skipped": 0}
    try:
        files = _list_files()
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200],
                "found": 0, "queued": 0, "skipped": 0}
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    found = len(files)
    queued = skipped = 0
    for f in files:
        name = f["name"]
        if name in existing:
            skipped += 1
            continue
        url = f.get("download_url")
        if not url:
            skipped += 1
            continue
        try:
            # downloadUrl is short-lived (~1h) — fetch immediately, don't queue the URL
            data = httpx.get(url, timeout=_TIMEOUT).content
            skey = storage.save_to_inbox(io.BytesIO(data), name)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, updated_at) "
                    "VALUES (%s, 'queued', 0, %s, %s, now())",
                    (name, skey, uploaded_by or "sharepoint"),
                )
            existing.add(name)
            queued += 1
        except Exception as e:
            print(f"[sharepoint] {name} failed: {e!r}")
    return {"ok": True, "configured": True, "found": found,
            "queued": queued, "skipped": skipped}


# ---- auto-sync daemon (leader-gated, flag-gated, default OFF) ----
_started = False


def _loop():
    from .config import SHAREPOINT_SYNC_INTERVAL_H
    time.sleep(120)                          # let boot settle
    while True:
        try:
            if enabled():
                res = import_all(uploaded_by="sharepoint-sync")
                if res.get("queued"):
                    print(f"[sharepoint] auto-sync queued {res['queued']} new file(s)")
        except Exception as e:
            print(f"[sharepoint] sync loop skipped: {e!r}")
        time.sleep(max(1, SHAREPOINT_SYNC_INTERVAL_H) * 3600)


def start() -> None:
    """Launch the auto-sync thread once (called from app lifespan, leader only)."""
    global _started
    from .config import SHAREPOINT_SYNC_ENABLED, SHAREPOINT_SYNC_INTERVAL_H
    if _started or not SHAREPOINT_SYNC_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, name="sharepoint-sync", daemon=True).start()
    print(f"[sharepoint] auto-sync on — every {SHAREPOINT_SYNC_INTERVAL_H}h")
