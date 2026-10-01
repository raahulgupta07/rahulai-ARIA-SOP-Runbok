"""FastAPI dependencies — verify our JWT, load the user, enforce role/status."""
import threading
import time

from fastapi import Header, HTTPException

from .security import decode_token
from . import store

_NO_ACCOUNT = "no DocSensei account for this identity"
_PENDING = "account awaiting admin approval"

# Bearer calls arrive on EVERY chat request — throttle the bookkeeping writes
# (last_login / auth_methods / audit row) to once per user per window.
_NOTE_EVERY = 600
_noted: dict[str, float] = {}
_noted_lock = threading.Lock()


def _due(key: str) -> bool:
    now = time.monotonic()
    with _noted_lock:
        if now - _noted.get(key, -1e9) < _NOTE_EVERY:
            return False
        if len(_noted) > 20000:
            _noted.clear()
        _noted[key] = now
        return True


def _bearer_user(token: str, app_key: str | None = None) -> dict:
    """Fallback when the token is not one of ours: an IdP access token (e.g.
    Keycloak via an OpenWebUI pipe). Maps to the EXISTING user by email — never
    creates one. 401 = token not accepted, 403 = accepted but no usable account."""
    from .oidc import verify_bearer
    from ..security_log import log_event
    from . import app_keys

    app = None
    if app_key:
        # an app key identifies the calling app INSTEAD of the client-id
        # allow-list; a bad/revoked key is refused outright (fail closed)
        app = app_keys.verify(app_key)
        if not app:
            raise HTTPException(status_code=401, detail="invalid or revoked app key")
    claims = verify_bearer(token, skip_client_check=bool(app))
    if not claims:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="token has no email claim")
    # email_verified is NOT required — same rule as Aria's own SSO login
    # (store.find_or_create): the corporate IdP's email is authoritative, and
    # Keycloak marks Office 365-brokered emails unverified unless "Trust Email"
    # is on. The token is still signature/issuer/client checked and must map to
    # an EXISTING active account.
    meta = {"issuer": claims.get("_issuer"), "client_id": claims.get("_client_id")}
    if app:
        meta["app_key"] = app["name"]
        app_keys.touch(app["id"])
    user = store.get_by_email(email)
    if not user or not user["active"] or user["role"] == "widget":
        if _due("deny:" + email):
            log_event("bearer_no_account", email, meta=meta)
        raise HTTPException(status_code=403, detail=_NO_ACCOUNT)
    if user["role"] == "pending":
        raise HTTPException(status_code=403, detail=_PENDING)
    if _due(f"ok:{user['id']}"):
        store.record_auth_method(user["id"], "oidc-bearer")
        try:
            store.touch_login(user["id"])
        except Exception as e:
            print(f"[auth] touch_login skipped: {e!r}")
        log_event("bearer_ok", email, meta=meta)
    return user


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization


def current_user(authorization: str | None = Header(default=None),
                 x_aria_app_key: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    payload = decode_token(token)
    if not payload:
        return _bearer_user(token, x_aria_app_key if isinstance(x_aria_app_key, str) else None)
    user = store.get_by_id(int(payload["sub"]))
    if not user or not user["active"]:
        raise HTTPException(status_code=401, detail="account inactive")
    if user["role"] == "pending":
        raise HTTPException(status_code=403, detail=_PENDING)
    return user


def current_principal(authorization: str | None = Header(default=None),
                      x_aria_app_key: str | None = Header(default=None)) -> dict:
    """Accept EITHER a member login token OR an embed-widget token, so embedded
    sites and logged-in members share one brain. Widget tokens are rate-limited
    per visitor here."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    payload = decode_token(token)
    if not payload:
        return _bearer_user(token, x_aria_app_key if isinstance(x_aria_app_key, str) else None)
    user = store.get_by_id(int(payload["sub"]))
    if not user or not user["active"]:
        raise HTTPException(status_code=401, detail="account inactive")
    if payload.get("typ") == "widget":
        from ..embed import get_key, check_rate, check_caps
        key = get_key(payload.get("kid"))
        if not key or not key["active"]:
            raise HTTPException(status_code=401, detail="embed key disabled")
        check_rate(key["id"], payload.get("vid", ""), key["rate_per_min"])
        check_caps(key)                 # per-key daily message + dollar ceilings
        u = dict(user)
        u["_widget"] = True
        u["_embed_key_id"] = key["id"]  # routes use this to meter widget spend
        return u
    if user["role"] == "pending":
        raise HTTPException(status_code=403, detail=_PENDING)
    return user


def require_admin(authorization: str | None = Header(default=None)) -> dict:
    user = current_user(authorization)
    # admin console: both 'admin' (all-sector admin) and 'superadmin' (top) qualify
    if user["role"] not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="admin only")
    return user


def require_content_manager(authorization: str | None = Header(default=None)) -> dict:
    """May manage content (upload / folders / move / retag / etc.). Admin-tier OR a
    plain user in a group that grants manage_content. Settings stays super-admin."""
    from .. import rbac
    user = current_user(authorization)
    if not rbac.can_manage_content(user):
        raise HTTPException(status_code=403, detail="content management not allowed")
    return user


def require_knowledge_manager(authorization: str | None = Header(default=None)) -> dict:
    """May teach knowledge (facts / Q&A approve-edit-delete). Admin-tier OR a plain
    user in a group that grants teach_knowledge."""
    from .. import rbac
    user = current_user(authorization)
    if not rbac.can_teach(user):
        raise HTTPException(status_code=403, detail="teaching not allowed")
    return user


def require_superadmin(authorization: str | None = Header(default=None)) -> dict:
    """Top-tier only. Gates the whole Settings surface (auth config incl. LDAP/OIDC
    secrets, storage creds, RBAC toggle, sectors/groups, user role assignment,
    governance/persona/features). Plain 'admin' can use every app page but NOT
    Settings, so these config mutations must be superadmin-only."""
    user = current_user(authorization)
    if user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="super-admin only")
    return user
