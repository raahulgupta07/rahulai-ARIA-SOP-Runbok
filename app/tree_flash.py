"""LLM-free document tree via vendored PageIndex Flash (opt-in pilot).

Flash builds the section tree from PDF layout statistics (font sizes, numbering,
bookmarks) — no LLM, no network. `build_tree_flash` returns the same shape
`brain_tools._walk_tree` consumes, or None whenever Flash found no real
hierarchy, so the caller falls back to the LLM builder / flat tree.

Standalone on purpose: no app.config / DB imports, so scripts/bench_tree.py and
the unit tests run without env or Postgres.
"""
from __future__ import annotations

import sys
from pathlib import Path

_VENDOR = str(Path(__file__).resolve().parent.parent / "vendor_pageindex")

# toc_source values that mean "no hierarchy found" -> let the caller fall back
_NO_TREE = {"pages", "unreadable"}


def _ensure_vendor_path() -> None:
    if _VENDOR not in sys.path:
        sys.path.insert(0, _VENDOR)


def _run_flash(pdf_path: str) -> dict:
    """Upstream `page_index_flash(path, summary=False, optimize=False)`, minus
    the process pool: `extract_toc(workers=1)` keeps parsing in this thread
    (upstream spawns processes for >=64-page PDFs, which a thread timeout can't
    stop and which re-imports __main__ inside uvicorn)."""
    _ensure_vendor_path()
    from flash_upstream.flash import api as fapi
    from flash_upstream.flash.main import extract_toc
    from flash_upstream.utils import strip_internal_keys

    result = extract_toc(fapi._validate_pdf(pdf_path), workers=1)
    structure = result.get("structure") or []
    if not structure:
        structure = fapi._page_nodes(result.get("page_texts") or [])
        result["structure"] = structure
        result["toc_source"] = "pages" if structure else "unreadable"
    elif structure[0]["start_index"] > 1:
        fapi._add_preface(structure)
    result.pop("page_texts", None)
    strip_internal_keys(structure)
    return result


def _convert_nodes(nodes) -> list[dict]:
    out = []
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        node = {
            "title": str(n.get("title") or "").strip(),
            "start_index": n.get("start_index"),
            "end_index": n.get("end_index", n.get("start_index")),
            "summary": n.get("summary") or "",
        }
        if n.get("node_id"):
            node["node_id"] = n["node_id"]
        kids = _convert_nodes(n.get("nodes"))
        if kids:
            node["nodes"] = kids
        out.append(node)
    return out


def _norm(title: str) -> str:
    return " ".join(title.lower().split())


def _dedupe(nodes: list[dict], seen: set[str]) -> list[dict]:
    """Drop repeat titles (keeping their children). On our SOPs the 'hybrid'
    bookmark merge re-hangs PURPOSE/SCOPE/... under PROCEDURE a second time;
    duplicate nodes would double-weight those titles in retrieval."""
    out = []
    for n in nodes:
        key = _norm(n["title"])
        kids = n.pop("nodes", None) or []
        if key and key in seen:
            out.extend(_dedupe(kids, seen))
            continue
        seen.add(key)
        kids = _dedupe(kids, seen)
        if kids:
            n["nodes"] = kids
        out.append(n)
    return out


def to_walk_shape(raw: dict) -> dict | None:
    """Flash result -> the `{doc_name, structure:[...]}` shape `_walk_tree` reads.
    None when Flash found no hierarchy (flat `pages`, `unreadable`, empty)."""
    if not isinstance(raw, dict):
        return None
    source = raw.get("toc_source")
    if source in _NO_TREE:
        return None
    structure = _dedupe(_convert_nodes(raw.get("structure")), set())
    if not structure:
        return None
    return {
        "doc_name": raw.get("doc_name") or "",
        "doc_title": raw.get("doc_title") or "",
        "structure": structure,
        "toc_source": source or "detected",
    }


def build_tree_flash(pdf_path: str, timeout_s: int = 30) -> dict | None:
    """Flash tree in `_walk_tree` shape, or None (no hierarchy / error / timeout).
    Never raises. Same detached-thread deadline pattern as ingest.build_tree."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        raw = ex.submit(_run_flash, str(pdf_path)).result(timeout=timeout_s)
        return to_walk_shape(raw)
    except _FTimeout:
        print(f"[tree_flash] exceeded {timeout_s}s — falling back")
        return None
    except Exception as e:  # scanned / encrypted / parser bug -> fall back
        print(f"[tree_flash] failed, falling back: {e!r}")
        return None
    finally:
        ex.shutdown(wait=False)
