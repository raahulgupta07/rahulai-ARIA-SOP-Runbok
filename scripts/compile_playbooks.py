"""One-time backfill: compile every ready doc into a user-facing playbook
(doc_playbook) from its ALREADY-COMPILED wiki markdown.

No re-vision, no re-upload. Idempotent — re-running UPSERTs the playbook row.

    .venv/bin/python scripts/compile_playbooks.py            # all ready docs
    .venv/bin/python scripts/compile_playbooks.py 9 11       # only these doc ids
"""
import sys

from app.db import get_conn
from app.compile_playbook import compile_playbook


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

    print(f"building playbooks for {len(rows)} doc(s)…")
    ok = 0
    for i, d in enumerate(rows, 1):
        res = compile_playbook(d["id"])
        ok += 1 if res else 0
        print(f"  [{i}/{len(rows)}] doc {d['id']} {d['name'][:42]:42} -> "
              f"{'OK' if res else 'skip/fail'}")
    print(f"done. {ok}/{len(rows)} playbooks written.")


if __name__ == "__main__":
    main(sys.argv[1:])
