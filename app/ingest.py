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
    """Build PageIndex reasoning tree. Returns None on failure (non-fatal).

    page_index makes its own LLM calls with NO timeout — a stalled OpenRouter
    response would hang ingest forever (observed). Run it in a worker thread with
    a hard deadline; on timeout fall back to the flat tree so the doc still
    finishes. (The orphaned thread eventually unblocks on the client's own TCP
    timeout; it can't corrupt anything — its result is just discarded.)"""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout
    deadline = float(os.getenv("TREE_BUILD_TIMEOUT", "120"))

    def _run():
        from pageindex import page_index  # vendored
        return page_index(str(pdf_path), model=INGEST_MODEL)

    # NB: don't use `with` — its __exit__ joins the thread, which would re-block on
    # a hung page_index. Detach instead (wait=False) and let the daemon thread die
    # on its own once the underlying TCP call times out.
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(_run).result(timeout=deadline)
    except _FTimeout:
        print(f"[ingest] tree build exceeded {deadline:.0f}s — using flat tree")
        return None
    except Exception as e:  # scanned/no-text/LLM error -> fall back to flat tree
        print(f"[ingest] tree build failed, using flat tree: {e!r}")
        return None
    finally:
        ex.shutdown(wait=False)


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


def process_doc(doc_id: int, src_path: Path, display_name: str, should_cancel=None,
                defer_enrich: bool = False, enrich_mode: bool = False) -> dict:
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
    # enrich_mode = the Enrichment Agent running PHASE 2 on an already-answerable
    # doc. Keep it 'ready_lite' (answerable) throughout instead of flipping to
    # 'processing' (which would drop it from the answer gate). The page rebuild is
    # one atomic DELETE+INSERT tx, so readers never see a gap.
    if not enrich_mode:
        _set(doc_id, status="processing", progress=10)
    _log_event(doc_id, "processing", "started processing")

    # ── RESUME CACHE ──────────────────────────────────────────────────────────
    # A requeued doc (boot-sweep after a restart, or manual retry) would otherwise
    # re-run every expensive phase-2 step from scratch: vision on every page, the
    # PageIndex tree, and the per-page wiki compile — minutes of redundant LLM work.
    # Snapshot those outputs NOW (before phase 1 wipes pages) so we can reuse them.
    _pcache: dict[int, dict] = {}    # page_no -> full prior row (text/vision/context)
    _tree_cached = None              # prior real PageIndex tree (skip rebuild)
    _compile_cached = False          # prior wiki compile present (skip recompile)
    try:
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT page_no, text, text_layer, vision_text, context "
                "FROM pages WHERE doc_id = %s",
                (doc_id,),
            ).fetchall():
                _pcache[r["page_no"]] = dict(r)
            _wiki_row = conn.execute("SELECT 1 FROM doc_wiki WHERE doc_id = %s", (doc_id,)).fetchone()
            _doc_row = conn.execute("SELECT tree_json FROM docs WHERE id = %s", (doc_id,)).fetchone()
        # full resume only when the prior run got all the way to compile AND every
        # rendered page has a cached row with vision already done — otherwise re-run.
        if _wiki_row and _pcache and all(
            (_pcache.get(pn, {}).get("vision_text") or "") for pn in _pcache
        ):
            _tree_cached = (_doc_row or {}).get("tree_json")
            _compile_cached = True
    except Exception as e:
        print(f"[ingest] resume-cache load failed (non-fatal): {e!r}")
    if _compile_cached:
        log_ingest(doc_id, "resume", f"♻ resuming — reusing vision + tree + wiki for {len(_pcache)} page(s)")

    pages = render_pages(pdf_path, doc_slug)
    _set(doc_id, progress=30, page_count=len(pages))
    _log_event(doc_id, "rendered", f"rendered {len(pages)} page(s)")
    _ck()

    n = len(pages) or 1

    # ════ PHASE 1 · FAST TEXT (no vision, no LLM) — make the doc answerable NOW ════
    # render_pages already pulled each page's native text layer; pages.tsv (FTS) is
    # GENERATED from text, so the doc is fully searchable without vision. Insert the
    # pages + a cheap flat tree and flip to 'ready_lite' in ~seconds. PHASE 2 below
    # then runs vision/compile/enrichers and upgrades the same rows in place.
    lang = detect_lang("\n".join(p["text"] for p in pages[:5]))
    _flat_lite = _flat_tree(display_name, pages)
    with get_conn() as conn:
        conn.execute("DELETE FROM pages WHERE doc_id = %s", (doc_id,))   # rerun-safe
        conn.execute("DELETE FROM nodes WHERE doc_id = %s", (doc_id,))
        conn.execute(
            "UPDATE docs SET lang = %s, tree_json = %s, page_count = %s WHERE id = %s",
            (lang, json.dumps(_flat_lite, ensure_ascii=False), len(pages), doc_id),
        )
        for p in pages:
            conn.execute(
                "INSERT INTO pages (doc_id, page_no, image_path, text, "
                "text_layer, vision_text, context) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (doc_id, p["page_no"], p["image_path"], p["text"],
                 p.get("text_layer", ""), "", ""),
            )
        # lite section nodes: one per page (real PageIndex tree lands in PHASE 2).
        # NB: _flat_tree() dicts are keyed 'physical_index', so build from pages.
        for p in pages:
            conn.execute(
                "INSERT INTO nodes (doc_id, page_no, title, summary) VALUES (%s, %s, %s, %s)",
                (doc_id, p["page_no"], f"{display_name} - page {p['page_no']}",
                 (p["text"][:300] if p["text"] else "")),
            )
    _set(doc_id, status="ready_lite", progress=40, ready_at=_now())
    log_ingest(doc_id, "ready_lite", f"✓ text ready — answerable now · enriching {n} pages…")
    _log_event(doc_id, "ready_lite", "text indexed — doc answerable; enriching in background")

    # Deferred mode: stop after phase 1. The doc is answerable; the Enrichment
    # Agent (enrich_agent daemon) claims it later and runs phase 2 on its own
    # throttle/schedule. Keeps upload + first-answer instant under load.
    if defer_enrich:
        log_ingest(doc_id, "ready_lite", "⏳ queued for background enrichment")
        return {"doc_id": doc_id, "page_count": len(pages), "deferred": True, "lang": lang}

    # ════ PHASE 2 · DEEP ENRICH (vision, real tree, compile, enrichers) ════
    # vision pre-read: transcribe screenshot text not in the PDF text layer.
    # report per-page progress (40 -> 80) so the UI bar climbs live.
    from .vision import preread_pages

    log_ingest(doc_id, "page", f"👁 reading pages… {n} total")
    _log_event(doc_id, "reading", f"reading {n} page(s) with vision")

    def _on_page(done: int) -> None:
        _ck()                       # abort between pages on stop/cancel
        _set(doc_id, pages_done=done, progress=40 + int(done / n * 40))
        log_ingest(doc_id, "page", f"👁 reading page {done}/{n}")
        _log_event(doc_id, "page", f"read page {done}/{n}")

    if _compile_cached:
        # full resume: restore each page's prior text/vision/context — skip the
        # vision LLM pass entirely (the dominant per-page cost).
        for p in pages:
            c = _pcache.get(p["page_no"])
            if c:
                p["text"] = c.get("text") or p["text"]
                p["text_layer"] = c.get("text_layer") or p.get("text_layer", "")
                p["vision_text"] = c.get("vision_text") or ""
                p["context"] = c.get("context") or ""
        _set(doc_id, pages_done=len(pages), progress=80)
        log_ingest(doc_id, "page", f"♻ reused {len(pages)} vision page(s)")
    else:
        preread_pages(pages, on_page=_on_page, should_cancel=should_cancel)
    _ck()

    _set(doc_id, progress=85)
    if _tree_cached:
        # reuse the PageIndex tree from the prior run — skip the LLM rebuild
        log_ingest(doc_id, "tree", "🌳 reusing cached page tree")
        try:
            tree = _tree_cached if isinstance(_tree_cached, (dict, list)) else json.loads(_tree_cached)
        except Exception:
            tree = build_tree(pdf_path)
        has_tree = tree is not None
        if not has_tree:
            tree = _flat_tree(display_name, pages)
    else:
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
        # on resume the prior page context is in _pcache → reuse it, skip the LLM
        _ctx_enrich = CONTEXT_ENRICH_ENABLED and not _compile_cached
        outline = ""
        if _ctx_enrich:
            titles = [t for t in (nd.get("title") or "" for nd in flat) if t]
            outline = "\n".join(titles[:120])[:1500]
        for p in pages:
            # Contextual Retrieval: 1-2 sentence blurb situating this page in the
            # document -> folded into pages.context -> contributes to tsv (FTS).
            # Fail-soft: enrich_page returns "" on any error/disabled flag.
            ctx = p.get("context") or "" if _compile_cached else ""
            if _ctx_enrich:
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

    # per-embedded-image detailed explanation (SOP screenshots → which screen /
    # element / action). Runs BEFORE compile so the screenshot notes fold into the
    # wiki + retrieval. Pages are now in the DB so _merge_into_pages can update them.
    _ck()
    try:
        from . import doc_images
        if doc_images.DOC_IMAGES_ENABLED and not _compile_cached:
            ni = doc_images.extract_and_describe(doc_id, pdf_path, doc_slug)
            log_ingest(doc_id, "images", f"🖼 explained {ni} screenshots")
            _log_event(doc_id, "images", f"explained {ni} embedded images")
        elif _compile_cached:
            # image descriptions were merged into page text last run → already in _pcache
            log_ingest(doc_id, "images", "🖼 reusing cached screenshot notes")
    except Exception as e:
        print(f"[ingest] doc_images skipped: {e!r}")

    # compiled wiki layer (vision-once -> clean markdown). One-time, fail-soft:
    # if it errors, chat still falls back to raw page text.
    _ck()
    from .config import COMPILE_ON_INGEST
    if COMPILE_ON_INGEST and _compile_cached:
        log_ingest(doc_id, "compile", "📝 reusing cached wiki")
    elif COMPILE_ON_INGEST:
        try:
            _set(doc_id, progress=95)
            log_ingest(doc_id, "compile", "📝 compiling wiki")
            _log_event(doc_id, "compile", "compiling wiki markdown")
            from .compile_wiki import compile_doc
            compile_doc(doc_id, display_name)
        except Exception as e:
            print(f"[ingest] compile_doc({doc_id}) failed (non-fatal): {e!r}")

    # ---- whole-doc enrichers ----
    # Classify the doc FIRST (needed to dispatch lookup vs playbook), then run the
    # independent passes CONCURRENTLY. These used to run one-after-another (~7
    # sequential LLM round-trips = the long 95% wait); they all just read the
    # compiled doc_pages_md, so they parallelize cleanly. Each task is self-
    # contained + fail-soft; pool workers capped by ENRICH_WORKERS.
    from concurrent.futures import ThreadPoolExecutor
    from .config import (
        COMPILE_PLAYBOOK, TYPE_AWARE_COMPILE, ENTITY_INDEX, KB_LINT,
        TROUBLESHOOT_TREE, PROC_CHAINING, AUTO_CATEGORIZE,
        AUTO_FACTS_ENABLED, AUTO_QA_ENABLED,
    )

    doc_type = "sop"
    if TYPE_AWARE_COMPILE:
        try:
            from .doc_type import classify_doc_type
            doc_type = classify_doc_type(doc_id, display_name) or "sop"
            log_ingest(doc_id, "doc_type", f"type · {doc_type}")
            _log_event(doc_id, "doc_type", f"classified type: {doc_type}")
        except Exception as e:
            print(f"[ingest] classify_doc_type({doc_id}) failed (non-fatal): {e!r}")

    def _t_compile():
        if TYPE_AWARE_COMPILE and doc_type == "reference":
            from .compile_lookup import extract_lookup
            n = extract_lookup(doc_id)
            log_ingest(doc_id, "lookup", f"lookup · {n} rows")
            _log_event(doc_id, "lookup", f"extracted {n} lookup rows")
        elif COMPILE_PLAYBOOK:
            from .compile_playbook import compile_playbook
            compile_playbook(doc_id, doc_type)
            log_ingest(doc_id, "playbook", "playbook built")
            _log_event(doc_id, "playbook", "compiled user playbook")

    def _t_entities():
        if not ENTITY_INDEX:
            return
        from .entities import extract_entities
        ne = extract_entities(doc_id)
        log_ingest(doc_id, "entities", f"indexed {ne} entities")
        _log_event(doc_id, "entities", f"indexed {ne} entities")
        # GraphRAG-A: typed entity->entity relationships (needs entities first)
        try:
            from . import graphrag
            if graphrag.GRAPHRAG_ENABLED:
                nr = graphrag.extract_relationships(doc_id)
                log_ingest(doc_id, "graph", f"linked {nr} relationships")
        except Exception as e:
            print(f"[ingest] graphrag relationships skipped: {e!r}")

    def _t_tree():
        if not TROUBLESHOOT_TREE:
            return
        from .compile_tree import compile_tree
        if compile_tree(doc_id):
            log_ingest(doc_id, "tree", "built troubleshooting tree")
            _log_event(doc_id, "tree", "compiled decision tree")

    def _t_deps():
        if not PROC_CHAINING:
            return
        from .dependencies import detect_dependencies
        nd = detect_dependencies(doc_id)
        _log_event(doc_id, "deps", f"detected {nd} prerequisite link(s)")

    def _t_category():
        if not AUTO_CATEGORIZE:
            return
        from .categorize import categorize_doc
        cat = categorize_doc(doc_id)
        if cat:
            log_ingest(doc_id, "category", f"categorized · {cat}")
            _log_event(doc_id, "category", f"categorized: {cat}")

    def _t_facts():
        if not AUTO_FACTS_ENABLED:
            return
        from .doc_facts import extract_doc_facts
        extract_doc_facts(doc_id, display_name)

    def _t_qa():
        if not AUTO_QA_ENABLED:
            return
        from .doc_qa import extract_doc_qa
        extract_doc_qa(doc_id, display_name)

    def _t_contradiction():
        # Karpathy-wiki Phase 1: extract this doc's atomic claims, then compare them
        # against existing docs' claims → real value conflicts go to a review queue.
        from .config import SEMANTIC_CONTRADICTION
        if not SEMANTIC_CONTRADICTION:
            return
        from . import claims, contradiction
        nc = claims.extract_claims(doc_id, display_name)
        log_ingest(doc_id, "claims", f"extracted {nc} claims")
        nk = contradiction.detect_for_doc(doc_id)
        if nk:
            log_ingest(doc_id, "conflict", f"⚠ {nk} contradiction(s) flagged", level="warn")
            _log_event(doc_id, "conflict", f"flagged {nk} contradiction(s) for review")

    _tasks = [_t_compile, _t_entities, _t_tree, _t_deps, _t_category, _t_facts, _t_qa,
              _t_contradiction]
    log_ingest(doc_id, "enrich", "enriching (parallel)")
    _ENRICH_WORKERS = int(os.getenv("ENRICH_WORKERS", "7"))
    with ThreadPoolExecutor(max_workers=max(1, min(_ENRICH_WORKERS, len(_tasks)))) as _ex:
        for _f in [_ex.submit(t) for t in _tasks]:
            try:
                _f.result()
            except Exception as e:
                print(f"[ingest] enrich task failed (non-fatal): {e!r}")

    # conflict lint AFTER lookup + facts exist (reads doc_lookup + memory)
    if KB_LINT:
        try:
            from .kb_lint import run_lint
            run_lint()
        except Exception as e:
            print(f"[ingest] run_lint failed (non-fatal): {e!r}")

    # refresh curated home starter chips AFTER Q&A mined (1 LLM call)
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
