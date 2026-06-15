"""Auth + admin endpoints. Mounted at /api/auth and /api/admin."""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import store
from .. import notify
from .security import hash_password, verify_password, make_token
from .deps import current_user, require_admin
from .ldap_auth import ldap_login, ldap_test, LdapError
from .oidc import auth_url, exchange, redirect_uri, OidcError

router = APIRouter()


def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "email", "name", "role", "auth_source", "active")}


def _issue(u: dict) -> dict:
    if not u["active"]:
        raise HTTPException(status_code=403, detail="account disabled")
    if u["role"] == "pending":
        raise HTTPException(status_code=403, detail="account awaiting admin approval")
    store.touch_login(u["id"])
    return {"token": make_token(u), "user": _public(u)}


# ---------------- config (public toggles for the login page) ----------------
@router.get("/auth/config")
def auth_config_public():
    return store.public_config()


# ---------------- local ----------------
class Credentials(BaseModel):
    email: str
    password: str
    name: str | None = None


@router.post("/auth/signup")
def signup(body: Credentials):
    cfg = store.get_config()
    if not cfg["enable_local"] or not cfg["enable_signup"]:
        raise HTTPException(status_code=403, detail="signup disabled")
    if store.get_by_email(body.email):
        raise HTTPException(status_code=409, detail="email already registered")
    u = store.create_user(
        body.email, body.name or "", "local",
        password_hash=hash_password(body.password),
    )
    if u["role"] == "pending":
        notify.emit("user", f"User awaiting approval · {body.email}", "", "alert")
        return {"pending": True, "user": _public(u)}
    notify.emit("user", f"New user · {body.email}", "", "info")
    return _issue(u)


@router.post("/auth/login")
def login(body: Credentials):
    from ..security_log import log_event, recent_fail_count
    from ..config import LOGIN_MAX_FAIL, LOGIN_LOCK_MIN
    cfg = store.get_config()
    if not cfg["enable_local"]:
        raise HTTPException(status_code=403, detail="local login disabled")
    # brute-force lockout: too many recent failures for this email → 429
    if recent_fail_count(body.email, LOGIN_LOCK_MIN) >= LOGIN_MAX_FAIL:
        log_event("login_locked", email=body.email, meta={"window_min": LOGIN_LOCK_MIN})
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts — locked for {LOGIN_LOCK_MIN} minutes")
    u = store.get_by_email(body.email)
    if not u or u["auth_source"] != "local" or not verify_password(body.password, u["password_hash"]):
        log_event("login_fail", email=body.email,
                  meta={"reason": "no user" if not u else "bad password"})
        raise HTTPException(status_code=401, detail="invalid email or password")
    log_event("login_ok", email=u["email"], meta={"source": "local"})
    return _issue(u)


# ---------------- ldap ----------------
class LdapCreds(BaseModel):
    username: str
    password: str


@router.post("/auth/ldap")
def ldap(body: LdapCreds):
    cfg = store.get_config()
    if not cfg["enable_ldap"]:
        raise HTTPException(status_code=403, detail="LDAP disabled")
    try:
        info = ldap_login(cfg, body.username, body.password)
    except LdapError as e:
        raise HTTPException(status_code=401, detail=str(e))
    # directory mail attribute is trusted → treat as a verified email
    try:
        u = store.find_or_create(info["email"], info["name"], "ldap", email_verified=True)
    except store.MergeBlocked as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _issue(u)


# ---------------- oidc / sso ----------------
@router.get("/auth/oidc/login")
def oidc_login(request: Request):
    cfg = store.get_config()
    if not cfg["enable_oidc"]:
        raise HTTPException(status_code=403, detail="SSO disabled")
    base = str(request.base_url).rstrip("/")
    try:
        return RedirectResponse(auth_url(cfg, public_url=base))
    except OidcError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/auth/oidc/callback")
def oidc_callback(code: str, state: str, request: Request):
    cfg = store.get_config()
    base = str(request.base_url).rstrip("/")
    try:
        info = exchange(cfg, code, state, public_url=base)
    except OidcError as e:
        return RedirectResponse(f"/login?error={e}")
    try:
        u = store.find_or_create(info["email"], info["name"], "oidc",
                                 oauth_sub=info["sub"], email_verified=info.get("email_verified", False))
    except store.MergeBlocked as e:
        return RedirectResponse(f"/login?error={e}")
    if not u["active"] or u["role"] == "pending":
        return RedirectResponse("/login?pending=1")
    store.touch_login(u["id"])
    # hand the token to the SPA via URL fragment (kept out of server logs)
    return RedirectResponse(f"/login#token={make_token(u)}")


# ---------------- me ----------------
@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return _public(user)


# ================= admin =================
@router.get("/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    return {"users": store.list_users()}


class AdminCreate(BaseModel):
    email: str
    name: str | None = None
    password: str
    role: str = "user"


@router.post("/admin/users")
def admin_create(body: AdminCreate, _: dict = Depends(require_admin)):
    if store.get_by_email(body.email):
        raise HTTPException(status_code=409, detail="email already exists")
    u = store.create_user(
        body.email, body.name or "", "local",
        password_hash=hash_password(body.password), role=body.role,
    )
    from ..security_log import log_event
    log_event("user_create", email=body.email, actor_email=_["email"],
              meta={"role": body.role})
    return _public(u)


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    name: str | None = None


@router.patch("/admin/users/{uid}")
def admin_patch(uid: int, body: UserPatch, admin: dict = Depends(require_admin)):
    if uid == admin["id"] and (body.role and body.role != "admin" or body.active is False):
        raise HTTPException(status_code=400, detail="cannot demote or disable yourself")
    prev = store.get_by_id(uid)
    u = store.update_user(uid, role=body.role, active=body.active, name=body.name)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    from ..security_log import log_event
    if body.role and prev and body.role != prev.get("role"):
        log_event("role_change", email=u.get("email"), actor_email=admin.get("email"),
                  meta={"from": prev.get("role"), "to": body.role})
    if body.active is False and prev and prev.get("active"):
        log_event("user_deactivate", email=u.get("email"), actor_email=admin.get("email"))
    if (
        body.role and body.role in ("user", "admin")
        and prev and prev.get("role") == "pending"
    ):
        notify.emit("user", f"User approved · {u.get('email')}", "", "success")
    return u


@router.delete("/admin/users/{uid}")
def admin_delete(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    if not store.delete_user(uid):
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "deleted": uid}


# ---- auth config (admin sees + edits everything incl. secrets) ----
@router.get("/admin/auth-config")
def admin_get_config(_: dict = Depends(require_admin)):
    return store.get_config()


@router.put("/admin/auth-config")
def admin_save_config(body: dict, _: dict = Depends(require_admin)):
    return store.save_config(body)


@router.post("/admin/auth-config/test-ldap")
def admin_test_ldap(body: dict, _: dict = Depends(require_admin)):
    return ldap_test(body)
