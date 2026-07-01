#!/usr/bin/env python3
"""One-time backfill: give every existing user and document a sector, so turning
on multi-tenant RBAC doesn't hide pre-RBAC data.

  - users: sector = their email domain (alice@acme.com -> sector 'acme.com'),
           via rbac.assign_user_sector (idempotent, fills NULL only).
  - docs : sector = their uploader's domain (docs.uploaded_by). When the uploader
           isn't an email (e.g. 'ingest:5', 'sharepoint-sync') the doc falls back
           to the FIRST admin's sector, so it stays visible to that tenant.

Idempotent — only touches NULL sectors. Safe to re-run. Run inside the app
container (has app deps + DB):

    docker exec docsensei-app python scripts/backfill_sectors.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_conn          # noqa: E402
from app import rbac                 # noqa: E402


def main() -> None:
    # 1) users → sector from email domain
    with get_conn() as c:
        users = c.execute(
            "SELECT id, email FROM users WHERE sector_id IS NULL AND email IS NOT NULL"
        ).fetchall()
    nu = 0
    for u in users:
        if rbac.assign_user_sector(u["id"], u["email"]):
            nu += 1

    # fallback sector = the first admin's domain (so uploader-less docs land somewhere)
    with get_conn() as c:
        admin = c.execute(
            "SELECT email FROM users WHERE role IN ('admin','superadmin') "
            "AND email IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
    fallback = None
    if admin and admin.get("email"):
        dom = rbac.sector_from_email(admin["email"])
        fallback = rbac.ensure_sector(dom) if dom else None

    # 2) docs → sector from uploader's domain, else fallback
    with get_conn() as c:
        docs = c.execute(
            "SELECT id, uploaded_by FROM docs WHERE sector_id IS NULL"
        ).fetchall()
    nd = 0
    for d in docs:
        sid = None
        ub = (d.get("uploaded_by") or "").strip()
        dom = rbac.sector_from_email(ub) if "@" in ub else None
        if dom:
            sid = rbac.ensure_sector(dom)
        if not sid:
            sid = fallback
        if sid:
            with get_conn() as c:
                c.execute("UPDATE docs SET sector_id = %s WHERE id = %s", (sid, d["id"]))
            nd += 1

    # report
    with get_conn() as c:
        ns = c.execute("SELECT count(*) AS n FROM sectors").fetchone()["n"]
    print(f"backfill done: {nu} user(s), {nd} doc(s) assigned a sector; "
          f"{ns} sector(s) now exist; fallback sector id = {fallback}")


if __name__ == "__main__":
    main()
