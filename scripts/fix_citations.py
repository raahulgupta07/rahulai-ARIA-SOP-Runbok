"""One-off: clean historically mis-attributed citations.

Before the 2026-06-14 citation fix, the model echoed page *numbers* into the
PAGES line but the code stored them as page *ids*, so old `messages.pages`
sometimes point at the wrong document. We can't recover the true citation
retroactively (would need to re-run retrieval + the LLM), but we CAN detect
rows whose stored citations are inconsistent with the answer's own inline
[p.N] markers, and drop those bogus citations so analytics (doc usage, blind
spots, graph) stop counting the wrong document.

A stored page is kept only if its real page_no appears in the answer's inline
[p.N] set. Messages with no inline markers are left untouched (can't judge).

Usage:
  python scripts/fix_citations.py            # DRY RUN — report only
  python scripts/fix_citations.py --commit   # apply (overwrites messages.pages)
"""
import json
import re
import sys

from app.db import get_conn

CITE = re.compile(r"\[p\.(\d{1,4})\]")


def main(commit: bool) -> None:
    scanned = inconsistent = cleared_msgs = dropped_cites = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, pages FROM messages "
            "WHERE role='bot' AND pages IS NOT NULL AND pages::text <> '[]'"
        ).fetchall()

        for r in rows:
            pages = r["pages"] if isinstance(r["pages"], list) else json.loads(r["pages"] or "[]")
            if not pages:
                continue
            scanned += 1
            cited_nos = {int(n) for n in CITE.findall(r["text"] or "")}
            if not cited_nos:
                continue  # no inline markers — cannot judge, leave as-is

            pids = [p.get("page_id") for p in pages if p.get("page_id")]
            real = {}
            if pids:
                for pr in conn.execute(
                    "SELECT id, page_no FROM pages WHERE id = ANY(%s)", (pids,)
                ).fetchall():
                    real[pr["id"]] = pr["page_no"]

            keep = [p for p in pages if real.get(p.get("page_id")) in cited_nos]
            if len(keep) != len(pages):
                inconsistent += 1
                dropped_cites += len(pages) - len(keep)
                if commit:
                    conn.execute("UPDATE messages SET pages=%s WHERE id=%s",
                                 (json.dumps(keep), r["id"]))
                    cleared_msgs += 1

    mode = "COMMITTED" if commit else "DRY RUN (no writes)"
    print(f"[{mode}]")
    print(f"  bot messages with citations scanned : {scanned}")
    print(f"  messages with inconsistent citations: {inconsistent}")
    print(f"  bogus citations dropped             : {dropped_cites}")
    if commit:
        print(f"  messages updated                    : {cleared_msgs}")
    else:
        print("  re-run with --commit to apply")


if __name__ == "__main__":
    main("--commit" in sys.argv)
