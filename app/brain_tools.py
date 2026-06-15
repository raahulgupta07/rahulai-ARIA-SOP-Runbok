"""Toolbelt the DocSensei brain drives itself (Scout-style navigation).

The agent calls these tools to navigate the 28 SOP trees, read pages, and
write what it learns. All return strings (Agno tool contract). Page *images*
are supplied separately to the run for vision answering.
"""
import json

from .db import get_conn
from .memory import add_memory, memory_facts


_CHILD_KEYS = ("structure", "nodes", "children", "subsections", "sub_sections")


def _walk_tree(nodes, doc_id, doc_name, out):
    """Flatten a PageIndex tree into (title, summary, page_no) rows.
    Handles the wrapper dict {doc_name, structure:[...]} and nested children."""
    if isinstance(nodes, dict):
        nodes = [nodes]
    if not isinstance(nodes, list):
        return
    for n in nodes:
        if not isinstance(n, dict):
            continue
        title = n.get("title") or ""
        summary = n.get("summary") or ""
        page = (
            n.get("physical_index")
            or n.get("page")
            or n.get("start_index")
        )
        if title or summary:  # skip pure wrapper nodes
            out.append(
                {
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "title": title,
                    "summary": summary,
                    "page_no": page,
                }
            )
        for key in _CHILD_KEYS:
            if n.get(key):
                _walk_tree(n[key], doc_id, doc_name, out)


def list_documents() -> str:
    """List every SOP/policy document available, with id, name and page count.
    Use this first to see what knowledge exists."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, page_count, lang FROM docs ORDER BY id"
        ).fetchall()
    if not rows:
        return "No documents uploaded yet."
    return "\n".join(
        f"[{r['id']}] {r['name']} ({r['page_count']} pages, {r['lang']})"
        for r in rows
    )


def open_outline(doc_id: int) -> str:
    """Show the table-of-contents outline (sections + page numbers) of one
    document so you can navigate to the right section. Pass the document id."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, tree_json FROM docs WHERE id = %s", (doc_id,)
        ).fetchone()
    if not row:
        return f"No document with id {doc_id}."
    tree = row["tree_json"]
    if isinstance(tree, str):
        tree = json.loads(tree)
    flat = []
    _walk_tree(tree, doc_id, row["name"], flat)
    if not flat:
        return f"{row['name']}: no outline (flat document)."
    lines = [f"Outline of {row['name']}:"]
    for f in flat:
        pg = f"p{f['page_no']}" if f["page_no"] else "p?"
        lines.append(f"  {pg}: {f['title']}  {('- ' + f['summary'][:120]) if f['summary'] else ''}")
    return "\n".join(lines)


def read_page(doc_id: int, page_no: int) -> str:
    """Read the extracted text of a specific page (doc_id + page number).
    Use to verify content before answering. (The page IMAGE is also shown to you
    when this page is a candidate.)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, text FROM pages WHERE doc_id = %s AND page_no = %s",
            (doc_id, page_no),
        ).fetchone()
    if not row:
        return f"No page {page_no} in document {doc_id}."
    txt = row["text"] or "(no extracted text — read the page image)"
    return f"[page_id {row['id']}] doc {doc_id} page {page_no}:\n{txt[:1500]}"


def remember_fact(fact: str) -> str:
    """Save a new fact, rule, or correction the user teaches you, so you recall
    it in future answers. Use when the user tells you something not in the docs."""
    # review gate: agent-learned facts land 'pending' until an admin approves
    mid = add_memory(fact, source="chat", status="pending")
    return f"Saved for review (memory #{mid}): {fact}"


def recall_facts() -> str:
    """Recall everything you have been taught (facts not in the documents)."""
    facts = memory_facts()
    return "\n".join(f"- {f['text']}" for f in facts) if facts else "No learned facts yet."
