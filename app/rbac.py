"""Sector-based row-level access control (multi-tenant).

Model (see mockups/mockup-domain-rls.html):
  - SUPER-ADMIN (role 'admin' or 'superadmin') — creates sectors, assigns
    sector-admins, manages the All-Access group; sees + writes everything.
  - SECTOR-ADMIN (role 'sector_admin') — uploads / manages docs in their OWN
    sector only.
  - USER (role 'user') — reads / chats their own sector only.
  - ALL-ACCESS GROUP — members read every sector (read-only), e.g. execs / audit.

A user's sector is derived from their EMAIL DOMAIN at signup (x@cityholdings.com
→ sector 'cityholdings.com'). Everything funnels through TWO helpers:
  - allowed_sectors(user) -> None | list[int]   (None = ALL, no filter)
  - can_write(user, sector_id) -> bool

ENFORCEMENT IS ALL-OR-NOTHING: every READ path must filter retrieval/lists by
allowed_sectors(); every WRITE must gate on can_write(). One unscoped query = a
leak. Flag RBAC_ENABLED (default OFF) — when off, allowed_sectors() returns None
(ALL) so behaviour is identical to the single-tenant app.
"""
from __future__ import annotations

import os

from .db import get_conn

RBAC_ENABLED: bool = os.getenv("RBAC_ENABLED", "0") == "1"   # env default (fallback)


def rbac_enabled() -> bool:
    """Effective RBAC switch — DB runtime config (super-admin UI) OVER the env
    default, so multi-tenant access can be turned on/off live with no restart.
    Fail-soft to the env value if config can't be read."""
    try:
        from . import appcfg
        return appcfg.get_rbac_enabled()
    except Exception:
        return RBAC_ENABLED

# legacy single-tenant admin == super-admin; new explicit role also honoured
_SUPER_ROLES = {"admin", "superadmin"}


def sector_from_email(email: str | None) -> str | None:
    """'john@cityholdings.com' -> 'cityholdings.com' (lowercased)."""
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def ensure_sector(name: str, label: str | None = None) -> int | None:
    """Get-or-create a sector by name; returns its id. Super-admin-driven, but
    also used to auto-create a user's sector at signup."""
    if not name:
        return None
    with get_conn() as c:
        row = c.execute("SELECT id FROM sectors WHERE name=%s", (name,)).fetchone()
        if row:
            return row["id"]
        row = c.execute(
            "INSERT INTO sectors (name, label) VALUES (%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (name, label or name),
        ).fetchone()
        return row["id"] if row else None


def assign_user_sector(user_id: int, email: str) -> int | None:
    """Set users.sector_id from the email domain (idempotent). Called at signup."""
    name = sector_from_email(email)
    if not name:
        return None
    sid = ensure_sector(name)
    if sid:
        with get_conn() as c:
            c.execute("UPDATE users SET sector_id=%s WHERE id=%s AND sector_id IS NULL",
                      (sid, user_id))
    return sid


def is_superadmin(user: dict | None) -> bool:
    return bool(user) and user.get("role") in _SUPER_ROLES


def is_sector_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "sector_admin"


def _sector_id(user: dict | None) -> int | None:
    """Authoritative home-sector for a user. Uses the dict if it carries
    sector_id, else looks it up by id (the auth principal may not include it)."""
    if not user:
        return None
    if user.get("sector_id") is not None:
        return user["sector_id"]
    uid = user.get("id")
    if not uid:
        return None
    try:
        with get_conn() as c:
            r = c.execute("SELECT sector_id FROM users WHERE id=%s", (uid,)).fetchone()
        return r["sector_id"] if r else None
    except Exception:
        return None


def _in_all_access(user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        with get_conn() as c:
            r = c.execute(
                "SELECT 1 FROM user_groups ug JOIN groups g ON g.id=ug.group_id "
                "WHERE ug.user_id=%s AND g.all_sectors=true LIMIT 1",
                (user_id,),
            ).fetchone()
        return bool(r)
    except Exception:
        return False


def allowed_sectors(user: dict | None) -> list[int] | None:
    """Sector ids this user may READ. None => ALL (no filter applied).

    OFF (flag) or super-admin or All-Access member => None (everything).
    Otherwise => [their sector] (possibly empty list = sees nothing)."""
    if not rbac_enabled():
        return None
    if is_superadmin(user) or _in_all_access(user and user.get("id")):
        return None
    sid = _sector_id(user)
    return [sid] if sid else []


def can_write(user: dict | None, sector_id: int | None) -> bool:
    """May this user upload / edit / delete a doc in `sector_id`?"""
    if not rbac_enabled():
        return bool(user) and user.get("role") in (_SUPER_ROLES | {"sector_admin"})
    if is_superadmin(user):
        return True
    return is_sector_admin(user) and _sector_id(user) == sector_id


def can_delete_doc(user: dict | None, doc: dict | None) -> bool:
    """May this user DELETE this document? Ownership rule:
      - super-admin                    → yes (any doc)
      - the uploader (docs.uploaded_by == user email) → yes (own doc)
      - sector/folder admin of the doc's sector       → yes (can_write)
    `doc` must carry `uploaded_by` + `sector_id`. Anyone else → no."""
    if not user or not doc:
        return False
    if is_superadmin(user):
        return True
    email = (user.get("email") or "").strip().lower()
    owner = (doc.get("uploaded_by") or "").strip().lower()
    if email and owner and email == owner:
        return True
    return can_write(user, doc.get("sector_id"))


def allowed_folders(user: dict | None) -> list[int] | None:
    """Folder ids this user may READ. None => ALL (super-admin / All-Access / RBAC
    off). Otherwise resolves the per-folder access rules:
      - access_mode='org'      => anyone
      - access_mode='sector'   => same sector as the user
      - access_mode='specific' => the user is listed, or in a listed group
    """
    if not rbac_enabled():
        return None
    if is_superadmin(user) or _in_all_access(user and user.get("id")):
        return None
    uid = user and user.get("id")
    sid = _sector_id(user)
    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT DISTINCT f.id FROM folders f "
                "LEFT JOIN folder_access fa ON fa.folder_id = f.id "
                "LEFT JOIN user_groups ug ON ug.user_id = %(uid)s "
                "WHERE f.access_mode = 'org' "
                "   OR (f.access_mode = 'sector' AND f.sector_id = %(sid)s) "
                "   OR (f.access_mode = 'specific' AND ( "
                "         (fa.principal_type='user'  AND fa.principal_id = %(uid)s) "
                "      OR (fa.principal_type='group' AND fa.principal_id = ug.group_id))) ",
                {"uid": uid, "sid": sid},
            ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


def scope_filter(user: dict | None) -> tuple[list[int] | None, list[int] | None]:
    """The row-level READ filter for a user: (sectors, folders).

    (None, None) => no filter (RBAC off / super-admin / All-Access) => see all.
    Otherwise a doc is visible iff its folder is in `folders` (the user's
    sector/org/specific-grant folders) OR it has no folder but sits in one of the
    user's `sectors` (unfiled docs in their own sector). Used by every read path."""
    secs = allowed_sectors(user)
    if secs is None:                     # super-admin / All-Access / RBAC off
        return None, None
    return secs, (allowed_folders(user) or [])


def can_create_sector(user: dict | None) -> bool:
    """Only super-admin creates sectors / sector-admins / groups."""
    return is_superadmin(user)


def user_write_sector(user: dict | None) -> int | None:
    """The sector a write (upload) lands in for this user (their own sector)."""
    return user and user.get("sector_id")


# ---- per-group feature access (which tabs a user may use) ----
ALL_FEATURES = ["chat", "sources", "workspace", "eval", "wiki"]


def user_features(user: dict | None) -> list[str]:
    """Effective app features (tabs) this user may use.
      - admin / superadmin  -> everything
      - else: UNION of features across the user's groups that DEFINE features;
              if the user is in no feature-defining group -> ALL (unrestricted until
              an admin puts them in a restricting group). Fail-open on error.
    """
    if is_superadmin(user):
        return list(ALL_FEATURES)
    uid = user and user.get("id")
    if not uid:
        return list(ALL_FEATURES)
    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT g.features FROM user_groups ug JOIN groups g ON g.id = ug.group_id "
                "WHERE ug.user_id = %s AND g.features IS NOT NULL",
                (uid,),
            ).fetchall()
        if not rows:
            return list(ALL_FEATURES)
        feats = set()
        for r in rows:
            for f in (r["features"] or []):
                feats.add(f)
        return [f for f in ALL_FEATURES if f in feats]
    except Exception:
        return list(ALL_FEATURES)


def has_feature(user: dict | None, feature: str) -> bool:
    return feature in user_features(user)
