"""SharePoint / OneDrive document ingest via Microsoft Graph (app-only OAuth).

A third ingest lane (alongside upload + S3 import): pull every supported file
from a SharePoint document library OR a user's OneDrive into the normal pipeline.
Mirrors s3_import — list -> dedup by docs.name -> download -> storage.save_to_inbox
-> docs row 'queued' -> the async worker ingests it.

TWO SOURCES, ONE LANE. Both use Microsoft Graph and the same app-only token; only
the drive-resolution step differs:
  - sharepoint: site_host + site_path -> /sites/{id}/drive
  - onedrive:   user_upn             -> /users/{upn}/drive
Everything downstream (walk, download, dedup, queue, auto-sync) is identical.

CONFIG (super-admin) lives per-kind in integration_config[key=<kind>]. The client
SECRET is read from the GRAPH_CLIENT_SECRET / SHAREPOINT_CLIENT_SECRET env var
(never stored in the DB), mirroring OPENROUTER_API_KEY. Fail-soft: every public
entry point returns a status dict, never raises into the caller.
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

KINDS = ("sharepoint", "onedrive")

# Plain (non-secret) config fields persisted per kind, returned to the UI as-is.
_FIELDS = {
    "sharepoint": ("tenant_id", "client_id", "site_host", "site_path", "drive_id", "folder"),
    "onedrive":   ("tenant_id", "client_id", "user_upn", "drive_id", "folder"),
}
# Boolean fields (per kind) — auto-sync toggle, editable in the UI.
_BOOLS = ("sync_enabled",)


def _norm(kind: str) -> str:
    return kind if kind in KINDS else "sharepoint"


def _default_interval() -> int:
    """Effective auto-sync interval (hours) when not overridden per source in the DB."""
    try:
        from .config import SHAREPOINT_SYNC_INTERVAL_H
        return max(1, int(SHAREPOINT_SYNC_INTERVAL_H or 6))
    except Exception:
        return 6


def _raw(kind: str) -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM integration_config WHERE key=%s", (kind,)
            ).fetchone()
        return (row["data"] if row else {}) or {}
    except Exception:
        return {}


def config(kind: str = "sharepoint") -> dict:
    """Non-secret config for one source. The client secret is NEVER returned raw —
    only a has_secret flag (true if set in DB or via env)."""
    kind = _norm(kind)
    cfg = _raw(kind)
    out = {"kind": kind, "has_secret": bool(_secret(kind))}
    for k in _FIELDS[kind]:
        out[k] = cfg.get(k, "")
    for b in _BOOLS:
        out[b] = bool(cfg.get(b))
    out["sync_interval_h"] = int(cfg.get("sync_interval_h") or 0) or _default_interval()
    return out


def save_config(patch: dict, kind: str = "sharepoint") -> dict:
    kind = _norm(kind)
    patch = patch or {}
    cur = _raw(kind)
    for k in _FIELDS[kind]:
        if k in patch:
            cur[k] = (patch[k] or "").strip()
    for b in _BOOLS:
        if b in patch:
            cur[b] = bool(patch[b])
    if "sync_interval_h" in patch:
        try:
            cur["sync_interval_h"] = max(1, int(patch["sync_interval_h"] or 6))
        except Exception:
            cur["sync_interval_h"] = 6
    # Secret: only overwrite when a non-blank value is sent (blank = keep existing).
    if "client_secret" in patch:
        sec = (patch["client_secret"] or "").strip()
        if sec:
            cur["client_secret"] = sec
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (kind, json.dumps(cur)),
        )
    return config(kind)


def clear_secret(kind: str = "sharepoint") -> dict:
    """Remove the DB-stored client secret for one source (env fallback still applies)."""
    kind = _norm(kind)
    cur = _raw(kind)
    cur.pop("client_secret", None)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (kind, json.dumps(cur)),
        )
    return config(kind)


def _secret(kind: str = "sharepoint") -> str:
    """DB-stored secret for this source first, then the shared env var."""
    db = (_raw(_norm(kind)).get("client_secret") or "").strip()
    if db:
        return db
    return os.getenv("GRAPH_CLIENT_SECRET", "") or os.getenv("SHAREPOINT_CLIENT_SECRET", "")


def enabled(kind: str = "sharepoint") -> bool:
    kind = _norm(kind)
    c = config(kind)
    if not (c["tenant_id"] and c["client_id"] and c["has_secret"]):
        return False
    if kind == "sharepoint":
        return bool(c["site_host"] and c["site_path"])
    return bool(c["user_upn"])


def sync_on(kind: str) -> bool:
    """Auto-sync active for this source: env master flag OR per-source UI toggle."""
    from .config import SHAREPOINT_SYNC_ENABLED
    return bool(SHAREPOINT_SYNC_ENABLED or _raw(_norm(kind)).get("sync_enabled"))


def interval_h(kind: str) -> int:
    """Per-source auto-sync interval in hours (DB override, else env/default), min 1."""
    raw = int(_raw(_norm(kind)).get("sync_interval_h") or 0) or _default_interval()
    return max(1, raw)


def _token(c: dict) -> str:
    url = f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token"
    data = {
        "client_id": c["client_id"],
        "client_secret": _secret(c.get("kind", "sharepoint")),
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = httpx.post(url, data=data, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _drive_for(kind: str, tok: str, c: dict) -> str:
    """Resolve the drive id for the configured source (kind-specific)."""
    if c.get("drive_id"):
        return c["drive_id"]
    if kind == "onedrive":
        upn = c["user_upn"].strip()
        r = httpx.get(f"{_GRAPH}/users/{upn}/drive", headers=_headers(tok), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()["id"]
    # sharepoint: site path -> site id -> default drive
    path = c["site_path"].strip("/")
    r = httpx.get(f"{_GRAPH}/sites/{c['site_host']}:/{path}",
                  headers=_headers(tok), timeout=_TIMEOUT)
    r.raise_for_status()
    site_id = r.json()["id"]
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


def _list_files(kind: str) -> list[dict]:
    c = config(kind)
    tok = _token(c)
    drive_id = _drive_for(kind, tok, c)
    root = f"{_GRAPH}/drives/{drive_id}/root"
    if c["folder"].strip("/"):
        root += f":/{c['folder'].strip('/')}:"
    start = f"{root}/children"
    out: list[dict] = []
    _walk(tok, drive_id, start, out)
    return out


def test_connection(kind: str = "sharepoint") -> dict:
    """Verify creds + source resolve. Returns {ok, detail/drive_id}."""
    kind = _norm(kind)
    if not enabled(kind):
        return {"ok": False, "configured": False, "detail": f"{kind} not configured"}
    try:
        c = config(kind)
        tok = _token(c)
        did = _drive_for(kind, tok, c)
        return {"ok": True, "configured": True, "drive_id": did}
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200]}


def preview(kind: str = "sharepoint") -> dict:
    """What an import WOULD queue (no writes)."""
    kind = _norm(kind)
    if not enabled(kind):
        return {"ok": False, "configured": False, "found": 0, "new": 0, "skipped": 0}
    try:
        files = _list_files(kind)
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200],
                "found": 0, "new": 0, "skipped": 0}
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    new = sum(1 for f in files if f["name"] not in existing)
    return {"ok": True, "configured": True, "found": len(files),
            "new": new, "skipped": len(files) - new}


def import_all(uploaded_by: str | None = None, kind: str = "sharepoint") -> dict:
    """Download every NEW file from the source and queue it for ingest (dedup by name)."""
    kind = _norm(kind)
    if not enabled(kind):
        return {"ok": False, "configured": False, "found": 0, "queued": 0, "skipped": 0}
    try:
        files = _list_files(kind)
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
                    (name, skey, uploaded_by or kind),
                )
            existing.add(name)
            queued += 1
        except Exception as e:
            print(f"[{kind}] {name} failed: {e!r}")
    return {"ok": True, "configured": True, "found": found,
            "queued": queued, "skipped": skipped}


# ---- auto-sync daemon (leader-gated, flag-gated, default OFF) ----
_started = False


def _loop():
    time.sleep(120)                          # let boot settle
    # Each source runs on its OWN interval; we tick every 60s and fire a source
    # only when its per-source interval has elapsed since its last run.
    last = {k: 0.0 for k in KINDS}
    while True:
        for kind in KINDS:
            try:
                # per-source UI toggle (or env master flag) + per-source interval,
                # both re-read every tick so UI changes take effect within a minute
                if sync_on(kind) and enabled(kind) and \
                        time.time() - last[kind] >= interval_h(kind) * 3600:
                    res = import_all(uploaded_by=f"{kind}-sync", kind=kind)
                    if res.get("queued"):
                        print(f"[{kind}] auto-sync queued {res['queued']} new file(s)")
                    last[kind] = time.time()
            except Exception as e:
                print(f"[{kind}] sync loop skipped: {e!r}")
        time.sleep(60)                       # check tick — NOT the full interval


def start() -> None:
    """Launch the auto-sync thread once (called from app lifespan, leader only).

    Runs whenever auto-sync could be turned on from the UI, so the per-source
    toggle works without an env var or restart. The loop itself is cheap (sleeps
    between cycles) and only imports sources whose toggle is on.
    """
    global _started
    from .config import SHAREPOINT_SYNC_INTERVAL_H
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="graph-sync", daemon=True).start()
    print(f"[graph] SharePoint/OneDrive auto-sync thread on — every {SHAREPOINT_SYNC_INTERVAL_H}h "
          "(per-source toggle in UI)")
