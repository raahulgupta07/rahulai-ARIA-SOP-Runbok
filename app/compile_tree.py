"""Troubleshooting decision-tree compilation — walkable branch logic at ingest.

For TROUBLESHOOTING / runbook documents that contain real decision logic ("if X
is the case do Y, otherwise do Z", "if the error is A → fix B; if B → fix C")
the useful experience is to WALK the diagnosis: answer a question → branch →
reach a fix. This module runs ONE LLM pass over a doc's already-compiled
page-marked markdown and compiles an interactive decision tree (question /
action / fix nodes) into `doc_tree` (table already exists). A purely LINEAR
procedure with no branching produces NO tree (compiles to nothing).

Mirrors compile_lookup.py / compile_playbook.py: per-page-capped concat of
doc_pages_md, ONE JSON-returning LLM call, robust fence-stripping `_parse_json`,
jsonb via json.dumps(...) + %s::jsonb, `if not _client: return 0`. Fail-soft:
any error -> print + return 0. Never raises.
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
)

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You convert an IT troubleshooting/runbook document into an interactive "
    "decision tree the user can WALK. Only produce nodes if the document has "
    "real branching/conditional logic (if/then/else, 'if the error is X do Y', "
    "different causes -> different fixes). If it is a purely linear procedure "
    'with no branching, reply exactly {"nodes": []}. Otherwise reply STRICT '
    'JSON: {"start": "n1", "nodes": [ {"id":"n1","type":"question","text":"...",'
    '"options":[{"label":"...","next":"n2"}]}, {"id":"n2","type":"fix","text":'
    '"the resolution, keep exact codes/paths/SQL verbatim"} ]}. Node types: '
    "'question' (a branch, has 2+ options each with label+next), 'action' (do "
    "something then continue — exactly one option with next), 'fix' (terminal "
    "resolution, no options needed). Keep all codes/paths/commands verbatim. "
    "Max 25 nodes."
)

# cap any single page's contribution so one degenerate/looped page (e.g. a 126k
# repeated-table compile) can't eat the whole window and starve the real logic.
_PER_PAGE_CAP = 6000

_TYPES = {"question", "action", "fix"}


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _doc_text(doc_id: int) -> str:
    """Compiled text for a doc: concat doc_pages_md in page order, each page
    truncated to _PER_PAGE_CAP so no single monster page dominates. Joined with
    '## Page N' markers (same as the sibling compilers). Empty string if none."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
    parts = []
    for r in rows:
        md = (r["md"] or "").strip()
        if not md:
            continue
        if len(md) > _PER_PAGE_CAP:
            md = md[:_PER_PAGE_CAP] + " …[truncated]"
        parts.append(f"## Page {r['page_no']}\n\n{md}")
    return "\n\n".join(parts).strip()


def _parse_json(raw: str) -> dict:
    """Robustly parse the model's JSON: strip ```json fences, tolerate surrounding
    prose, fall back to the first {...} block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def compile_tree(doc_id: int) -> int:
    """Compile one doc's already-compiled text into an interactive decision tree
    and UPSERT it into doc_tree. Returns 1 if a non-empty tree was written, 0 on
    skip / empty / linear-no-branch / parse-fail / error. Never raises."""
    try:
        if not _client:
            return 0
        body = _doc_text(doc_id)
        if not body:
            return 0

        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": body[:40000]},
            ],
            max_tokens=4000,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, dict):
            print(f"[compile_tree] {doc_id}: parsed JSON not an object")
            return 0

        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            # linear procedure / no branching / empty -> write nothing
            return 0

        # first pass: collect the set of declared node ids (only well-formed ones)
        known = set()
        for n in raw_nodes:
            if isinstance(n, dict):
                nid = (str(n.get("id")) if n.get("id") is not None else "").strip()
                if nid:
                    known.add(nid)

        # second pass: coerce each node, dropping options whose next is unknown
        nodes = []
        for n in raw_nodes:
            if not isinstance(n, dict):
                continue
            nid = (str(n.get("id")) if n.get("id") is not None else "").strip()
            if not nid:
                continue
            ntype = (n.get("type") or "").strip().lower()
            if ntype not in _TYPES:
                ntype = "action"
            text = (n.get("text") or "").strip()
            options = []
            for o in (n.get("options") or []):
                if not isinstance(o, dict):
                    continue
                nxt = (str(o.get("next")) if o.get("next") is not None else "").strip()
                if nxt not in known:
                    continue  # drop dangling reference
                options.append({
                    "label": (o.get("label") or "").strip(),
                    "next": nxt,
                })
            nodes.append({
                "id": nid,
                "type": ntype,
                "text": text,
                "options": options,
            })

        if not nodes:
            return 0

        node_ids = {n["id"] for n in nodes}
        start = (str(data.get("start")) if data.get("start") is not None else "").strip()
        if start not in node_ids:
            start = nodes[0]["id"]

        tree = {"start": start, "nodes": nodes}
        payload = json.dumps(tree, ensure_ascii=False)
        chars = len(payload)

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO doc_tree (doc_id, tree, chars) "
                "VALUES (%s, %s::jsonb, %s) "
                "ON CONFLICT (doc_id) DO UPDATE SET "
                "tree = EXCLUDED.tree, chars = EXCLUDED.chars",
                (doc_id, payload, chars),
            )
        return 1
    except Exception as e:
        print(f"[compile_tree] {doc_id} failed: {e!r}")
        return 0
