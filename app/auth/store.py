"""User + auth-config persistence. Identity is keyed by email — all three
auth methods (local/ldap/oidc) resolve to one users row."""
import json

from ..db import get_conn

PUBLIC_COLS = "id, email, name, role, auth_source, auth_methods, active, created_at, last_login"

# defaults merged UNDER whatever the admin saved in auth_config.data
DEFAULT_CONFIG = {
    "enable_local": True,
    # Open self-registration is OFF by default (production/internal-tool safe):
    # users come from LDAP/SSO or are created by an admin. A super-admin can turn
    # it on in Settings → Authentication for public signup.
    "enable_signup": False,
    "enable_ldap": False,
    "enable_oidc": False,
    # master on/off per method (Settings → Authentication cards). Default ON so
    # a method appears on the login page as soon as a provider/dir is configured;
    # flip OFF to hide the whole method without deleting its config.
    "sso_enabled": True,
    "ldap_enabled": True,
    "default_role": "user",          # user | pending | admin
    "first_user_admin": True,
    # Merge an LDAP/SSO login into an existing account with the SAME email even
    # when it was created via a different method. OFF by default (OpenWebUI model)
    # — only ever merges when the provider VERIFIES the email (LDAP mail trusted;
    # OIDC requires email_verified=true). Prevents account takeover via spoofed email.
    "merge_by_email": False,
    "ldap": {
        "host": "", "port": 389, "bind_dn": "", "bind_password": "",
        "base_dn": "",
        # OpenWebUI-style (login accepts bare username OR full email/UPN):
        #   username_attr = LDAP_ATTRIBUTE_FOR_USERNAME (AD=sAMAccountName, OpenLDAP=uid)
        #   email_attr    = LDAP_ATTRIBUTE_FOR_MAIL
        #   search_filter = optional extra constraint, ANDed (e.g. (memberOf=...))
        # user_filter is legacy/optional — leave blank to use the smart default.
        "username_attr": "sAMAccountName",
        "email_attr": "mail", "name_attr": "cn",
        "search_filter": "", "user_filter": "",
        # TLS: use_ssl = LDAPS (port 636); start_tls = StartTLS on 389.
        "use_ssl": False, "start_tls": False,
    },
    "oidc": {
        "issuer": "", "client_id": "", "client_secret": "",
        "scopes": "openid email profile",
        # SSO button styling on the login page. provider picks the icon;
        # label overrides the button text (blank → derived from provider).
        "provider": "generic",          # microsoft | google | okta | generic
        "label": "",                    # e.g. "Continue with Microsoft"
    },
    # MULTI-PROVIDER (new): admins can add many SSO providers + many LDAP
    # directories. Each renders its own login button. The legacy single
    # `oidc`/`ldap` above is migrated into these lists on read (see helpers).
    "oidc_providers": [],   # [{id,name,provider,label,issuer,client_id,client_secret,scopes,enabled}]
    "ldap_directories": [], # [{id,name,host,port,bind_dn,bind_password,base_dn,username_attr,email_attr,name_attr,search_filter,user_filter,use_ssl,start_tls,enabled}]
}

_SSO_DEFLABEL = {"microsoft": "Continue with Microsoft", "google": "Continue with Google",
                 "okta": "Continue with Okta", "generic": "Continue with SSO"}


def oidc_providers() -> list:
    """All configured SSO providers. Migrates the legacy single `oidc` block into
    a one-element list when no multi-provider list exists yet (back-compat)."""
    c = get_config()
    lst = list(c.get("oidc_providers") or [])
    if not lst:
        oc = c.get("oidc") or {}
        if oc.get("issuer") and oc.get("client_id"):
            lst = [{
                "id": "default", "name": (oc.get("provider") or "SSO").title(),
                "provider": oc.get("provider") or "generic", "label": oc.get("label") or "",
                "issuer": oc["issuer"], "client_id": oc["client_id"],
                "client_secret": oc.get("client_secret", ""),
                "scopes": oc.get("scopes") or "openid email profile",
                "enabled": bool(c.get("enable_oidc")),
            }]
    return lst


def ldap_directories() -> list:
    """All configured LDAP directories. Migrates the legacy single `ldap` block."""
    c = get_config()
    lst = list(c.get("ldap_directories") or [])
    if not lst:
        lc = c.get("ldap") or {}
        if lc.get("host"):
            lst = [{**lc, "id": "default", "name": lc.get("name") or "LDAP / AD",
                    "enabled": bool(c.get("enable_ldap"))}]
    return lst


def oidc_provider(pid: str) -> dict | None:
    for p in oidc_providers():
        if str(p.get("id")) == str(pid):
            return p
    return None


def ldap_directory(did: str) -> dict | None:
    for d in ldap_directories():
        if str(d.get("id")) == str(did):
            return d
    return None


def ensure_superadmin() -> None:
    """Bootstrap the single super-admin from env (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD).
    With signup disabled this is the ONLY way to get an admin. Idempotent: creates the
    user if missing, and on every boot resets it to role=admin/active with the env
    password — so the env is the source of truth (change password = change env + restart)."""
    import os
    from .security import hash_password
    email = (os.getenv("SUPERADMIN_EMAIL") or "").strip().lower()
    pw = os.getenv("SUPERADMIN_PASSWORD") or ""
    if not email or not pw:
        return
    ph = hash_password(pw)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET role='superadmin', active=true, password_hash=%s, auth_source='local', "
                "auth_methods = ARRAY(SELECT DISTINCT m FROM unnest(coalesce(auth_methods, ARRAY[]::text[]) || ARRAY['local']) AS m) "
                "WHERE id=%s",
                (ph, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (email, name, role, password_hash, auth_source, auth_methods) "
                "VALUES (%s,%s,'superadmin',%s,'local',ARRAY['local'])",
                (email, email.split("@")[0], ph),
            )
    print(f"[auth] super-admin ensured from env: {email}")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_config() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM auth_config WHERE id = 1").fetchone()
    saved = row["data"] if row else {}
    return _merge(DEFAULT_CONFIG, saved)


def save_config(data: dict) -> dict:
    with get_conn() as conn:
        conn.execute(
            "UPDATE auth_config SET data = %s WHERE id = 1", (json.dumps(data),)
        )
    return get_config()


def public_config() -> dict:
    """What the login page is allowed to see — toggles only, no secrets."""
    c = get_config()
    # enabled + valid providers only (never expose secrets to the login page)
    provs = [p for p in oidc_providers() if p.get("enabled") and p.get("issuer") and p.get("client_id")]
    dirs = [d for d in ldap_directories() if d.get("enabled") and d.get("host")]
    sso = [{"id": p.get("id"), "provider": p.get("provider") or "generic",
            "label": (p.get("label") or "").strip() or _SSO_DEFLABEL.get(p.get("provider") or "generic", _SSO_DEFLABEL["generic"])}
           for p in provs]
    ld = [{"id": d.get("id"), "name": d.get("name") or "LDAP / AD"} for d in dirs]
    return {
        "enable_local": c["enable_local"],
        "enable_signup": c["enable_signup"],
        # a method shows only when its master switch is on AND it has a valid provider/dir
        "enable_ldap": bool(dirs) and bool(c.get("ldap_enabled", True)),
        "enable_oidc": bool(provs) and bool(c.get("sso_enabled", True)),
        "sso_providers": sso,
        "ldap_dirs": ld,
        # legacy single-provider fields (older cached clients) — first provider
        "sso_provider": sso[0]["provider"] if sso else "generic",
        "sso_label": sso[0]["label"] if sso else _SSO_DEFLABEL["generic"],
    }


def count_users() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]


def get_by_email(email: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)
        ).fetchone()


def get_by_id(uid: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()


def touch_login(uid: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login = now() WHERE id = %s", (uid,))


def record_auth_method(uid: int, method: str | None) -> None:
    """Union `method` (local|ldap|oidc) into the user's auth_methods[] — so one
    merged-by-email row shows EVERY way that email has signed in. Fail-soft."""
    if not method:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET auth_methods = "
                "  ARRAY(SELECT DISTINCT m FROM unnest("
                "    coalesce(auth_methods, ARRAY[]::text[]) || ARRAY[%s::text]) AS m) "
                "WHERE id = %s",
                (method, uid),
            )
    except Exception as e:
        print(f"[auth] record_auth_method skipped: {e!r}")


def add_to_users_group(uid: int, role: str | None = None, auth_source: str | None = None) -> None:
    """Every real member auto-joins the default 'Users' group (chat-only baseline)
    so admins see the full roster and can lift the whole floor at once. Skips
    anonymous embed visitors. Idempotent, fail-soft. Group is NOT auto-removed on
    role change — membership is additive, features stack."""
    if role == "widget" or auth_source == "embed":
        return
    try:
        with get_conn() as conn:
            g = conn.execute("SELECT id FROM groups WHERE name = 'Users'").fetchone()
            if not g:
                return
            conn.execute(
                "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (uid, g["id"]),
            )
    except Exception as e:
        print(f"[auth] add_to_users_group skipped: {e!r}")


def backfill_auth_and_groups() -> None:
    """Boot-time backfill (idempotent): seed auth_methods from auth_source for old
    rows, and add every real member to the 'Users' group. Runs after group seeding."""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET auth_methods = ARRAY[auth_source] "
                "WHERE auth_methods IS NULL AND auth_source IS NOT NULL"
            )
            conn.execute(
                "INSERT INTO user_groups (user_id, group_id) "
                "SELECT u.id, g.id FROM users u CROSS JOIN groups g "
                "WHERE g.name = 'Users' AND u.role <> 'widget' "
                "  AND coalesce(u.auth_source,'') <> 'embed' "
                "ON CONFLICT DO NOTHING"
            )
    except Exception as e:
        print(f"[auth] backfill_auth_and_groups skipped: {e!r}")


def create_user(
    email: str, name: str, source: str,
    password_hash: str | None = None, oauth_sub: str | None = None,
    role: str | None = None,
) -> dict:
    """First user ever becomes super-admin (if config says so); else config default role."""
    cfg = get_config()
    if role is None:
        if count_users() == 0 and cfg["first_user_admin"]:
            role = "superadmin"
        else:
            role = cfg["default_role"]
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO users (email, name, role, password_hash, auth_source, oauth_sub, auth_methods) "
            "VALUES (%s,%s,%s,%s,%s,%s,ARRAY[%s]) RETURNING *",
            (email, name or email.split("@")[0], role, password_hash, source, oauth_sub, source),
        ).fetchone()
    # every real member auto-joins the 'Users' group (chat-only baseline)
    add_to_users_group(row["id"], role=role, auth_source=source)
    # row-level access: assign the user's home sector from their email domain
    try:
        from .. import rbac
        if rbac.rbac_enabled():
            rbac.assign_user_sector(row["id"], email)
            with get_conn() as conn:
                row = conn.execute("SELECT * FROM users WHERE id=%s", (row["id"],)).fetchone()
    except Exception as e:
        print(f"[rbac] sector assign skipped: {e!r}")
    return row


class MergeBlocked(Exception):
    """Raised when an email already exists under a different sign-in method and
    merging isn't allowed (flag off, or the incoming email isn't verified)."""


def find_or_create(email: str, name: str, source: str, oauth_sub: str | None = None,
                   email_verified: bool = False) -> dict:
    """For LDAP/OIDC: JIT-provision by email (one identity per verified email).

    If a row with this email already exists from a DIFFERENT method, only merge
    into it when `merge_by_email` is on AND the provider verified the email —
    otherwise refuse, so a spoofed-email IdP can't hijack an existing account.
    Merging keeps the existing row untouched (role is never downgraded)."""
    u = get_by_email(email)
    if u:
        if u["auth_source"] == source:
            return u                       # same provider — always fine
        cfg = get_config()
        if cfg.get("merge_by_email") and email_verified:
            return u                       # verified cross-provider merge
        raise MergeBlocked(
            f"An account for {email} already exists via '{u['auth_source']}'. "
            "Ask an admin to enable email merge, or sign in with that method."
        )
    return create_user(email, name, source, oauth_sub=oauth_sub)


# ---- admin ops ----
def list_users() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            f"SELECT {PUBLIC_COLS} FROM users ORDER BY created_at DESC"
        ).fetchall()


def update_user(uid: int, role: str | None = None, active: bool | None = None,
                name: str | None = None) -> dict | None:
    sets, vals = [], []
    if role is not None: sets.append("role = %s"); vals.append(role)
    if active is not None: sets.append("active = %s"); vals.append(active)
    if name is not None: sets.append("name = %s"); vals.append(name)
    if not sets:
        return get_by_id(uid)
    vals.append(uid)
    with get_conn() as conn:
        return conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = %s "
            f"RETURNING {PUBLIC_COLS}", tuple(vals)
        ).fetchone()


def delete_user(uid: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM users WHERE id = %s RETURNING id", (uid,)
        ).fetchone()
    return bool(row)
