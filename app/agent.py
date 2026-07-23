"""DocSensei — single agent brain (Scout-style), two speeds.

QUICK (default): the page text is already transcribed at ingest, so we feed the
FULL page text directly and answer in ONE streamed LLM call — fast + detailed,
no slow per-chat vision, no tool round-trips.

DEEP: feed page IMAGES + give the Agno agent its toolbelt so it can navigate
outlines / read other pages for hard or ambiguous questions.

Bilingual EN/Burmese. Persistent session + taught facts.
"""
import re
import threading
from contextlib import contextmanager

from .config import (
    OPENROUTER_API_KEY, CHAT_MODEL, DEEP_MODEL, DATABASE_URL,
    OPENROUTER_BASE_URL, LLM_MAX_CONCURRENCY,
)
from .memory import memory_facts
from .retrieve import search_brain

# Global cap on simultaneous user-facing LLM calls. Excess callers BLOCK here
# (backpressure) instead of all hitting OpenRouter at once. Gated at the public
# entry points (stream_reply / answer) only — NOT the _quick_stream/_deep_text
# primitives, so the deep->quick fallback never re-acquires and deadlocks.
_llm_sema = threading.BoundedSemaphore(LLM_MAX_CONCURRENCY) if LLM_MAX_CONCURRENCY > 0 else None


@contextmanager
def _llm_slot():
    if _llm_sema is None:
        yield
        return
    _llm_sema.acquire()
    try:
        yield
    finally:
        _llm_sema.release()


_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

# shared answer rules (detail + per-page inline citations)
_RULES_HEAD = (
    "You are DocSensei, the single expert brain for company SOPs and policies.\n"
    "LANGUAGE: ALWAYS answer in the SAME language as the user's QUESTION, even if "
    "the source is in another language (translate the source). English question -> "
    "English answer; Burmese question -> Burmese answer. Never mirror the document "
    "language over the question language.\n"
    "DETAIL: Extract the COMPLETE answer from the SOURCE PAGES — every step, field "
    "code, transaction code, path, value, threshold, button and table entry, "
    "EXACTLY as written. Do NOT summarise away detail. If it is a procedure, give "
    "every numbered step in order. Quote exact on-screen values, menu/screen names, "
    "table/field codes and button labels verbatim (e.g. 'put 506 in the General "
    "Parameter Management screen', 'set Access = 2'). If the SOP gives MORE THAN ONE "
    "way / screen / role to do it, list ALL of them, each labelled. Render any table "
    "from the source as a markdown table and keep every row.\n"
    "STRUCTURE: Start with a one-line summary, then the numbered steps, then any "
    "notes / warnings / contacts. Use markdown (numbered lists, **bold** for screen "
    "names and codes, tables) so the steps are easy to follow.\n"
)
_RULES_CITE = (
    "CITATIONS: After each step or factual claim, cite the source it came from inline "
    "as [N], using the bracketed SOURCE NUMBER shown in that block's header (the [1], "
    "[2], … label — NOT the page number). If a fact spans two sources cite both, "
    "e.g. [1][3].\n"
)
_RULES_GROUND = (
    "GROUNDING: Use ONLY the SOURCE PAGES and KNOWN FACTS below. Never invent steps. "
    "If the answer is not present, say so plainly and ask the user to upload the "
    "relevant SOP.\n"
    "PRECEDENCE: KNOWN FACTS are corrections/updates the team taught you and OVERRIDE "
    "the SOURCE PAGES whenever they disagree. If a KNOWN FACT contradicts a page, "
    "follow the KNOWN FACT, give that as the answer, and briefly note the page is "
    "outdated (e.g. 'updated — the SOP still lists X'). Never let a stale page win "
    "over a taught fact.\n"
)
_RULES_PAGES = (
    "At the very end output one line exactly: PAGES: <comma-separated SOURCE NUMBERS "
    "[N] you used> (or 'PAGES: none').\n"
)
_RULES_NOCITE = (
    "CITATIONS: Do NOT cite sources. Write the answer as plain prose with NO bracketed "
    "[N] markers, NO page references, and NO 'PAGES:' line. Just give the answer.\n"
)
_RULES_FOLLOWUPS = (
    "Output ONE line exactly: FOLLOWUPS: <three follow-up questions the user might "
    "ask next, separated by ' | '>. Each MUST be a COMPLETE, natural question ending "
    "with '?' (NOT a topic or phrase like 'User Creation' — write 'How do I create a "
    "user?'). They MUST continue the SAME topic/procedure the user just asked about — "
    "deeper steps, error handling, prerequisites, or verification of THAT procedure. "
    "NEVER suggest questions about other unrelated runbooks that merely appear in the "
    "source pages (after an order question, do NOT suggest user-account questions). "
    "Ground them in the cited document's content. Write them in the SAME language "
    "as the QUESTION (English question -> English; Burmese question -> Burmese). "
    "(or 'FOLLOWUPS: none')."
)


def chat_rules(cite: bool = True) -> str:
    """Assemble the answer system prompt. When `cite` is False the model is told
    NOT to emit [N] markers or a PAGES line (citations disabled per deployment).
    A configured persona (identity/voice) is prepended — it governs tone only,
    never grounding."""
    persona = ""
    try:
        from . import appcfg, persona as persona_mod
        persona = persona_mod.persona_block(appcfg.get_persona())
    except Exception:
        persona = ""
    body = persona + _RULES_HEAD + (_RULES_CITE if cite else _RULES_NOCITE) + _RULES_GROUND
    if cite:
        body += _RULES_PAGES
    return body + _RULES_FOLLOWUPS


# back-compat constant (full citations-on rules) for any other importer
RULES = chat_rules(True)

_PAGES_RE = re.compile(r"PAGES:\s*([0-9, ]+|none)", re.IGNORECASE)
_FOLLOWUPS_RE = re.compile(r"FOLLOWUPS:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_CITE_RE = re.compile(r"\[(\d{1,3})\]")


def parse_followups(text: str) -> tuple[str, list[str]]:
    """Pull the trailing FOLLOWUPS: line, strip it. Returns (clean, [q1,q2,q3])."""
    outs: list[str] = []
    m = _FOLLOWUPS_RE.search(text or "")
    if m and m.group(1).strip().lower() != "none":
        outs = [s.strip(" -•\t") for s in m.group(1).split("|")]
        outs = [s for s in outs if len(s) > 4][:3]
    clean = _FOLLOWUPS_RE.sub("", text or "").strip()
    return clean, outs


def parse_pages(text: str, fallback: list[int]) -> tuple[str, list[int]]:
    """Pull the trailing PAGES: line, strip it. Returns (clean, source_numbers) —
    the numbers are 1-based indices into the source list shown to the model, NOT
    page_ids. Callers map them to real page_ids via map_cited()."""
    nums: list[int] = []
    m = _PAGES_RE.search(text or "")
    if m and m.group(1).lower().strip() != "none":
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
    clean = _PAGES_RE.sub("", text or "").strip()
    return clean, (nums or fallback)


def strip_citations(text: str) -> str:
    """Remove ALL citation artefacts from an answer (used when citations are
    disabled for a deployment): the trailing PAGES: line + every inline [N] marker,
    then tidy the doubled spaces the removals leave behind."""
    t = _PAGES_RE.sub("", text or "")
    t = _CITE_RE.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;:])", r"\1", t)     # space left before punctuation
    return t.strip()


def map_cited(nums: list[int], pages: list[dict]) -> list[int]:
    """Map the model's 1-based source numbers ([1],[2]…) to real page_ids, in
    order, de-duplicated. Out-of-range markers are dropped. This is the fix for
    the old page_no/page_id confusion: the model only ever echoes small source
    indices, and we resolve them against the exact list we showed it."""
    out, seen = [], set()
    for n in nums:
        if 1 <= n <= len(pages):
            pid = pages[n - 1]["page_id"]
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def render_cited(text: str, pages: list[dict]) -> str:
    """Rewrite the model's [N] source markers into human page numbers [p.X] for
    display. Markers out of range are left untouched."""
    def _sub(m):
        n = int(m.group(1))
        if 1 <= n <= len(pages):
            return f"[p.{pages[n - 1]['page_no']}]"
        return m.group(0)
    return _CITE_RE.sub(_sub, text or "")


def _brain_facts(query: str) -> list[dict]:
    """Relevance-ranked taught facts for the prompt (was: dump ALL active facts).
    Uses the unified brain so facts surface by how well they match the question,
    while still guaranteeing the top active facts when the brain is small (so
    behavior is unchanged with only a handful of facts). Fail-soft -> all facts."""
    try:
        items = search_brain(query)
        facts = [it for it in items if it.get("type") == "fact"]
        if facts:
            return [{"id": it["id"], "text": it.get("text_full") or it.get("snippet") or ""}
                    for it in facts]
    except Exception as e:
        print(f"[agent] brain fact lookup failed, dumping all facts: {e!r}")
    return memory_facts()


def _render_playbook(conn, doc_id, label: str = "", with_deps: bool = True) -> str:
    """Render one doc's compiled playbook as a compact text block. '' if none."""
    pb = conn.execute(
        "SELECT goal, who, prerequisites, steps, verify, if_fails, escalate "
        "FROM doc_playbook WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    if not pb or not (pb.get("steps") or []):
        return ""
    L = [label] if label else []
    if pb.get("goal"):
        L.append(f"Goal: {pb['goal']}")
    if pb.get("who"):
        L.append(f"Who: {pb['who']}")
    if pb.get("prerequisites"):
        L.append("Prerequisites:")
        L += [f"  - {x}" for x in pb["prerequisites"]]
    if pb.get("steps"):
        L.append("Steps:")
        for s in pb["steps"]:
            a = (s.get("action") or "").strip()
            d = (s.get("detail") or "").strip()
            L.append(f"  {s.get('n','')}. {a}" + (f" — {d}" if d else ""))
    if pb.get("verify"):
        L.append("Done when:")
        L += [f"  - {x}" for x in pb["verify"]]
    if pb.get("if_fails"):
        L.append("If it fails:")
        L += [f"  - {x}" for x in pb["if_fails"]]
    if pb.get("escalate"):
        L.append(f"Escalate: {pb['escalate']}")
    if with_deps:
        try:
            from .dependencies import dependencies_for
            deps = dependencies_for(doc_id)
            if deps:
                L.append("Complete these prerequisite procedures FIRST:")
                L += [f"  - {d['doc_name']}" + (f" — {d['reason']}" if d.get('reason') else "")
                      for d in deps]
        except Exception as e:
            print(f"[agent] dependency lookup failed (non-fatal): {e!r}")
    return "\n".join(L)


def _playbook_block(seed_pages: list[dict]) -> str:
    """Pre-compiled playbook(s) for the cited doc(s). Single doc -> one playbook;
    when the question's pages span several compiled docs and MULTIDOC_SYNTH is on,
    include the top few and ask the model to MERGE them into one coherent procedure
    (Phase 7 multi-doc synthesis). Fail-soft -> ''."""
    try:
        import os
        from collections import Counter
        from .db import get_conn
        ids = [p.get("doc_id") for p in seed_pages if p.get("doc_id")]
        if not ids:
            return ""
        ranked = [d for d, _ in Counter(ids).most_common()]
        synth = os.getenv("MULTIDOC_SYNTH", "1") == "1"
        cap = int(os.getenv("MULTIDOC_MAX", "2")) if synth else 1

        with get_conn() as conn:
            blocks = []
            for did in ranked:
                if len(blocks) >= cap:
                    break
                with_doc = conn.execute(
                    "SELECT name FROM docs WHERE id = %s", (did,)
                ).fetchone()
                label = f"— from {with_doc['name']} —" if (with_doc and cap > 1) else ""
                b = _render_playbook(conn, did, label=label)
                if b:
                    blocks.append(b)
        if not blocks:
            return ""
        if len(blocks) == 1:
            head = ("PLAYBOOK (a pre-compiled guide for the most relevant document — use "
                    "it as the backbone of your answer WHEN it matches the question; still "
                    "cite the source pages, and KNOWN FACTS override it on conflict):")
            return head + "\n" + blocks[0] + "\n"
        head = ("PLAYBOOKS (the question spans several documents — MERGE these pre-compiled "
                "guides into ONE coherent end-to-end procedure, in the right order, without "
                "repeating shared steps; cite the source pages; KNOWN FACTS override):")
        return head + "\n\n" + "\n\n".join(blocks) + "\n"
    except Exception as e:
        print(f"[agent] playbook block failed (non-fatal): {e!r}")
        return ""


def _context(query: str, seed_pages: list[dict]) -> str:
    parts = []
    facts = _brain_facts(query)
    if facts:
        parts.append("KNOWN FACTS (taught by the team — these OVERRIDE the source "
                     "pages on any conflict):")
        parts += [f"  - {f['text']}" for f in facts]
        parts.append("")
    try:
        from .config import USE_PLAYBOOK_IN_CHAT
    except Exception:
        USE_PLAYBOOK_IN_CHAT = True
    if USE_PLAYBOOK_IN_CHAT:
        pb = _playbook_block(seed_pages)
        if pb:
            parts.append(pb)
    parts.append("SOURCE PAGES:")
    for i, p in enumerate(seed_pages, 1):
        body = (p.get("text_full") or p.get("snippet") or "").strip()
        parts.append(
            f"\n━━ [{i}] Doc: {p['doc_name']} · page {p['page_no']} ━━\n{body}"
        )
    parts.append(f"\n\nUSER QUESTION: {query}")
    return "\n".join(parts)


# ---------------- QUICK: direct streamed completion ----------------
def _is_transient_llm_error(e: Exception) -> bool:
    """5xx / rate-limit / connection blip from the LLM provider — worth one retry."""
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(code, int) and (code >= 500 or code == 429):
        return True
    name = type(e).__name__.lower()
    return any(k in name for k in ("ratelimit", "apiconnection", "internalserver", "timeout", "serviceunavailable"))


def _chat_stream_with_retry(**kwargs):
    """_client.chat.completions.create with one retry on a transient provider error.
    The request stream is established here (under saturation a 5xx fires at request
    time, before any token) — retrying once removes most of those failures."""
    import time as _t
    try:
        return _client.chat.completions.create(**kwargs)
    except Exception as e:
        if not _is_transient_llm_error(e):
            raise
        print(f"[agent] transient LLM error ({e!r}) — retrying once")
        _t.sleep(1.2)
        return _client.chat.completions.create(**kwargs)


_AUDIENCE_RULE = {
    "simple": (
        "AUDIENCE: a non-technical END USER. Answer in plain language, short numbered "
        "steps, minimal jargon (briefly explain any code/screen name you must use). "
        "Keep it to what they need to DO; omit deep internals unless essential."
    ),
    "expert": (
        "AUDIENCE: IT support staff. Give the full procedure with every exact code, "
        "path, SQL, screen and value; do not simplify away detail."
    ),
}


def _quick_stream(query: str, seed_pages: list[dict], history: list[dict] | None = None,
                  meter: dict | None = None, audience: str = "expert"):
    """Yield answer token deltas (single LLM call, full page text, no images).

    `history` = prior turns [{role, text}] so follow-ups ("more details", "why?")
    are understood as continuations of the conversation, not standalone questions.
    `meter` = optional dict filled with {model, tok_in, tok_out} for telemetry.
    `audience` = 'simple' (end user) | 'expert' (IT support) tunes verbosity.
    """
    if meter is not None:
        meter["model"] = CHAT_MODEL
    if not _client:
        yield "LLM key not configured."
        return
    from . import appcfg
    messages = [{"role": "system", "content": chat_rules(appcfg.citations_enabled())}]
    arule = _AUDIENCE_RULE.get(audience or "expert")
    if arule:
        messages.append({"role": "system", "content": arule})
    for h in (history or [])[-6:]:                       # last 3 Q/A pairs
        txt = (h.get("text") or "").strip()
        if not txt:
            continue
        role = "assistant" if h.get("role") == "bot" else "user"
        messages.append({"role": role, "content": txt[:1200]})
    messages.append({"role": "user", "content": _context(query, seed_pages)})
    stream = _chat_stream_with_retry(
        model=CHAT_MODEL, messages=messages,
        max_tokens=8000, temperature=0.2, stream=True,
        stream_options={"include_usage": True},          # final chunk carries usage
    )
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage and meter is not None:
            meter["tok_in"] = getattr(usage, "prompt_tokens", 0) or 0
            meter["tok_out"] = getattr(usage, "completion_tokens", 0) or 0
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


# ---------------- DEEP: Agno agent (images + tools), chunk-streamed ----------
def _deep_text(query: str, seed_pages: list[dict], session_id: str | None,
               history: list[dict] | None = None, audience: str = "expert",
               meter: dict | None = None) -> str:
    from agno.agent import Agent
    from agno.media import Image
    from agno.models.openrouter import OpenRouter
    from .brain_tools import (
        list_documents, open_outline, read_page, remember_fact, recall_facts,
    )

    model = OpenRouter(id=DEEP_MODEL, api_key=OPENROUTER_API_KEY, max_tokens=8000)
    # NO PostgresAgentStorage: Agno would persist every turn's full page-text
    # context + images into agent_sessions.memory, ballooning one session to
    # ~18MB. Follow-up context comes from our own messages history (below)
    # instead, which keeps agent_sessions from growing at all.
    from . import appcfg
    agent = Agent(
        model=model,
        instructions=[chat_rules(appcfg.citations_enabled()), _AUDIENCE_RULE.get(audience or "expert", ""),
                      "Candidate pages are also shown as IMAGES — read them "
                      "for screenshots / diagrams / Burmese text not in the text layer. "
                      "Use list_documents/open_outline/read_page to verify or pull more."],
        tools=[list_documents, open_outline, read_page, remember_fact, recall_facts],
        markdown=True,
    )
    ctx = _context(query, seed_pages)
    if history:
        prior = "\n".join(
            f"{'Assistant' if h.get('role') == 'bot' else 'User'}: {(h.get('text') or '').strip()[:800]}"
            for h in history[-4:] if (h.get('text') or '').strip())
        if prior:
            ctx = f"PRIOR CONVERSATION (for follow-up context):\n{prior}\n\n{ctx}"
    images = [Image(filepath=p["image_path"]) for p in seed_pages if p.get("image_path")]
    try:
        resp = agent.run(ctx, images=images)
        # deep-mode token metering: Agno aggregates per-call metrics on the
        # response; without this, deep answers report 0 tokens / $0 and the
        # cost dashboards undercount. Fail-soft — metering never breaks answers.
        if meter is not None:
            try:
                m = getattr(resp, "metrics", None) or {}
                meter["tok_in"] = sum(m.get("input_tokens") or []) or sum(m.get("prompt_tokens") or [])
                meter["tok_out"] = sum(m.get("output_tokens") or []) or sum(m.get("completion_tokens") or [])
            except Exception as e:
                print(f"[agent] deep meter failed (non-fatal): {e!r}")
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:
        print(f"[agent] deep run failed, fallback quick text: {e!r}")
        return "".join(_quick_stream(query, seed_pages))


# ---------------- public: streaming generator ----------------
def stream_reply(query: str, seed_pages: list[dict], mode: str = "quick",
                 session_id: str | None = None, history: list[dict] | None = None,
                 meter: dict | None = None, audience: str = "expert"):
    """Generator of answer token strings. Caller accumulates + parses PAGES.
    `meter` (optional) is filled with {model, tok_in, tok_out} for telemetry.
    `audience` = 'simple'|'expert' tunes answer depth (Phase 6 role-based)."""
    with _llm_slot():   # hold one LLM permit for the whole generation/stream
        if mode == "deep":
            if meter is not None:
                meter["model"] = DEEP_MODEL
            # deep mode gets follow-up context from our own messages history (no
            # Agno persistent session memory — see _deep_text)
            text = _deep_text(query, seed_pages, session_id, history, audience, meter=meter)
            for i in range(0, len(text), 48):   # chunk so the client still streams
                yield text[i:i + 48]
        else:
            yield from _quick_stream(query, seed_pages, history, meter=meter, audience=audience)


# ---------------- public: non-streaming (legacy /ask) ----------------
def answer(query: str, seed_pages: list[dict], mode: str = "quick",
           session_id: str | None = None) -> dict:
    with _llm_slot():
        if mode == "deep":
            text = _deep_text(query, seed_pages, session_id)
        else:
            text = "".join(_quick_stream(query, seed_pages))
    clean, nums = parse_pages(text, [])
    clean = render_cited(clean, seed_pages)
    page_ids = map_cited(nums, seed_pages) or [p["page_id"] for p in seed_pages]
    return {"text": clean, "page_ids": page_ids}
