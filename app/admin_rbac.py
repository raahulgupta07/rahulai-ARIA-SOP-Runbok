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
# sentinel so partial-update callers can DISTINGUISH "leave unchanged" from an
# explicit None (which clears/normalises a field).
_UNSET = object()


def create_group(name: str, all_sectors: bool = False, description: str | None = None,
                 features: list[str] | None = None, manage_content: bool = False,
                 teach_knowledge: bool = False) -> dict:
    """Create-or-update a group by unique name. Returns the full row.

    ON CONFLICT(name) updates every supplied column (lets an admin flip a group's
    All-Access / content-permission flags / features without delete+recreate).
    features=None → NULL (no group-level feature restriction).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("group name required")
    feats = None if features is None else [f for f in features if f in FEATURES]
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO groups (name, all_sectors, description, features, "
            "                    manage_content, teach_knowledge) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET "
            "  all_sectors=EXCLUDED.all_sectors, description=EXCLUDED.description, "
            "  features=EXCLUDED.features, manage_content=EXCLUDED.manage_content, "
            "  teach_knowledge=EXCLUDED.teach_knowledge "
            "RETURNING id, name, all_sectors, description, "
            "          coalesce(features,'{}') AS features, manage_content, teach_knowledge",
            (name, bool(all_sectors), description, feats,
             bool(manage_content), bool(teach_knowledge)),
        ).fetchone()
    d = dict(row)
    d["features"] = list(d["features"] or [])
    return d


def update_group(group_id: int, *, name=_UNSET, description=_UNSET, all_sectors=_UNSET,
                 features=_UNSET, manage_content=_UNSET, teach_knowledge=_UNSET) -> dict:
    """Partial update of a group — only the supplied fields change. The `_UNSET`
    sentinel lets a caller pass an explicit None (e.g. description=None to clear it,
    features=None to drop the feature restriction) distinctly from "leave alone".
    features is validated as a subset of ALL_FEATURES. Raises ValueError if the
    group doesn't exist. Returns the full updated row."""
    sets: list[str] = []
    params: list = []
    if name is not _UNSET:
        nm = (name or "").strip()
        if not nm:
            raise ValueError("group name required")
        sets.append("name=%s"); params.append(nm)
    if description is not _UNSET:
        sets.append("description=%s"); params.append(description)
    if all_sectors is not _UNSET:
        sets.append("all_sectors=%s"); params.append(bool(all_sectors))
    if features is not _UNSET:
        feats = None if features is None else [f for f in features if f in rbac.ALL_FEATURES]
        sets.append("features=%s"); params.append(feats)
    if manage_content is not _UNSET:
        sets.append("manage_content=%s"); params.append(bool(manage_content))
    if teach_knowledge is not _UNSET:
        sets.append("teach_knowledge=%s"); params.append(bool(teach_knowledge))

    with get_conn() as c:
        if sets:
            params.append(group_id)
            row = c.execute(
                f"UPDATE groups SET {', '.join(sets)} WHERE id=%s "
                "RETURNING id, name, all_sectors, description, "
                "          coalesce(features,'{}') AS features, manage_content, teach_knowledge",
                tuple(params),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT id, name, all_sectors, description, "
                "       coalesce(features,'{}') AS features, manage_content, teach_knowledge "
                "FROM groups WHERE id=%s", (group_id,),
            ).fetchone()
    if not row:
        raise ValueError("group not found")
    d = dict(row)
    d["features"] = list(d["features"] or [])
    return d


def list_groups() -> list[dict]:
    """All groups with member_count + members:[{id,email}]. Fail-soft -> []."""
    try:
        with get_conn() as c:
            grows = c.execute(
                "SELECT id, name, all_sectors, description, "
                "       coalesce(features, '{}') AS features, "
                "       manage_content, teach_knowledge "
                "FROM groups ORDER BY id DESC"
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
                "description": g["description"],
                "features": list(g["features"] or []),
                "manage_content": bool(g["manage_content"]),
                "teach_knowledge": bool(g["teach_knowledge"]),
                "member_count": len(members),
                "members": members,
            })
        return out
    except Exception:
        return []


# which app features (tabs) a group's members may use
FEATURES = ["chat", "sources", "workspace", "eval", "wiki"]


def set_group_features(group_id: int, features: list[str]) -> dict:
    """Set the allowed feature keys for a group (validated against FEATURES)."""
    feats = [f for f in (features or []) if f in FEATURES]
    with get_conn() as c:
        row = c.execute(
            "UPDATE groups SET features = %s WHERE id = %s "
            "RETURNING id, name, all_sectors, coalesce(features,'{}') AS features",
            (feats, group_id),
        ).fetchone()
    if not row:
        raise ValueError("group not found")
    d = dict(row)
    d["features"] = list(d["features"] or [])
    return d


# two canonical access groups, seeded/normalised on boot (admin can edit/delete).
# "Administrators" = every tab + full content-permission grants; "Users" = chat-only,
# no content grants. Each is (name, features, manage_content, teach_knowledge, legacy_name)
# where legacy_name is the pre-2026-07 group this one supersedes (renamed in place so
# member rows survive the migration).
_CANONICAL_GROUPS = [
    ("Administrators", ["chat", "sources", "workspace", "eval", "wiki"], True,  True,  "Full access"),
    ("Users",          ["chat"],                                          False, False, "Chat only"),
]


def ensure_default_groups() -> None:
    """Seed/normalise the two canonical groups on boot. Migration-safe + idempotent:
      - if the new name already exists → leave it ALONE (never clobber admin edits);
      - else if the legacy name exists → RENAME it in place (preserves member rows)
        and set its features + content-permission flags to the canonical values;
      - else create it fresh.
    Defensive (fail-soft, prints on error) like the prior impl."""
    try:
        with get_conn() as c:
            existing = {r["name"] for r in c.execute("SELECT name FROM groups").fetchall()}
            for name, feats, mc, tk, legacy in _CANONICAL_GROUPS:
                fv = [f for f in feats if f in FEATURES]
                if name in existing:
                    continue                                    # respect admin's later edits
                if legacy in existing:                          # migrate the old group in place
                    c.execute(
                        "UPDATE groups SET name=%s, features=%s, manage_content=%s, "
                        "teach_knowledge=%s WHERE name=%s",
                        (name, fv, mc, tk, legacy),
                    )
                else:                                           # fresh install
                    c.execute(
                        "INSERT INTO groups (name, features, manage_content, teach_knowledge) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING",
                        (name, fv, mc, tk),
                    )
    except Exception as e:
        print(f"[rbac] ensure_default_groups skipped: {e!r}")


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
