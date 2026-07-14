"""Auth + admin endpoints. Mounted at /api/auth and /api/admin."""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import store
from .. import notify
from .security import hash_password, verify_password, make_token
from .deps import current_user, require_admin, require_superadmin
from .ldap_auth import ldap_login, ldap_test, LdapError
from .oidc import auth_url, exchange, redirect_uri, OidcError, consume_state

router = APIRouter()


def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "email", "name", "role", "auth_source", "active")}


def _issue(u: dict, method: str | None = None) -> dict:
    if not u["active"]:
        raise HTTPException(status_code=403, detail="account disabled")
    if u["role"] == "pending":
        raise HTTPException(status_code=403, detail="account awaiting admin approval")
    store.touch_login(u["id"])
    # record EVERY sign-in method this email used (merge-by-email shows all) +
    # keep the user in the default 'Users' group. Both idempotent + fail-soft.
    store.record_auth_method(u["id"], method or u.get("auth_source"))
    store.add_to_users_group(u["id"], role=u.get("role"), auth_source=u.get("auth_source"))
    # multi-tenant: on EVERY login ensure the user's sector = their email domain
    # (idempotent — assign_user_sector only fills a NULL sector). Backfills users
    # created before RBAC was on. Gated on rbac_enabled; fail-soft.
    try:
        from .. import rbac
        if rbac.rbac_enabled():
            rbac.assign_user_sector(u["id"], u["email"])
    except Exception as e:
        print(f"[rbac] login sector assign skipped: {e!r}")
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
    from_admin: bool = False    # /login/admin → super-admin escape hatch


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
    return _issue(u, "local")


@router.post("/auth/login")
def login(body: Credentials):
    from ..security_log import log_event, recent_fail_count
    from ..config import LOGIN_MAX_FAIL, LOGIN_LOCK_MIN
    cfg = store.get_config()
    # local login can be turned off org-wide, BUT the /login/admin route
    # (from_admin=true) is an escape hatch: admins can always sign in with a
    # local password so a broken SSO/LDAP can never lock everyone out. A
    # non-admin who hits /login/admin is still rejected after the password check.
    if not cfg["enable_local"] and not body.from_admin:
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
    # escape hatch is admins only — a normal local user can't bypass the toggle.
    # BOTH admin and superadmin qualify (a superadmin who turns local login off
    # must still be able to get back in via /login/admin — else full lockout).
    if body.from_admin and not cfg["enable_local"] and u["role"] not in ("admin", "superadmin"):
        log_event("login_fail", email=body.email, meta={"reason": "admin-route non-admin"})
        raise HTTPException(status_code=403, detail="admin sign-in only")
    log_event("login_ok", email=u["email"], meta={"source": "local", "admin_route": body.from_admin})
    return _issue(u, "local")


# ---------------- ldap ----------------
class LdapCreds(BaseModel):
    username: str
    password: str
    directory: str | None = None    # which LDAP directory (multi-provider); None = first enabled


@router.post("/auth/ldap")
def ldap(body: LdapCreds):
    dirs = [d for d in store.ldap_directories() if d.get("enabled") and d.get("host")]
    if not dirs:
        raise HTTPException(status_code=403, detail="LDAP disabled")
    directory = store.ldap_directory(body.directory) if body.directory else None
    if directory is None or not directory.get("enabled"):
        directory = dirs[0]
    try:
        info = ldap_login(directory, body.username, body.password)
    except LdapError as e:
        raise HTTPException(status_code=401, detail=str(e))
    # directory mail attribute is trusted → treat as a verified email
    try:
        u = store.find_or_create(info["email"], info["name"], "ldap", email_verified=True)
    except store.MergeBlocked as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _issue(u, "ldap")


# ---------------- oidc / sso ----------------
@router.get("/auth/oidc/login")
def oidc_login(request: Request, pid: str | None = None):
    provs = [p for p in store.oidc_providers() if p.get("enabled") and p.get("issuer") and p.get("client_id")]
    if not provs:
        raise HTTPException(status_code=403, detail="SSO disabled")
    provider = store.oidc_provider(pid) if pid else None
    if provider is None or not provider.get("enabled"):
        provider = provs[0]
    base = str(request.base_url).rstrip("/")
    try:
        return RedirectResponse(auth_url(provider, public_url=base))
    except OidcError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/auth/oidc/callback")
def oidc_callback(code: str, state: str, request: Request):
    # Whole body is wrapped: an SSO callback must NEVER surface a raw 500 to the
    # browser — any unexpected error is logged with a traceback and the user is
    # sent back to /login with a short message.
    import urllib.parse as _url
    try:
        base = str(request.base_url).rstrip("/")
        ok, pid = consume_state(state)
        if not ok:
            return RedirectResponse("/login?error=invalid+state")
        provider = store.oidc_provider(pid) if pid else None
        if provider is None:
            provs = [p for p in store.oidc_providers() if p.get("issuer") and p.get("client_id")]
            provider = provs[0] if provs else None
        if provider is None:
            return RedirectResponse("/login?error=unknown+provider")
        try:
            info = exchange(provider, code, state, public_url=base)
        except OidcError as e:
            return RedirectResponse("/login?error=" + _url.quote(str(e)))
        try:
            u = store.find_or_create(info["email"], info["name"], "oidc",
                                     oauth_sub=info["sub"], email_verified=info.get("email_verified", False))
        except store.MergeBlocked as e:
            return RedirectResponse("/login?error=" + _url.quote(str(e)))
        if not u["active"] or u["role"] == "pending":
            return RedirectResponse("/login?pending=1")
        store.touch_login(u["id"])
        store.record_auth_method(u["id"], "oidc")
        store.add_to_users_group(u["id"], role=u.get("role"), auth_source=u.get("auth_source"))
        # hand the token to the SPA via URL fragment (kept out of server logs)
        return RedirectResponse(f"/login#token={make_token(u)}")
    except Exception as e:
        import traceback
        print("[oidc] callback failed:\n" + traceback.format_exc())
        return RedirectResponse("/login?error=" + _url.quote(f"sso login failed: {e}"))


# ---------------- me ----------------
@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    from .. import rbac
    return {
        **_public(user),
        "features": rbac.user_features(user),
        "capabilities": {
            "manage_content": rbac.can_manage_content(user),
            "teach_knowledge": rbac.can_teach(user),
        },
    }


# ================= admin =================
@router.get("/admin/users")
def admin_list_users(_: dict = Depends(require_superadmin)):
    return {"users": store.list_users()}


class AdminCreate(BaseModel):
    email: str
    name: str | None = None
    password: str
    role: str = "user"


@router.post("/admin/users")
def admin_create(body: AdminCreate, _: dict = Depends(require_superadmin)):
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
def admin_patch(uid: int, body: UserPatch, admin: dict = Depends(require_superadmin)):
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
def admin_delete(uid: int, admin: dict = Depends(require_superadmin)):
    if uid == admin["id"]:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    if not store.delete_user(uid):
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "deleted": uid}


# ---- auth config (admin sees + edits everything incl. secrets) ----
@router.get("/admin/auth-config")
def admin_get_config(_: dict = Depends(require_superadmin)):
    return store.get_config()


@router.put("/admin/auth-config")
def admin_save_config(body: dict, _: dict = Depends(require_superadmin)):
    return store.save_config(body)


@router.post("/admin/auth-config/test-ldap")
def admin_test_ldap(body: dict, _: dict = Depends(require_superadmin)):
    return ldap_test(body)
