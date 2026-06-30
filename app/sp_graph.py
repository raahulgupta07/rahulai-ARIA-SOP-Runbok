"""SharePoint document ingest via Microsoft Graph — DEVICE-CODE (delegated user login).

A ZERO-ADMIN alternative to the app-only lane in `sharepoint.py`. Where that module
needs an Azure app registration (tenant_id + client_id + client_secret + admin-consented
`Sites.Read.All`/`Files.Read.All` APPLICATION permissions), this one lets a headless
server sync a SharePoint library using a *user's* delegated login:

  - No app registration, no client secret, no admin consent.
  - Uses the PUBLIC Azure CLI client_id by default (a Microsoft first-party public app
    that supports the device-code grant out of the box).
  - The admin runs the device flow once (open a URL, type a code, sign in) → we get a
    refresh_token with `offline_access` and store it. From then on every sync silently
    exchanges that refresh_token for short-lived access tokens — no human in the loop.

Contrast with `sharepoint.py`:
  app-only  : client_credentials grant, app identity, sees everything the app is granted.
  THIS lane : device_code → refresh_token, acts AS the signed-in user, sees what they see.

Storage: device tokens live in the EXISTING `integration_config` table under our OWN
key `sp_device` ({refresh_token, tenant, client_id, connected_at, account}) — the
app-only lane owns `microsoft`/`sharepoint`/`onedrive` keys, so the two never collide.

Fail-soft: every public CONFIG/STATUS function returns a status dict / safe value and
never raises. `list_files` is the one exception — it RAISES on error so the caller can
record the failure (mirrors `sharepoint._list_files`).
"""
import json
import os
from urllib.parse import urlparse

import httpx

from .db import get_conn

_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
_GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 60.0

_KEY = "sp_device"
# Microsoft Graph PowerShell — well-known PUBLIC client, preauthorized for Graph
# device-code (no app registration). The Azure CLI client (04b07795-…) is now
# BLOCKED from minting Graph tokens via borrowed device-code → AADSTS65002.
_DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"  # MS Graph PowerShell public app
_DEFAULT_TENANT = "organizations"
_SCOPE = "Sites.Read.All Files.Read.All offline_access"


# ----------------------------- storage (own key) -----------------------------
def _raw(key):
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM integration_config WHERE key=%s", (key,)
            ).fetchone()
        return (row["data"] if row else {}) or {}
    except Exception:
        return {}


def _write(key, data):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO integration_config (key, data, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (key, json.dumps(data)),
        )


# ----------------------------- device-code flow -----------------------------
def device_start(client_id: str = "", tenant: str = "") -> dict:
    """Begin the device-code flow. Returns the user_code + verification URL to show
    the admin, plus the device_code that device_poll() needs. Persists tenant+client_id
    so poll can reuse them. Fail-soft."""
    cid = (client_id or "").strip() or _DEFAULT_CLIENT_ID
    ten = (tenant or "").strip() or _DEFAULT_TENANT
    try:
        url = f"https://login.microsoftonline.com/{ten}/oauth2/v2.0/devicecode"
        r = httpx.post(url, data={"client_id": cid, "scope": _SCOPE}, timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        cur = _raw(_KEY)
        cur["tenant"] = ten
        cur["client_id"] = cid
        cur["device_code"] = j.get("device_code", "")  # stash so poll works with no arg
        _write(_KEY, cur)
        return {
            "ok": True,
            "user_code": j.get("user_code", ""),
            "verification_uri": j.get("verification_uri") or j.get("verification_url", ""),
            "device_code": j.get("device_code", ""),
            "interval": int(j.get("interval") or 5),
            "expires_in": int(j.get("expires_in") or 900),
            "message": j.get("message", ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


def _account_from_id_token(id_token: str) -> str:
    """Best-effort: decode the JWT id_token payload for an email/upn. Never raises."""
    try:
        import base64

        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "ignore"))
        return (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or claims.get("name")
            or ""
        )
    except Exception:
        return ""


def device_poll(device_code: str) -> dict:
    """Poll the token endpoint with the device_code. While the user hasn't finished,
    Azure returns error `authorization_pending`/`slow_down` → we report status=pending.
    On success we store the refresh_token (+account) → status=done. Fail-soft."""
    cur = _raw(_KEY)
    cid = (cur.get("client_id") or "").strip() or _DEFAULT_CLIENT_ID
    ten = (cur.get("tenant") or "").strip() or _DEFAULT_TENANT
    device_code = (device_code or "").strip() or (cur.get("device_code") or "")
    try:
        url = f"https://login.microsoftonline.com/{ten}/oauth2/v2.0/token"
        r = httpx.post(
            url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": cid,
                "device_code": device_code,
            },
            timeout=_TIMEOUT,
        )
        j = r.json()
        if r.status_code >= 400:
            err = (j.get("error") or "").strip()
            if err in ("authorization_pending", "slow_down"):
                return {"ok": True, "status": "pending"}
            return {"ok": False, "status": "error",
                    "detail": (j.get("error_description") or err or "device flow error")[:200]}
        rt = j.get("refresh_token", "")
        if not rt:
            return {"ok": False, "status": "error", "detail": "no refresh_token returned"}
        cur["refresh_token"] = rt
        cur["tenant"] = ten
        cur["client_id"] = cid
        from datetime import datetime, timezone
        cur["connected_at"] = datetime.now(timezone.utc).isoformat()
        acct = _account_from_id_token(j.get("id_token", "")) if j.get("id_token") else ""
        if acct:
            cur["account"] = acct
        _write(_KEY, cur)
        return {"ok": True, "status": "done", "connected": True}
    except Exception as e:
        return {"ok": False, "status": "error", "detail": str(e)[:200]}


def connected() -> bool:
    return bool((_raw(_KEY).get("refresh_token") or "").strip())


def disconnect() -> dict:
    """Forget the stored device tokens (keeps the row but clears credentials). Fail-soft."""
    try:
        cur = _raw(_KEY)
        for k in ("refresh_token", "account", "connected_at"):
            cur.pop(k, None)
        _write(_KEY, cur)
    except Exception:
        pass
    return {"ok": True}


def status() -> dict:
    """Connection status for the UI. Fail-soft, never raises."""
    cur = _raw(_KEY)
    return {
        "connected": bool((cur.get("refresh_token") or "").strip()),
        "account": cur.get("account", "") or "",
        "connected_at": cur.get("connected_at", "") or "",
    }


# ----------------------------- Graph plumbing -----------------------------
def _access_token() -> str:
    """Exchange the stored refresh_token for an access_token. Rotates/stores the new
    refresh_token if Azure returns one. RAISES on failure (callers wrap)."""
    cur = _raw(_KEY)
    rt = (cur.get("refresh_token") or "").strip()
    if not rt:
        raise RuntimeError("SharePoint device login not connected")
    cid = (cur.get("client_id") or "").strip() or _DEFAULT_CLIENT_ID
    ten = (cur.get("tenant") or "").strip() or _DEFAULT_TENANT
    url = f"https://login.microsoftonline.com/{ten}/oauth2/v2.0/token"
    r = httpx.post(
        url,
        data={
            "grant_type": "refresh_token",
            "client_id": cid,
            "refresh_token": rt,
            "scope": _SCOPE,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    j = r.json()
    new_rt = j.get("refresh_token")
    if new_rt and new_rt != rt:
        cur["refresh_token"] = new_rt
        _write(_KEY, cur)
    return j["access_token"]


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _drive_for(tok: str, site_url: str) -> str:
    """Resolve a SharePoint site from a full browser URL → site id → default drive id.

    e.g. https://contoso.sharepoint.com/sites/Ops →
         GET /sites/contoso.sharepoint.com:/sites/Ops → site id → GET /sites/{id}/drive."""
    u = urlparse(site_url.strip())
    host = u.netloc
    path = u.path.strip("/")  # e.g. "sites/Ops"
    r = httpx.get(f"{_GRAPH}/sites/{host}:/{path}", headers=_headers(tok), timeout=_TIMEOUT)
    r.raise_for_status()
    site_id = r.json()["id"]
    r = httpx.get(f"{_GRAPH}/sites/{site_id}/drive", headers=_headers(tok), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["id"]


def _walk(tok: str, drive_id: str, item_url: str, out: list, exts: set) -> None:
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
                _walk(tok, drive_id, child, out, exts)
            elif it.get("file"):
                if os.path.splitext(name)[1].lower() in exts:
                    out.append({
                        "id": it["id"],
                        "name": name,
                        "size": it.get("size", 0),
                        "download_url": it.get("@microsoft.graph.downloadUrl"),
                    })
        url = body.get("@odata.nextLink")


def list_files(site_url: str, folder: str = "", exts=None) -> list[dict]:
    """List supported files in a SharePoint site's default drive (optionally under a
    folder). Resolves the site from a full browser URL. RAISES on error (caller wraps)."""
    use_exts = set(exts) if exts else _EXTS
    tok = _access_token()
    drive_id = _drive_for(tok, site_url)
    root = f"{_GRAPH}/drives/{drive_id}/root"
    if folder.strip("/"):
        root += f":/{folder.strip('/')}:"
    start = f"{root}/children"
    out: list[dict] = []
    _walk(tok, drive_id, start, out, use_exts)
    return out
