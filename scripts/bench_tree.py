"""Benchmark the LLM tree builder (vendored PageIndex) vs PageIndex Flash.

    PYTHONPATH=.:vendor_pageindex python scripts/bench_tree.py data/processed --no-llm
    PYTHONPATH=.:vendor_pageindex python scripts/bench_tree.py a.pdf b.pdf

Args are PDF files or directories (scanned non-recursively for *.pdf). Copies
that differ only by the 8-hex storage prefix (`1bad1ae4__X.pdf`) are benched once.
No DB needed. The LLM column needs the OpenRouter key + INGEST_MODEL in env.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "vendor_pageindex")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app import tree_flash  # noqa: E402

_PREFIX = re.compile(r"^[0-9a-f]{8}__")


def _collect(args: list[str]) -> list[Path]:
    seen, out = set(), []
    for a in args:
        p = Path(a)
        files = sorted(p.glob("*.pdf")) if p.is_dir() else [p]
        for f in files:
            key = _PREFIX.sub("", f.name).lower()
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def _count(nodes) -> tuple[int, int]:
    """(node count, max depth) of a structure list."""
    if isinstance(nodes, dict):
        nodes = nodes.get("structure") or [nodes]
    n, depth = 0, 0
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        kn, kd = _count(node.get("nodes") or [])
        n += 1 + kn
        depth = max(depth, 1 + kd)
    return n, depth


def _pages(pdf: Path) -> int:
    try:
        import pymupdf
        with pymupdf.open(pdf) as d:
            return len(d)
    except Exception:
        try:
            import pypdfium2 as pdfium
            d = pdfium.PdfDocument(str(pdf))
            n = len(d)
            d.close()
            return n
        except Exception:
            return -1


def _bench_flash(pdf: Path) -> dict:
    t = time.perf_counter()
    try:
        raw = tree_flash._run_flash(str(pdf))
        err = ""
    except Exception as e:
        raw, err = None, f"{type(e).__name__}: {e}"[:60]
    ms = (time.perf_counter() - t) * 1000
    shaped = tree_flash.to_walk_shape(raw) if raw else None
    n, d = _count(shaped) if shaped else (0, 0)
    src = (raw or {}).get("toc_source") or ("error" if err else "?")
    titles = [x["title"] for x in (shaped or {}).get("structure", [])][:4]
    return {"ms": ms, "src": src, "nodes": n, "depth": d, "used": shaped is not None,
            "err": err, "titles": titles}


def _bench_llm(pdf: Path, model: str) -> dict:
    from pageindex import page_index  # vendored LLM builder
    t = time.perf_counter()
    try:
        tree = page_index(str(pdf), model=model)
        n, _ = _count(tree)
    except Exception as e:
        print(f"  llm failed on {pdf.name}: {e!r}")
        n = -1
    return {"ms": (time.perf_counter() - t) * 1000, "nodes": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--no-llm", action="store_true", help="only run Flash")
    ap.add_argument("--titles", action="store_true", help="print top-level Flash titles")
    a = ap.parse_args()

    model = ""
    if not a.no_llm:
        import os
        model = os.getenv("INGEST_MODEL", "")
        if not model:
            from app.config import INGEST_MODEL as model  # noqa: N811

    pdfs = _collect(a.paths)
    hdr = f"{'document':52} {'pg':>3} {'flash ms':>8} {'toc_source':>10} {'nodes':>5} {'dep':>3} {'used':>4}"
    if not a.no_llm:
        hdr += f" {'llm ms':>8} {'llm n':>5}"
    print(hdr)
    print("-" * len(hdr))
    used = 0
    by_src: dict[str, int] = {}
    for pdf in pdfs:
        f = _bench_flash(pdf)
        used += f["used"]
        by_src[f["src"]] = by_src.get(f["src"], 0) + 1
        name = _PREFIX.sub("", pdf.name)[:52]
        line = (f"{name:52} {_pages(pdf):>3} {f['ms']:>8.0f} {f['src']:>10} "
                f"{f['nodes']:>5} {f['depth']:>3} {'yes' if f['used'] else 'no':>4}")
        if not a.no_llm:
            l = _bench_llm(pdf, model)
            line += f" {l['ms']:>8.0f} {l['nodes']:>5}"
        print(line)
        if f["err"]:
            print(f"    ! {f['err']}")
        if a.titles and f["titles"]:
            print("    > " + " | ".join(t[:40] for t in f["titles"]))
    print("-" * len(hdr))
    print(f"{len(pdfs)} PDFs · Flash hierarchy used: {used} · fell back: {len(pdfs) - used} · "
          f"toc_source: {by_src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
