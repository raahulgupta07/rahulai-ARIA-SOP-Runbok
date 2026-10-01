"""Rebuild section trees for already-ingested docs with PageIndex Flash (no LLM).

Why: until 2.29.0 `vendor_pageindex/` was a git pointer, so installs from GitHub
had NO PageIndex and every doc fell back to a flat "one node per page" tree. This
rebuilds the real section tree from each doc's stored source PDF — layout and
bookmarks only, ~0.1-0.7 s per SOP, $0. Pages, answers, Q&A, facts are untouched;
only `nodes` + `docs.tree_json` are replaced, in one transaction per doc.

Run inside the app container:
    python scripts/rebuild_trees.py --dry-run          # report only
    python scripts/rebuild_trees.py                    # flat-tree docs only (default)
    python scripts/rebuild_trees.py --all --limit 20   # every ready doc, first 20
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_conn                      # noqa: E402
from app.brain_tools import _walk_tree           # noqa: E402
from app.ingest import _fill_summaries           # noqa: E402
from app.tree_flash import build_tree_flash      # noqa: E402
from app.worker import _resolve_src              # noqa: E402


def _is_flat(conn, doc_id: int) -> bool:
    r = conn.execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE title ~ ' - page [0-9]+$') AS flat "
        "FROM nodes WHERE doc_id = %s", (doc_id,)).fetchone()
    return (r["n"] or 0) == 0 or r["flat"] == r["n"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="rebuild every ready doc, not just flat-tree ones")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    with get_conn() as conn:
        docs = conn.execute(
            "SELECT id, name, storage_key, status FROM docs "
            "WHERE status IN ('ready','ready_lite') ORDER BY id").fetchall()
    stats = {"seen": 0, "rebuilt": 0, "kept_flat": 0, "no_source": 0, "skipped_has_tree": 0}
    t0 = time.time()
    for d in docs:
        if a.limit and stats["seen"] >= a.limit:
            break
        with get_conn() as conn:
            if not a.all and not _is_flat(conn, d["id"]):
                stats["skipped_has_tree"] += 1
                continue
            pages = conn.execute(
                "SELECT page_no, coalesce(text,'') AS text FROM pages WHERE doc_id=%s ORDER BY page_no",
                (d["id"],)).fetchall()
        stats["seen"] += 1
        src = None
        try:
            src = _resolve_src(d)
        except Exception:
            pass
        if not src or not os.path.exists(str(src)):
            stats["no_source"] += 1
            continue
        tree = build_tree_flash(str(src))
        if not tree:
            stats["kept_flat"] += 1          # scanned/image-only → Flash can't; keep current tree
            continue
        tree = _fill_summaries(tree, pages)
        flat: list = []
        _walk_tree(tree, d["id"], d["name"], flat)
        flat = [n for n in flat if n.get("page_no")]
        if not flat:
            stats["kept_flat"] += 1
            continue
        if a.dry_run:
            stats["rebuilt"] += 1
            print(f"[dry] doc {d['id']}: {len(flat)} sections ({tree.get('toc_source')})")
            continue
        with get_conn() as conn, conn.transaction():
            conn.execute("DELETE FROM nodes WHERE doc_id = %s", (d["id"],))
            for nd in flat:
                conn.execute("INSERT INTO nodes (doc_id, page_no, title, summary) VALUES (%s,%s,%s,%s)",
                             (d["id"], nd["page_no"], nd["title"], nd["summary"]))
            conn.execute("UPDATE docs SET tree_json = %s WHERE id = %s",
                         (json.dumps(tree, ensure_ascii=False), d["id"]))
        stats["rebuilt"] += 1
    stats["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
