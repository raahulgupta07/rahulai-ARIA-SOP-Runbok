"""FastAPI dependencies — verify our JWT, load the user, enforce role/status."""
from fastapi import Header, HTTPException

from .security import decode_token
from . import store


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = store.get_by_id(int(payload["sub"]))
    if not user or not user["active"]:
        raise HTTPException(status_code=401, detail="account inactive")
    if user["role"] == "pending":
        raise HTTPException(status_code=403, detail="account awaiting admin approval")
    return user


def current_principal(authorization: str | None = Header(default=None)) -> dict:
    """Accept EITHER a member login token OR an embed-widget token, so embedded
    sites and logged-in members share one brain. Widget tokens are rate-limited
    per visitor here."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")
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
        raise HTTPException(status_code=403, detail="account awaiting admin approval")
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
