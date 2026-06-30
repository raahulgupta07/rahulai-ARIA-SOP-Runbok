"""Per-embedded-image detailed extraction (SOP screenshots).

Native-text SOPs carry their procedure text in the PDF text layer for free, but
the *screenshots* (which screen, which button to click) are images. This module
extracts EACH embedded image and asks the vision model to explain it in detail —
screen / what's shown / the highlighted element + its label / the action — so the
answer can tell the user exactly what to click, per image, not as a vague page
blob.

Flag DOC_IMAGES_ENABLED (default 0). Fail-soft. Saves PNGs next to the page
images (PAGES_DIR/<slug>/img_<page>_<idx>.png), rows in doc_image. Tiny images
(logos/icons) are skipped by a min-size filter.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import fitz

from .config import PAGES_DIR
from .db import get_conn

DOC_IMAGES_ENABLED: bool = os.getenv("DOC_IMAGES_ENABLED", "0") == "1"
_MIN_W = int(os.getenv("DOC_IMAGE_MIN_W", "120"))   # skip icons/logos below this
_MIN_H = int(os.getenv("DOC_IMAGE_MIN_H", "90"))
_MAX_PER_DOC = int(os.getenv("DOC_IMAGE_MAX", "60"))

_PROMPT = (
    "This is a SCREENSHOT from a company IT/SOP runbook. Explain it precisely for a "
    "user following the procedure. Reply ONLY JSON: "
    "{\"screen\":\"<the screen/window/page name>\", "
    "\"shows\":\"<what is on screen: a form, list, dialog, menu...>\", "
    "\"element\":\"<the highlighted/circled/arrowed control + its exact label, or '' >\", "
    "\"action\":\"<what the user should do here, e.g. 'click the Search button' or "
    "'enter End Date = today', or '' >\"}. Read button/field/menu labels verbatim. "
    "If it is a decorative logo/banner, return all empty strings."
)


def _client():
    from .vision import _client as vc, CHAT_MODEL  # reuse the configured vision client
    return vc, CHAT_MODEL


def describe_image(image_path: str) -> dict:
    """One detailed vision verdict for a single screenshot. Fail-soft -> {}."""
    vc, model = _client()
    if not vc:
        return {}
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = vc.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            max_tokens=300, temperature=0)
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.I).strip()
        d = json.loads(raw)
        return {k: (d.get(k) or "").strip() for k in ("screen", "shows", "element", "action")}
    except Exception as e:
        print(f"[doc_images] describe failed {image_path}: {e!r}")
        return {}


def extract_and_describe(doc_id: int, pdf_path, doc_slug: str) -> int:
    """Extract every meaningful embedded image from the PDF, describe each in
    detail, store doc_image rows. Returns count written. Fail-soft."""
    if not DOC_IMAGES_ENABLED:
        return 0
    out_dir = Path(PAGES_DIR) / doc_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"[doc_images] open {pdf_path} failed: {e!r}")
        return 0
    with get_conn() as c:
        c.execute("DELETE FROM doc_image WHERE doc_id = %s", (doc_id,))   # rerun-safe
        for pno in range(len(doc)):
            page = doc[pno]
            seen_xref = set()
            for idx, info in enumerate(page.get_images(full=True)):
                if written >= _MAX_PER_DOC:
                    break
                xref = info[0]
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.width < _MIN_W or pix.height < _MIN_H:
                        continue                       # logo/icon — skip
                    if pix.n >= 5:                     # CMYK/alpha -> RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    img_path = out_dir / f"img_{pno + 1}_{idx}.png"
                    pix.save(str(img_path))
                except Exception as e:
                    print(f"[doc_images] extract p{pno+1}#{idx} failed: {e!r}")
                    continue
                d = describe_image(str(img_path))
                if not d or not any(d.values()):       # decorative -> drop the file + skip
                    try:
                        img_path.unlink()
                    except Exception:
                        pass
                    continue
                c.execute(
                    "INSERT INTO doc_image (doc_id, page_no, idx, image_path, screen, "
                    "shows, element, action) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (doc_id, pno + 1, idx, str(img_path), d.get("screen"),
                     d.get("shows"), d.get("element"), d.get("action")))
                written += 1
    # fold a compact per-page screenshot summary into the page text so retrieval +
    # compile_wiki pick it up (so the answer can reference "on this screen ...").
    _merge_into_pages(doc_id)
    return written


def _merge_into_pages(doc_id: int):
    """Append 'SCREENSHOTS:' notes to each page's stored text so FTS + the compiled
    wiki include the per-image explanations."""
    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT page_no, screen, shows, element, action FROM doc_image "
                "WHERE doc_id = %s ORDER BY page_no, idx", (doc_id,)).fetchall()
            by_page: dict[int, list[str]] = {}
            for r in rows:
                bits = [b for b in (r["screen"], r["shows"], r["element"], r["action"]) if b]
                if bits:
                    by_page.setdefault(r["page_no"], []).append(" — ".join(bits))
            for pno, notes in by_page.items():
                block = "\n\nSCREENSHOTS:\n" + "\n".join(f"* {n}" for n in notes)
                c.execute(
                    "UPDATE pages SET text = coalesce(text,'') || %s "
                    "WHERE doc_id = %s AND page_no = %s "
                    "AND position('SCREENSHOTS:' in coalesce(text,'')) = 0",
                    (block, doc_id, pno))
    except Exception as e:
        print(f"[doc_images] merge failed: {e!r}")


def _resolve_pdf(doc_id: int, name: str):
    """Find a doc's source PDF wherever it landed (uploads/inbox/processed).
    Stored filenames slugify spaces/&/etc to underscores, so match on an
    alphanumeric-only normalisation of both names. Returns a Path or None."""
    import glob
    from .config import PROCESSED_DIR, INBOX_DIR, UPLOADS_DIR
    def _n(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower())
    key = _n(Path(name).stem)
    for d in (UPLOADS_DIR, INBOX_DIR, PROCESSED_DIR):
        for p in glob.glob(os.path.join(str(d), "*.pdf")):
            if key and key in _n(os.path.basename(p)):
                return Path(p)
    return None


def backfill_all() -> dict:
    """Extract + explain images for every ready doc using its stored PDF (no
    re-ingest needed). Run once after enabling DOC_IMAGES_ENABLED."""
    with get_conn() as c:
        docs = c.execute("SELECT id, name FROM docs WHERE status='ready' ORDER BY id").fetchall()
    total, done = 0, 0
    for d in docs:
        pdf = _resolve_pdf(d["id"], d["name"])
        if not pdf:
            continue
        try:
            total += extract_and_describe(d["id"], pdf, f"doc_{d['id']}")
            done += 1
        except Exception as e:
            print(f"[doc_images] backfill doc {d['id']} failed: {e!r}")
    return {"docs": done, "images": total}


def images_for(doc_id: int) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, page_no, idx, screen, shows, element, action "
            "FROM doc_image WHERE doc_id = %s ORDER BY page_no, idx", (doc_id,)).fetchall()
    return [dict(r) for r in rows]
