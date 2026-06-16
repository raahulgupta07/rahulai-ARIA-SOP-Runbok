"""SharePoint / OneDrive document ingest via Microsoft Graph (app-only OAuth).

A third ingest lane (alongside upload + S3 import): pull every supported file
from a SharePoint document library OR a user's OneDrive into the normal pipeline.
Mirrors s3_import — list -> dedup by docs.name -> download -> storage.save_to_inbox
-> docs row 'queued' -> the async worker ingests it.

CONFIG IS SPLIT IN TWO (so admins set it up cleanly):
  1. CREDENTIALS — one Azure app, shared by both sources. Stored once in
     integration_config[key='microsoft']: tenant_id, client_id, client_secret
     (env GRAPH_CLIENT_SECRET as fallback), plus sp_enabled / od_enabled flags.
     Set in Settings -> Integrations -> Microsoft 365.
  2. LOCATION — per source, in integration_config[key=<kind>]: where to read from
     (site_host+site_path for SharePoint, user_upn for OneDrive), optional drive_id
     and folder, plus the auto-sync toggle + interval. Set in the Add menu.

TWO SOURCES, ONE LANE. Both use the same app-only token; only drive-resolution
differs: sharepoint -> /sites/{id}/drive, onedrive -> /users/{upn}/drive.
Everything downstream (walk, download, dedup, queue, auto-sync) is identical.

Fail-soft: every public entry point returns a status dict, never raises.
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
_CREDS_KEY = "microsoft"

# Per-kind LOCATION fields (no credentials here).
_FIELDS = {
    "sharepoint": ("site_host", "site_path", "drive_id", "folder"),
    "onedrive":   ("user_upn", "drive_id", "folder"),
}
_BOOLS = ("sync_enabled",)


def _norm(kind: str) -> str:
    return kind if kind in KINDS else "sharepoint"


def _default_interval() -> int:
    try:
        from .config import SHAREPOINT_SYNC_INTERVAL_H
        return max(1, int(SHAREPOINT_SYNC_INTERVAL_H or 6))
    except Exception:
        return 6


def _raw(key: str) -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM integration_config WHERE key=%s", (key,)
            ).fetchone()
        return (row["data"] if row else {}) or {}
    except Exception:
        return {}


def _write(key: str, data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (key, json.dumps(data)),
        )


# ----------------------------- credentials (shared) -----------------------------
def _secret() -> str:
    """DB-stored client secret first, then the shared env var."""
    db = (_raw(_CREDS_KEY).get("client_secret") or "").strip()
    if db:
        return db
    return os.getenv("GRAPH_CLIENT_SECRET", "") or os.getenv("SHAREPOINT_CLIENT_SECRET", "")


def _creds() -> dict:
    """Internal: full credentials incl. secret (never returned to the UI)."""
    c = _raw(_CREDS_KEY)
    return {
        "tenant_id": (c.get("tenant_id") or "").strip(),
        "client_id": (c.get("client_id") or "").strip(),
        "secret": _secret(),
    }


def creds_config() -> dict:
    """Non-secret credential config for Settings. Secret exposed only as has_secret."""
    c = _raw(_CREDS_KEY)
    return {
        "tenant_id": (c.get("tenant_id") or "").strip(),
        "client_id": (c.get("client_id") or "").strip(),
        "has_secret": bool(_secret()),
        "sp_enabled": bool(c.get("sp_enabled", True)),
        "od_enabled": bool(c.get("od_enabled", True)),
    }


def save_creds(patch: dict) -> dict:
    patch = patch or {}
    cur = _raw(_CREDS_KEY)
    for k in ("tenant_id", "client_id"):
        if k in patch:
            cur[k] = (patch[k] or "").strip()
    for b in ("sp_enabled", "od_enabled"):
        if b in patch:
            cur[b] = bool(patch[b])
    if "client_secret" in patch:
        sec = (patch["client_secret"] or "").strip()
        if sec:
            cur["client_secret"] = sec
    _write(_CREDS_KEY, cur)
    return creds_config()


def clear_creds_secret() -> dict:
    cur = _raw(_CREDS_KEY)
    cur.pop("client_secret", None)
    _write(_CREDS_KEY, cur)
    return creds_config()


def creds_ready() -> bool:
    c = _creds()
    return bool(c["tenant_id"] and c["client_id"] and c["secret"])


def kind_enabled(kind: str) -> bool:
    """Source allowed by the admin's enable flags in Settings."""
    c = _raw(_CREDS_KEY)
    return bool(c.get("sp_enabled", True)) if _norm(kind) == "sharepoint" \
        else bool(c.get("od_enabled", True))


def test_creds() -> dict:
    """Verify the Azure app registration by fetching a Graph token. No location."""
    if not creds_ready():
        return {"ok": False, "configured": False, "detail": "Microsoft 365 not configured"}
    try:
        _token()
        return {"ok": True, "configured": True, "detail": "Credentials valid"}
    except Exception as e:
        return {"ok": False, "configured": True, "detail": str(e)[:200]}


# ----------------------------- location (per source) -----------------------------
def config(kind: str = "sharepoint") -> dict:
    """Per-source LOCATION config (no credentials). Includes a creds_ready hint so
    the Add UI can gate itself, and the effective sync interval."""
    kind = _norm(kind)
    cfg = _raw(kind)
    out = {"kind": kind, "creds_ready": creds_ready(), "kind_enabled": kind_enabled(kind)}
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
    _write(kind, cur)
    return config(kind)


def enabled(kind: str = "sharepoint") -> bool:
    """Source is fully ready to import: creds + enabled flag + location present."""
    kind = _norm(kind)
    if not (creds_ready() and kind_enabled(kind)):
        return False
    c = config(kind)
    if kind == "sharepoint":
        return bool(c["site_host"] and c["site_path"])
    return bool(c["user_upn"])


def sync_on(kind: str) -> bool:
    from .config import SHAREPOINT_SYNC_ENABLED
    return bool(SHAREPOINT_SYNC_ENABLED or _raw(_norm(kind)).get("sync_enabled"))


def interval_h(kind: str) -> int:
    raw = int(_raw(_norm(kind)).get("sync_interval_h") or 0) or _default_interval()
    return max(1, raw)


# ----------------------------- Graph plumbing -----------------------------
def _token() -> str:
    c = _creds()
    url = f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token"
    data = {
        "client_id": c["client_id"],
        "client_secret": c["secret"],
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
    tok = _token()
    drive_id = _drive_for(kind, tok, c)
    root = f"{_GRAPH}/drives/{drive_id}/root"
    if c["folder"].strip("/"):
        root += f":/{c['folder'].strip('/')}:"
    start = f"{root}/children"
    out: list[dict] = []
    _walk(tok, drive_id, start, out)
    return out


def test_connection(kind: str = "sharepoint") -> dict:
    """Verify the configured LOCATION resolves to a drive. Returns {ok, detail/drive_id}."""
    kind = _norm(kind)
    if not creds_ready():
        return {"ok": False, "configured": False, "detail": "Set up Microsoft 365 in Settings first"}
    if not enabled(kind):
        return {"ok": False, "configured": False, "detail": f"{kind} location not set"}
    try:
        tok = _token()
        did = _drive_for(kind, tok, config(kind))
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


# ---- auto-sync daemon (leader-gated, per-source toggle + interval) ----
_started = False


def _loop():
    time.sleep(120)                          # let boot settle
    last = {k: 0.0 for k in KINDS}
    while True:
        for kind in KINDS:
            try:
                if sync_on(kind) and enabled(kind) and \
                        time.time() - last[kind] >= interval_h(kind) * 3600:
                    res = import_all(uploaded_by=f"{kind}-sync", kind=kind)
                    if res.get("queued"):
                        print(f"[{kind}] auto-sync queued {res['queued']} new file(s)")
                    last[kind] = time.time()
            except Exception as e:
                print(f"[{kind}] sync loop skipped: {e!r}")
        time.sleep(60)                       # check tick — toggle/interval apply within a minute


def start() -> None:
    """Launch the auto-sync thread once (leader only). Cheap — sleeps between cycles;
    only imports sources whose per-source toggle is on."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="graph-sync", daemon=True).start()
    print("[graph] SharePoint/OneDrive auto-sync thread on (per-source toggle + interval in UI)")
