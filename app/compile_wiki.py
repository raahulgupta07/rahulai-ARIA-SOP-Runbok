"""Compiled "wiki" layer — Karpathy second-brain pattern, adapted.

Vision runs ONCE at ingest (into pages.text). This module then reformats that raw
per-page text into CLEAN MARKDOWN, one time, and stores it in doc_pages_md +
doc_wiki. Chat reads the compiled markdown (complete, pre-digested) so even a small
model answers in full detail — and never re-reads the file.

Reformat, never summarise: every table, step, code, value, SQL and field is kept.
"""
import json

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL,
)

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_PAGE_SYS = (
    "You reformat one page of an IT SOP/runbook into CLEAN MARKDOWN.\n"
    "RULES:\n"
    "- REFORMAT ONLY. Never summarise, shorten, or drop anything.\n"
    "- Keep EVERY step, field name, code, transaction path, value, threshold, "
    "button label, SQL query and contact EXACTLY as written.\n"
    "- Render tables as proper markdown tables (| col | col |). Keep all rows.\n"
    "- Keep numbered procedures numbered and in order.\n"
    "- Preserve the original language (English or Burmese) verbatim.\n"
    "- Output ONLY the markdown for this page. No preamble, no commentary."
)

_DOC_SYS = (
    "You write a 2-4 sentence factual summary of an IT SOP from its pages. "
    "State what the SOP is for and the key process. No fluff. Same language as the doc."
)


def _compile_page(text: str) -> str:
    """Raw page text -> clean markdown (reformat, never omit). Fail-soft -> raw."""
    body = (text or "").strip()
    if not body:
        return ""
    if not _client:
        return body
    try:
        r = _client.chat.completions.create(
            model=COMPILE_MODEL,
            messages=[
                {"role": "system", "content": _PAGE_SYS},
                {"role": "user", "content": body[:24000]},
            ],
            max_tokens=8000,
            temperature=0,
        )
        return (r.choices[0].message.content or "").strip() or body
    except Exception as e:
        print(f"[compile] page reformat failed: {e!r}")
        return body            # fall back to raw text — never lose content


def _doc_summary(title: str, page_mds: list[str]) -> str:
    if not _client:
        return ""
    sample = "\n\n".join(page_mds)[:12000]
    try:
        r = _client.chat.completions.create(
            model=COMPILE_MODEL,
            messages=[
                {"role": "system", "content": _DOC_SYS},
                {"role": "user", "content": f"SOP: {title}\n\n{sample}"},
            ],
            max_tokens=400,
            temperature=0.1,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[compile] summary failed: {e!r}")
        return ""


def compile_doc(doc_id: int, title: str = "") -> dict:
    """Compile every page of one doc into doc_pages_md + doc_wiki. Idempotent
    (re-run replaces prior rows). Returns {pages, chars}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, text FROM pages WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
        if not title:
            d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
            title = (d or {}).get("name", "") if d else ""

    page_mds: list[str] = []
    total = 0
    with get_conn() as conn:
        conn.execute("DELETE FROM doc_pages_md WHERE doc_id = %s", (doc_id,))
        for r in rows:
            md = _compile_page(r["text"])
            page_mds.append(md)
            total += len(md)
            conn.execute(
                "INSERT INTO doc_pages_md (doc_id, page_no, md, chars) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (doc_id, page_no) DO UPDATE SET md = EXCLUDED.md, chars = EXCLUDED.chars",
                (doc_id, r["page_no"], md, len(md)),
            )

    doc_md = "\n\n".join(
        f"## Page {r['page_no']}\n\n{md}" for r, md in zip(rows, page_mds) if md
    )
    summary = _doc_summary(title, page_mds)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO doc_wiki (doc_id, title, summary, md, updated_at) "
            "VALUES (%s, %s, %s, %s, now()) "
            "ON CONFLICT (doc_id) DO UPDATE SET title = EXCLUDED.title, "
            "summary = EXCLUDED.summary, md = EXCLUDED.md, updated_at = now()",
            (doc_id, title, summary, doc_md),
        )
    return {"pages": len(rows), "chars": total}
