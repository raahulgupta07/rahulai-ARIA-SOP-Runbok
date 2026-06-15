"""One-time backfill: compile every ready doc into the wiki layer
(doc_pages_md + doc_wiki) from its ALREADY-STORED vision text.

No re-vision, no re-upload. Idempotent — re-running replaces prior compiled rows.

    .venv/bin/python scripts/compile_all.py            # all ready docs
    .venv/bin/python scripts/compile_all.py 12 14      # only these doc ids
"""
import sys

from app.db import get_conn
from app.compile_wiki import compile_doc


def main(argv: list[str]) -> None:
    ids = [int(x) for x in argv if x.isdigit()]
    with get_conn() as conn:
        if ids:
            rows = conn.execute(
                "SELECT id, name FROM docs WHERE id = ANY(%s) ORDER BY id", (ids,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name FROM docs WHERE status = 'ready' ORDER BY id"
            ).fetchall()

    print(f"compiling {len(rows)} doc(s)…")
    for i, d in enumerate(rows, 1):
        try:
            res = compile_doc(d["id"], d["name"])
            print(f"  [{i}/{len(rows)}] doc {d['id']} {d['name'][:42]:42} "
                  f"-> {res['pages']}p {res['chars']} chars")
        except Exception as e:
            print(f"  [{i}/{len(rows)}] doc {d['id']} FAILED: {e!r}")
    print("done.")


if __name__ == "__main__":
    main(sys.argv[1:])
