"""Vision pre-read: at ingest, transcribe each page image (incl. screenshot text
that is NOT in the PDF text layer) so retrieval can find image-only content.

Uses the OpenRouter OpenAI-compatible endpoint directly (lighter than a full
agent). flash-lite, cheap. Concurrency-capped to respect rate limits.
"""
import base64
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from .config import OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL

# toggle: set VISION_PREREAD=0 in .env to skip (faster/cheaper ingest)
VISION_PREREAD = os.getenv("VISION_PREREAD", "1") == "1"
_WORKERS = int(os.getenv("VISION_WORKERS", "6"))
# hard per-page timeout so one stalled OpenRouter call can't hang the whole
# (synchronous) upload request forever.
_TIMEOUT = float(os.getenv("VISION_TIMEOUT", "90"))

_PROMPT = (
    "This is one page of a company SOP/policy document. Transcribe ONLY the text "
    "that is actually visible on the page, EXACTLY as written, including text "
    "inside screenshots: UI labels, menu names, button text, field names, table "
    "cells, error messages, IP addresses, paths and numbers. "
    "Do NOT infer, summarise, explain, translate, or add anything that is not "
    "literally printed on the page. If a region is unreadable, write [unreadable]. "
    "After the transcription, add a block starting 'VISUAL CUES:' listing any visual "
    "instruction the screenshots convey that the text alone misses — which button / "
    "field / row is highlighted, circled, boxed, or pointed at by an arrow; what the "
    "user is meant to click; the active tab or screen; the order arrows imply. One "
    "bullet per cue; write 'none' if there are no such cues. "
    "Then add exactly one line starting 'CAPTION:' that briefly says what the "
    "page/screenshot shows. Output plain text only."
)

_client = (
    OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=_TIMEOUT,
        max_retries=2,
    )
    if OPENROUTER_API_KEY
    else None
)


def _describe(image_path: str) -> str:
    if not _client:
        return ""
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            max_tokens=1600,
            temperature=0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[vision] page describe failed {image_path}: {e!r}")
        return ""


def preread_pages(pages: list[dict], on_page=None, should_cancel=None) -> None:
    """Enrich each page dict's 'text' in place with a vision transcription.
    Combined text = original text layer + vision-read screenshot text.

    on_page(done:int) is called after each page finishes (any order) so the
    caller can report live ingest progress. If should_cancel() returns True the
    remaining pages are cancelled and a Cancelled is raised."""
    from .ingest_control import Cancelled
    if not VISION_PREREAD or not _client:
        if on_page:
            on_page(len(pages))
        return
    results: dict[int, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futs = {ex.submit(_describe, p["image_path"]): i for i, p in enumerate(pages)}
        try:
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:  # bounded by client timeout/retries
                    print(f"[vision] page {i} failed: {e!r}")
                    results[i] = ""
                done += 1
                if on_page:
                    on_page(done)            # may raise Cancelled — let it propagate
                if should_cancel and should_cancel():
                    raise Cancelled("vision pre-read cancelled")
        except Cancelled:
            for f in futs:                   # stop the not-yet-started ones
                f.cancel()
            raise
    for i, p in enumerate(pages):
        vt = results.get(i, "")
        if vt:
            p["vision_text"] = vt
            layer = (p.get("text_layer") or "").strip()
            p["text"] = (layer + "\n[VISION]\n" + vt).strip() if layer else vt
