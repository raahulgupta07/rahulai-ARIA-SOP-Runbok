"""User + auth-config persistence. Identity is keyed by email — all three
auth methods (local/ldap/oidc) resolve to one users row."""
import json

from ..db import get_conn

PUBLIC_COLS = "id, email, name, role, auth_source, active, created_at, last_login"

# defaults merged UNDER whatever the admin saved in auth_config.data
DEFAULT_CONFIG = {
    "enable_local": True,
    "enable_signup": True,
    "enable_ldap": False,
    "enable_oidc": False,
    "default_role": "user",          # user | pending | admin
    "first_user_admin": True,
    # Merge an LDAP/SSO login into an existing account with the SAME email even
    # when it was created via a different method. OFF by default (OpenWebUI model)
    # — only ever merges when the provider VERIFIES the email (LDAP mail trusted;
    # OIDC requires email_verified=true). Prevents account takeover via spoofed email.
    "merge_by_email": False,
    "ldap": {
        "host": "", "port": 389, "bind_dn": "", "bind_password": "",
        "base_dn": "", "user_filter": "(sAMAccountName={username})",
        "email_attr": "mail", "name_attr": "cn",
    },
    "oidc": {
        "issuer": "", "client_id": "", "client_secret": "",
        "scopes": "openid email profile",
    },
}


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
                "UPDATE users SET role='admin', active=true, password_hash=%s, auth_source='local' WHERE id=%s",
                (ph, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (email, name, role, password_hash, auth_source) "
                "VALUES (%s,%s,'admin',%s,'local')",
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
    return {
        "enable_local": c["enable_local"],
        "enable_signup": c["enable_signup"],
        "enable_ldap": c["enable_ldap"],
        "enable_oidc": c["enable_oidc"] and bool(c["oidc"]["issuer"] and c["oidc"]["client_id"]),
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


def create_user(
    email: str, name: str, source: str,
    password_hash: str | None = None, oauth_sub: str | None = None,
    role: str | None = None,
) -> dict:
    """First user ever becomes admin (if config says so); else config default role."""
    cfg = get_config()
    if role is None:
        if count_users() == 0 and cfg["first_user_admin"]:
            role = "admin"
        else:
            role = cfg["default_role"]
    with get_conn() as conn:
        return conn.execute(
            "INSERT INTO users (email, name, role, password_hash, auth_source, oauth_sub) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (email, name or email.split("@")[0], role, password_hash, source, oauth_sub),
        ).fetchone()


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
