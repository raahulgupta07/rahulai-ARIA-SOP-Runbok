"""Super-admin RBAC operations (PURE functions, no routes).

This module is the data layer behind the super-admin governance UI:
  - sectors  : create / list (with doc+user counts) / delete (guarded)
  - users    : reassign role + home sector, list for the assignment UI
  - groups   : create / list (with members) / delete + membership add/remove

Everything is a plain function over `db.get_conn()` (psycopg3, dict_row,
autocommit). Reuses `rbac.ensure_sector` for sector get-or-create so the
email-domain key + label conventions stay in one place.

Callers (routes) are responsible for super-admin gating — these functions do
NO auth checks. They fail-soft where sensible (lists never raise) and raise
ValueError only for caller-correctable problems (bad role, sector still in use).
"""
from __future__ import annotations

from .db import get_conn
from . import rbac

# the only roles a user may hold; super-admin reassignment is validated against this
VALID_ROLES = {"superadmin", "admin", "sector_admin", "user"}


# ---------------------------------------------------------------- sectors ----
def create_sector(name: str, label: str | None = None) -> dict:
    """Get-or-create a sector by name (email-domain key). Returns {id,name,label}.

    Reuses rbac.ensure_sector for the insert/conflict logic, then reads back the
    canonical row so the returned label reflects what's actually stored.
    """
    name = (name or "").strip().lower()
    if not name:
        raise ValueError("sector name required")
    sid = rbac.ensure_sector(name, label)
    if not sid:
        raise ValueError("could not create sector")
    with get_conn() as c:
        row = c.execute(
            "SELECT id, name, label FROM sectors WHERE id=%s", (sid,)
        ).fetchone()
    return dict(row) if row else {"id": sid, "name": name, "label": label or name}


def list_sectors() -> list[dict]:
    """All sectors with doc_count + user_count. Newest first. Fail-soft -> []."""
    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT s.id, s.name, s.label, "
                "  (SELECT count(*) FROM docs  d WHERE d.sector_id = s.id) AS doc_count, "
                "  (SELECT count(*) FROM users u WHERE u.sector_id = s.id) AS user_count "
                "FROM sectors s ORDER BY s.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def delete_sector(sector_id: int) -> bool:
    """Delete a sector ONLY if no docs reference it (else ValueError).

    On delete, detach users (users.sector_id -> NULL) so accounts survive. Note:
    `folders.sector_id` is ON DELETE CASCADE in the schema, so empty folders in
    this sector go with it; docs are the hard guard (a doc with content must be
    moved/removed first). Returns True if a row was deleted.
    """
    with get_conn() as c:
        ndocs = c.execute(
            "SELECT count(*) AS n FROM docs WHERE sector_id=%s", (sector_id,)
        ).fetchone()
        if ndocs and ndocs["n"] > 0:
            raise ValueError(
                f"sector still has {ndocs['n']} document(s); move or delete them first"
            )
        # detach users first (FK is a plain reference, no cascade on users.sector_id)
        c.execute("UPDATE users SET sector_id=NULL WHERE sector_id=%s", (sector_id,))
        row = c.execute(
            "DELETE FROM sectors WHERE id=%s RETURNING id", (sector_id,)
        ).fetchone()
    return bool(row)


# ------------------------------------------------------------------ users ----
def set_user(user_id: int, role: str | None = None, sector_id: int | None = None) -> dict:
    """Super-admin reassign a user's role and/or home sector.

    Pass role and/or sector_id; omit (None) to leave a field unchanged. Use a
    sentinel-free contract: to CLEAR a sector pass sector_id=0 (-> NULL). Role is
    validated against VALID_ROLES. Returns the updated public user dict.
    """
    sets: list[str] = []
    params: list = []
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role {role!r}; must be one of {sorted(VALID_ROLES)}")
        sets.append("role=%s")
        params.append(role)
    if sector_id is not None:
        # 0 (or falsy non-None) clears the sector; a real id sets it
        sets.append("sector_id=%s")
        params.append(sector_id or None)

    with get_conn() as c:
        if sets:
            params.append(user_id)
            row = c.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id=%s "
                "RETURNING id, email, role, sector_id",
                tuple(params),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT id, email, role, sector_id FROM users WHERE id=%s", (user_id,)
            ).fetchone()
    if not row:
        raise ValueError(f"user {user_id} not found")
    return dict(row)


def list_users(limit: int = 500) -> list[dict]:
    """Users for the assignment UI: {id,email,name,role,sector_id,sector_name}.

    LEFT JOIN sectors so unassigned users (sector_id NULL) still appear. Newest
    first. Fail-soft -> []."""
    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT u.id, u.email, u.name, u.role, u.sector_id, "
                "       s.name AS sector_name "
                "FROM users u LEFT JOIN sectors s ON s.id = u.sector_id "
                "ORDER BY u.id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ----------------------------------------------------------------- groups ----
def create_group(name: str, all_sectors: bool = False) -> dict:
    """Create-or-update a group by unique name. Returns {id,name,all_sectors}.

    ON CONFLICT(name) updates all_sectors (lets an admin flip a group to/from
    All-Access without delete+recreate).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("group name required")
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO groups (name, all_sectors) VALUES (%s, %s) "
            "ON CONFLICT (name) DO UPDATE SET all_sectors=EXCLUDED.all_sectors "
            "RETURNING id, name, all_sectors",
            (name, bool(all_sectors)),
        ).fetchone()
    return dict(row)


def list_groups() -> list[dict]:
    """All groups with member_count + members:[{id,email}]. Fail-soft -> []."""
    try:
        with get_conn() as c:
            grows = c.execute(
                "SELECT id, name, all_sectors FROM groups ORDER BY id DESC"
            ).fetchall()
            mrows = c.execute(
                "SELECT ug.group_id, u.id, u.email "
                "FROM user_groups ug JOIN users u ON u.id = ug.user_id "
                "ORDER BY u.email"
            ).fetchall()
        by_group: dict[int, list[dict]] = {}
        for m in mrows:
            by_group.setdefault(m["group_id"], []).append({"id": m["id"], "email": m["email"]})
        out = []
        for g in grows:
            members = by_group.get(g["id"], [])
            out.append({
                "id": g["id"],
                "name": g["name"],
                "all_sectors": g["all_sectors"],
                "member_count": len(members),
                "members": members,
            })
        return out
    except Exception:
        return []


def delete_group(group_id: int) -> bool:
    """Delete a group (user_groups rows cascade via FK ON DELETE CASCADE)."""
    with get_conn() as c:
        row = c.execute(
            "DELETE FROM groups WHERE id=%s RETURNING id", (group_id,)
        ).fetchone()
    return bool(row)


def add_group_member(group_id: int, user_id: int) -> bool:
    """Add a user to a group (idempotent). Returns True if a new row was inserted."""
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s) "
            "ON CONFLICT (user_id, group_id) DO NOTHING RETURNING user_id",
            (user_id, group_id),
        ).fetchone()
    return bool(row)


def remove_group_member(group_id: int, user_id: int) -> bool:
    """Remove a user from a group. Returns True if a row was removed."""
    with get_conn() as c:
        row = c.execute(
            "DELETE FROM user_groups WHERE group_id=%s AND user_id=%s RETURNING user_id",
            (group_id, user_id),
        ).fetchone()
    return bool(row)
