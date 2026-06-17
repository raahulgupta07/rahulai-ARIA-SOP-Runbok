"""Backfill Contextual Retrieval blurbs for already-ingested pages.

For every page whose `context` is empty (or NULL), load its document title +
outline + page text, call context_enrich.enrich_page, and write the result to
pages.context. The GENERATED pages.tsv column then automatically re-folds the new
context into the FTS source (no manual tsv update needed).

Resumable: only touches empty contexts, so re-running picks up where it left off.
Bounded + prints progress. Fail-soft per page (a failed enrich leaves the page
empty so a later run can retry it).

Run inside the container:
    docker exec docsensei-app python scripts/enrich_existing.py
or locally:
    PYTHONPATH=. python scripts/enrich_existing.py
"""
import os
import sys
from pathlib import Path

# allow running as `python scripts/enrich_existing.py` (no PYTHONPATH set)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import get_conn  # noqa: E402
from app.context_enrich import enrich_page, CONTEXT_ENRICH_ENABLED  # noqa: E402

# safety cap so an accidental run can't churn forever; override with env.
_MAX = int(os.getenv("ENRICH_BACKFILL_MAX", "100000"))


def _outline_for(doc_id: int) -> str:
    """Compact outline = node titles for the doc (cheap, bounded)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title FROM nodes WHERE doc_id = %s AND title <> '' "
            "ORDER BY page_no NULLS LAST, id",
            (doc_id,),
        ).fetchall()
    titles = [r["title"] for r in rows if r.get("title")]
    return "\n".join(titles[:120])[:1500]


def main() -> int:
    if not CONTEXT_ENRICH_ENABLED:
        print("CONTEXT_ENRICH_ENABLED is off — nothing to do.")
        return 0

    with get_conn() as conn:
        pending = conn.execute(
            "SELECT count(*) AS n FROM pages "
            "WHERE context IS NULL OR context = ''"
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT p.id, p.doc_id, p.text, "
            "       COALESCE(d.name, '') AS doc_name, COALESCE(d.lang, '') AS lang "
            "FROM pages p JOIN docs d ON d.id = p.doc_id "
            "WHERE p.context IS NULL OR p.context = '' "
            "ORDER BY p.doc_id, p.page_no "
            "LIMIT %s",
            (_MAX,),
        ).fetchall()

    total = len(rows)
    print(f"[enrich] {pending} pages need context; processing up to {total} this run.")

    outlines: dict[int, str] = {}
    done = filled = 0
    for r in rows:
        done += 1
        doc_id = r["doc_id"]
        if doc_id not in outlines:
            outlines[doc_id] = _outline_for(doc_id)
        ctx = enrich_page(r["doc_name"], outlines[doc_id], r["text"] or "", r["lang"])
        if ctx:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE pages SET context = %s WHERE id = %s", (ctx, r["id"])
                )
            filled += 1
        if done % 10 == 0 or done == total:
            print(f"[enrich] {done}/{total} processed, {filled} filled")

    print(f"[enrich] done: {filled} pages enriched ({total - filled} left empty).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
