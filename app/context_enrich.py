"""Contextual Retrieval enrichment (Anthropic-style, vectorless).

At ingest, generate a 1-2 sentence context blurb per page (what document, what
section/topic, what the page covers) and fold it into the FTS source so isolated
pages are not ambiguous. This is the main recall lever for the vectorless
Postgres full-text search.

The page's `context` text is stored in `pages.context` and contributes to the
GENERATED `pages.tsv` column (see db.py SCHEMA) so it is searchable via FTS.

Uses the OpenRouter OpenAI-compatible endpoint directly, mirroring vision.py /
doc_facts.py. Cheap (1 small call per page). Fully fail-soft: any error -> "".
"""
import os

from .config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    INGEST_MODEL,
    CHAT_MODEL,
)

# Flag (in-module to avoid colliding with other agents editing config.py).
CONTEXT_ENRICH_ENABLED = os.getenv("CONTEXT_ENRICH_ENABLED", "1") == "1"

# trims
_OUTLINE_MAX = 1500
_PAGE_MAX = 1500
_OUT_MAX = 240

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI

    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _model() -> str:
    # The OpenAI client hits OpenRouter directly, so the model id must NOT carry
    # the litellm "openrouter/" prefix (INGEST_MODEL default does). Strip it;
    # fall back to CHAT_MODEL which is already in OpenRouter form.
    m = INGEST_MODEL or ""
    if m.startswith("openrouter/"):
        m = m[len("openrouter/"):]
    return m or CHAT_MODEL


_SYS = (
    "You situate a single page within its document so it can be retrieved out of "
    "context. Given the document title, its outline, and this page's text, write "
    "1-2 SHORT sentences saying what document this is, what section/topic the page "
    "belongs to, and what this page covers. Keep exact codes/names/values if they "
    "identify the topic. Preserve the page's language (English or Burmese). "
    "Output ONLY the sentences, no preamble, no labels, no quotes."
)


def enrich_page(
    doc_title: str, outline: str, page_text: str, lang: str = ""
) -> str:
    """Return a 1-2 sentence context blurb situating this page in its document.

    Fail-soft: returns "" on disabled flag, missing client, empty page, or ANY
    exception. Never raises — must never block ingest.
    """
    if not CONTEXT_ENRICH_ENABLED or not _client:
        return ""
    page_text = (page_text or "").strip()
    if not page_text:
        return ""
    try:
        outline = (outline or "").strip()[:_OUTLINE_MAX]
        body = page_text[:_PAGE_MAX]
        user = (
            f"DOCUMENT TITLE: {doc_title or '(untitled)'}\n\n"
            f"DOCUMENT OUTLINE:\n{outline or '(none)'}\n\n"
            f"PAGE TEXT:\n{body}"
        )
        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user},
            ],
            max_tokens=120,
            temperature=0.0,
        )
        out = (resp.choices[0].message.content or "").strip()
        # strip stray wrapping quotes the model sometimes adds
        if len(out) >= 2 and out[0] in "\"'" and out[-1] == out[0]:
            out = out[1:-1].strip()
        return out[:_OUT_MAX].strip()
    except Exception as e:
        print(f"[context_enrich] enrich failed: {e!r}")
        return ""
