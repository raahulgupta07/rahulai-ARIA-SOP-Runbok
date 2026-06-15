"""Backfill nodes table + split text_layer/vision_text for docs ingested before
the hybrid-retrieval upgrade. Idempotent."""
import json

from app.db import get_conn
from app.brain_tools import _walk_tree


def main():
    with get_conn() as conn:
        docs = conn.execute("SELECT id, name, tree_json FROM docs ORDER BY id").fetchall()
        print(f"{len(docs)} docs")

        # 1. nodes
        for d in docs:
            have = conn.execute(
                "SELECT count(*) AS c FROM nodes WHERE doc_id = %s", (d["id"],)
            ).fetchone()["c"]
            if have:
                continue
            tree = d["tree_json"]
            if isinstance(tree, str):
                tree = json.loads(tree)
            flat = []
            _walk_tree(tree, d["id"], d["name"], flat)
            for n in flat:
                conn.execute(
                    "INSERT INTO nodes (doc_id, page_no, title, summary) "
                    "VALUES (%s, %s, %s, %s)",
                    (d["id"], n["page_no"], n["title"], n["summary"]),
                )
        total_nodes = conn.execute("SELECT count(*) AS c FROM nodes").fetchone()["c"]
        print(f"nodes now: {total_nodes}")

        # 2. split text -> text_layer / vision_text where not set
        pages = conn.execute(
            "SELECT id, text FROM pages WHERE vision_text IS NULL OR vision_text = ''"
        ).fetchall()
        n = 0
        for p in pages:
            t = p["text"] or ""
            if "[VISION]" in t:
                layer, _, vis = t.partition("[VISION]")
                layer, vis = layer.strip(), vis.strip()
            else:
                layer, vis = t.strip(), ""
            conn.execute(
                "UPDATE pages SET text_layer = %s, vision_text = %s WHERE id = %s",
                (layer, vis, p["id"]),
            )
            n += 1
        print(f"split text on {n} pages")


if __name__ == "__main__":
    main()
