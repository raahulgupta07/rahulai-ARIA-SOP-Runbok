"""Master catalog (Phase 7) — an auto "what Aria can help with" map.

Pure reads, no LLM, fail-soft. Builds from data already compiled by earlier
phases: documents grouped by category, each with its playbook goal (the
user-facing "what this lets you do"), plus the cross-cutting SYSTEMS (entity
index, kind='system') that connect many docs. Feeds a Capabilities view and can
seed starter chips.
"""
from .db import get_conn


def build_catalog() -> dict:
    """Return {groups:[{name, items:[{doc_id, title, goal, doc_type}]}],
    systems:[{name, doc_count}], totals:{docs, playbooks, entities}}. Fail-soft."""
    try:
        with get_conn() as conn:
            docs = conn.execute(
                "SELECT d.id, d.name, "
                "COALESCE(NULLIF(d.category,''), 'General') AS category, "
                "d.doc_type, pb.goal "
                "FROM docs d LEFT JOIN doc_playbook pb ON pb.doc_id = d.id "
                "WHERE d.status = 'ready' ORDER BY category, d.name"
            ).fetchall()
            systems = conn.execute(
                "SELECT e.name, count(DISTINCT m.doc_id) AS doc_count "
                "FROM entities e JOIN entity_mention m ON m.entity_id = e.id "
                "WHERE e.kind = 'system' GROUP BY e.name "
                "HAVING count(DISTINCT m.doc_id) >= 2 "
                "ORDER BY doc_count DESC, e.name LIMIT 20"
            ).fetchall()
            n_pb = conn.execute("SELECT count(*) AS n FROM doc_playbook").fetchone()
            n_ent = conn.execute("SELECT count(*) AS n FROM entities").fetchone()

        groups: dict[str, list] = {}
        for d in docs:
            groups.setdefault(d["category"], []).append({
                "doc_id": d["id"],
                "title": d["name"],
                "goal": d.get("goal") or "",
                "doc_type": d.get("doc_type") or "",
            })
        out_groups = [{"name": k, "items": v} for k, v in groups.items()]
        return {
            "groups": out_groups,
            "systems": list(systems),
            "totals": {
                "docs": len(docs),
                "playbooks": (n_pb or {}).get("n", 0),
                "entities": (n_ent or {}).get("n", 0),
            },
        }
    except Exception as e:
        print(f"[catalog] build failed (non-fatal): {e!r}")
        return {"groups": [], "systems": [], "totals": {}}
