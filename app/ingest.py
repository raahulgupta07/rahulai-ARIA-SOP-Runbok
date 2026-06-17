"""Ingest pipeline: file -> page PNGs -> PageIndex tree -> docs/pages rows.

Vectorless: PageIndex builds a reasoning tree (TOC + summaries). We also keep
per-page rendered images (for answer-with-picture) and per-page extracted text
(fallback retrieval / display). Burmese-safe: no embeddings, no OCR required for
text-bearing PDFs; scanned docs still get page images for vision answering.
"""
import os
import sys
import json
from pathlib import Path

import fitz  # pymupdf
import litellm

litellm.suppress_debug_info = True  # quiet retry "Provider List" noise

from .config import (
    OPENROUTER_API_KEY,
    INGEST_MODEL,
    PAGES_DIR,
    ROOT,
)
from .db import get_conn, log_ingest
from .brain_tools import _walk_tree

# make OpenRouter key visible to litellm (used inside vendored pageindex)
if OPENROUTER_API_KEY:
    os.environ.setdefault("OPENROUTER_API_KEY", OPENROUTER_API_KEY)

# vendored PageIndex on path
_VENDOR = str(ROOT / "vendor_pageindex")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
# higher DPI = legible screenshot-in-screenshot (Phase 0). Env-tunable, default 220.
RENDER_DPI = int(os.getenv("RENDER_DPI", "220"))


def detect_lang(text: str) -> str:
    """my if Burmese script present, en otherwise, unknown if no text."""
    if not text:
        return "unknown"
    burmese = sum(1 for ch in text if "က" <= ch <= "႟")
    return "my" if burmese >= 5 else "en"


def _image_to_pdf(img_path: Path, out_pdf: Path) -> Path:
    """Wrap a standalone image into a 1-page PDF so the same flow handles it."""
    doc = fitz.open()
    img = fitz.open(str(img_path))
    rect = img[0].rect
    pdf_bytes = img.convert_to_pdf()
    img.close()
    src = fitz.open("pdf", pdf_bytes)
    page = doc.new_page(width=rect.width, height=rect.height)
    page.show_pdf_page(page.rect, src, 0)
    doc.save(str(out_pdf))
    doc.close()
    return out_pdf


def render_pages(pdf_path: Path, doc_slug: str) -> list[dict]:
    """Render each PDF page to PNG + extract text. Returns list of page dicts."""
    out_dir = Path(PAGES_DIR) / doc_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    pages = []
    zoom = RENDER_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    from . import s3client
    use_s3 = s3client.backend() == "s3" and s3client.enabled()
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat)
        img_path = out_dir / f"{i}.png"
        pix.save(str(img_path))
        stored = str(img_path)
        if use_s3:
            # durable page image in S3; image_path = "s3:<bucket-key>" (served from S3)
            try:
                okey = s3client.prefixed(f"pages/{doc_slug}/{i}.png")
                s3client.put_file(img_path, okey, content_type="image/png")
                stored = f"s3:{okey}"
            except Exception as e:
                print(f"[ingest] s3 page upload failed (keeping local): {e!r}")
        text = (page.get_text("text") or "").strip()
        pages.append(
            {
                "page_no": i,
                "image_path": stored,
                "text": text,
                "text_layer": text,
                "vision_text": "",
            }
        )
    doc.close()
    return pages


def build_tree(pdf_path: Path) -> dict | None:
    """Build PageIndex reasoning tree. Returns None on failure (non-fatal)."""
    try:
        from pageindex import page_index  # vendored
        tree = page_index(str(pdf_path), model=INGEST_MODEL)
        return tree
    except Exception as e:  # scanned/no-text/LLM error -> fall back to flat tree
        print(f"[ingest] tree build failed, using flat tree: {e!r}")
        return None


def _flat_tree(name: str, pages: list[dict]) -> list[dict]:
    """Minimal tree when PageIndex can't run: one node per page."""
    return [
        {
            "title": f"{name} - page {p['page_no']}",
            "physical_index": p["page_no"],
            "summary": (p["text"][:300] if p["text"] else ""),
        }
        for p in pages
    ]


def _set(doc_id: int, **fields) -> None:
    """Patch a docs row (status/progress/pages_done/...). Fail-soft."""
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = now()"
    vals = list(fields.values()) + [doc_id]
    try:
        with get_conn() as conn:
            conn.execute(f"UPDATE docs SET {cols} WHERE id = %s", vals)
    except Exception as e:  # progress writes must never crash the pipeline
        print(f"[ingest] _set({doc_id}) failed: {e!r}")


def _log_event(doc_id: int, stage: str, msg: str = "") -> None:
    """Append one row to the per-document ingest_events timeline (read by the
    ops cockpit GET /api/documents/{id}/log). Fail-soft — own short-lived
    autocommit connection, never raises into the pipeline."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ingest_events (doc_id, stage, msg) VALUES (%s, %s, %s)",
                (doc_id, (stage or "")[:60], (msg or "")[:500]),
            )
    except Exception as e:  # event logging must never crash ingest
        print(f"[ingest_events] write skipped: {e!r}")


def process_doc(doc_id: int, src_path: Path, display_name: str, should_cancel=None) -> dict:
    """Run the ingest pipeline for an EXISTING docs row (status flow:
    processing -> ready/failed), writing live progress as it goes.

    Rerun-safe: clears any prior pages/nodes for this doc first, so a requeued
    or retried doc never duplicates rows. Raises on failure (caller marks the
    row 'failed'). `should_cancel()` (optional) is polled between steps; when it
    returns True a Cancelled is raised so the worker can stop this doc cleanly.
    """
    from .ingest_control import Cancelled

    def _ck() -> None:
        if should_cancel and should_cancel():
            raise Cancelled(f"doc {doc_id} cancelled")

    src_path = Path(src_path)
    ext = src_path.suffix.lower()

    # normalise to a PDF for rendering + tree
    if ext in IMAGE_EXTS:
        pdf_path = src_path.with_suffix(".pdf")
        _image_to_pdf(src_path, pdf_path)
    elif ext == ".pdf":
        pdf_path = src_path
    else:
        raise ValueError(f"unsupported file type: {ext}")

    # stable, collision-free page-image folder keyed by doc id
    doc_slug = f"doc_{doc_id}"

    log_ingest(doc_id, "queued", f"⬆ queued: {display_name}")
    _log_event(doc_id, "queued", f"queued: {display_name}")
    _ck()
    _set(doc_id, status="processing", progress=10)
    _log_event(doc_id, "processing", "started processing")
    pages = render_pages(pdf_path, doc_slug)
    _set(doc_id, progress=30, page_count=len(pages))
    _log_event(doc_id, "rendered", f"rendered {len(pages)} page(s)")
    _ck()

    # vision pre-read: transcribe screenshot text not in the PDF text layer.
    # report per-page progress (30 -> 80) so the UI bar climbs live.
    from .vision import preread_pages

    n = len(pages) or 1
    log_ingest(doc_id, "page", f"👁 reading pages… {n} total")
    _log_event(doc_id, "reading", f"reading {n} page(s) with vision")

    def _on_page(done: int) -> None:
        _ck()                       # abort between pages on stop/cancel
        _set(doc_id, pages_done=done, progress=30 + int(done / n * 50))
        log_ingest(doc_id, "page", f"👁 reading page {done}/{n}")
        _log_event(doc_id, "page", f"read page {done}/{n}")

    preread_pages(pages, on_page=_on_page, should_cancel=should_cancel)
    _ck()

    _set(doc_id, progress=85)
    log_ingest(doc_id, "tree", f"🌳 building page tree… {len(pages)} pages")
    _log_event(doc_id, "structure", f"building page tree ({len(pages)} pages)")
    tree = build_tree(pdf_path)
    has_tree = tree is not None
    if not has_tree:
        tree = _flat_tree(display_name, pages)

    lang = detect_lang("\n".join(p["text"] for p in pages[:5]))

    with get_conn() as conn:
        # rerun-safe: wipe any prior children for this doc
        conn.execute("DELETE FROM pages WHERE doc_id = %s", (doc_id,))
        conn.execute("DELETE FROM nodes WHERE doc_id = %s", (doc_id,))
        conn.execute(
            "UPDATE docs SET lang = %s, tree_json = %s, page_count = %s "
            "WHERE id = %s",
            (lang, json.dumps(tree, ensure_ascii=False), len(pages), doc_id),
        )
        # precompute flattened tree nodes for fast section retrieval (also reused
        # as a compact outline for Contextual Retrieval enrichment below)
        flat = []
        _walk_tree(tree, doc_id, display_name, flat)
        # compact outline string from node titles (cheap; bounded)
        from .context_enrich import enrich_page, CONTEXT_ENRICH_ENABLED
        outline = ""
        if CONTEXT_ENRICH_ENABLED:
            titles = [t for t in (nd.get("title") or "" for nd in flat) if t]
            outline = "\n".join(titles[:120])[:1500]
        for p in pages:
            # Contextual Retrieval: 1-2 sentence blurb situating this page in the
            # document -> folded into pages.context -> contributes to tsv (FTS).
            # Fail-soft: enrich_page returns "" on any error/disabled flag.
            ctx = ""
            if CONTEXT_ENRICH_ENABLED:
                _ck()  # abort between pages on stop/cancel (mirror vision loop)
                ctx = enrich_page(display_name, outline, p["text"], lang)
                if ctx:
                    _log_event(doc_id, "context", f"context enriched p.{p['page_no']}")
            conn.execute(
                "INSERT INTO pages (doc_id, page_no, image_path, text, "
                "text_layer, vision_text, context) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    doc_id,
                    p["page_no"],
                    p["image_path"],
                    p["text"],
                    p.get("text_layer", ""),
                    p.get("vision_text", ""),
                    ctx,
                ),
            )
        for nd in flat:
            conn.execute(
                "INSERT INTO nodes (doc_id, page_no, title, summary) "
                "VALUES (%s, %s, %s, %s)",
                (doc_id, nd["page_no"], nd["title"], nd["summary"]),
            )

    # compiled wiki layer (vision-once -> clean markdown). One-time, fail-soft:
    # if it errors, chat still falls back to raw page text.
    _ck()
    from .config import COMPILE_ON_INGEST
    if COMPILE_ON_INGEST:
        try:
            _set(doc_id, progress=95)
            log_ingest(doc_id, "compile", "📝 compiling wiki")
            _log_event(doc_id, "compile", "compiling wiki markdown")
            from .compile_wiki import compile_doc
            compile_doc(doc_id, display_name)
        except Exception as e:
            print(f"[ingest] compile_doc({doc_id}) failed (non-fatal): {e!r}")

    # type-aware compile: classify doc, then route to the matching compiler
    # (reference -> lookup table; sop/runbook/policy -> playbook w/ tuned prompt).
    from .config import COMPILE_PLAYBOOK, TYPE_AWARE_COMPILE
    doc_type = "sop"
    if TYPE_AWARE_COMPILE:
        try:
            from .doc_type import classify_doc_type
            doc_type = classify_doc_type(doc_id, display_name) or "sop"
            log_ingest(doc_id, "doc_type", f"🏷 type · {doc_type}")
            _log_event(doc_id, "doc_type", f"classified type: {doc_type}")
        except Exception as e:
            print(f"[ingest] classify_doc_type({doc_id}) failed (non-fatal): {e!r}")

    if TYPE_AWARE_COMPILE and doc_type == "reference":
        try:
            log_ingest(doc_id, "lookup", "building lookup table")
            from .compile_lookup import extract_lookup
            n = extract_lookup(doc_id)
            _log_event(doc_id, "lookup", f"extracted {n} lookup rows")
        except Exception as e:
            print(f"[ingest] extract_lookup({doc_id}) failed (non-fatal): {e!r}")
    elif COMPILE_PLAYBOOK:
        try:
            log_ingest(doc_id, "playbook", "📋 building playbook")
            _log_event(doc_id, "playbook", "compiling user playbook")
            from .compile_playbook import compile_playbook
            compile_playbook(doc_id, doc_type)
        except Exception as e:
            print(f"[ingest] compile_playbook({doc_id}) failed (non-fatal): {e!r}")

    # entity index: extract recurring entities for cross-doc linking (Phase 3)
    from .config import ENTITY_INDEX, KB_LINT
    if ENTITY_INDEX:
        try:
            from .entities import extract_entities
            ne = extract_entities(doc_id)
            log_ingest(doc_id, "entities", f"indexed {ne} entities")
            _log_event(doc_id, "entities", f"indexed {ne} entities")
        except Exception as e:
            print(f"[ingest] extract_entities({doc_id}) failed (non-fatal): {e!r}")

    # conflict lint: recompute same-term/divergent-value conflicts (Phase 4, cheap)
    if KB_LINT:
        try:
            from .kb_lint import run_lint
            run_lint()
        except Exception as e:
            print(f"[ingest] run_lint failed (non-fatal): {e!r}")

    # troubleshooting tree: compile a decision tree if the doc has branching (Phase 6.2)
    from .config import TROUBLESHOOT_TREE
    if TROUBLESHOOT_TREE:
        try:
            from .compile_tree import compile_tree
            nt = compile_tree(doc_id)
            if nt:
                log_ingest(doc_id, "tree", "built troubleshooting tree")
                _log_event(doc_id, "tree", "compiled decision tree")
        except Exception as e:
            print(f"[ingest] compile_tree({doc_id}) failed (non-fatal): {e!r}")

    # procedure chaining: detect cross-doc prerequisites for this doc (Phase 5)
    from .config import PROC_CHAINING
    if PROC_CHAINING:
        try:
            from .dependencies import detect_dependencies
            nd = detect_dependencies(doc_id)
            _log_event(doc_id, "deps", f"detected {nd} prerequisite link(s)")
        except Exception as e:
            print(f"[ingest] detect_dependencies({doc_id}) failed (non-fatal): {e!r}")

    # auto-category: one LLM topic tag (uses the compiled summary if present)
    from .config import AUTO_CATEGORIZE
    if AUTO_CATEGORIZE:
        try:
            from .categorize import categorize_doc
            cat = categorize_doc(doc_id)
            if cat:
                log_ingest(doc_id, "category", f"🏷 categorized · {cat}")
                _log_event(doc_id, "category", f"categorized: {cat}")
        except Exception as e:
            print(f"[ingest] categorize_doc({doc_id}) failed (non-fatal): {e!r}")

    # auto-facts: extract durable facts from the compiled doc → pending review
    from .config import AUTO_FACTS_ENABLED, AUTO_QA_ENABLED
    if AUTO_FACTS_ENABLED:
        try:
            from .doc_facts import extract_doc_facts
            extract_doc_facts(doc_id, display_name)
        except Exception as e:
            print(f"[ingest] extract_doc_facts({doc_id}) failed (non-fatal): {e!r}")

    # auto-Q&A: mine grounded question/answer pairs from the doc → review-gated bank
    if AUTO_QA_ENABLED:
        try:
            from .doc_qa import extract_doc_qa
            extract_doc_qa(doc_id, display_name)
        except Exception as e:
            print(f"[ingest] extract_doc_qa({doc_id}) failed (non-fatal): {e!r}")

    # refresh the curated bilingual home starter chips now the corpus grew (1 LLM call)
    try:
        from .starter_chips import refresh as _refresh_chips
        _refresh_chips()
    except Exception as e:
        print(f"[ingest] starter_chips refresh failed (non-fatal): {e!r}")

    log_ingest(doc_id, "ready", f"✓ ready · indexed {len(pages)} pages")
    _log_event(doc_id, "ready", f"ready — indexed {len(pages)} page(s)")
    return {
        "doc_id": doc_id,
        "page_count": len(pages),
        "has_tree": has_tree,
        "lang": lang,
    }


def ingest_file(src_path: Path, display_name: str) -> dict:
    """Synchronous full ingest (scripts / back-compat). Creates a docs row and
    processes it inline. The async path (upload + worker) inserts the queued
    row itself and calls process_doc directly."""
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO docs (name, status, progress) "
            "VALUES (%s, 'processing', 1) RETURNING id",
            (display_name,),
        ).fetchone()
    doc_id = row["id"]
    try:
        result = process_doc(doc_id, src_path, display_name)
    except Exception as e:
        _set(doc_id, status="failed")
        _log_event(doc_id, "failed", f"ingest failed: {e}")
        raise
    _set(doc_id, status="ready", progress=100, ready_at=_now())
    return result


def _now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc)
