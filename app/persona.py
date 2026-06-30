"""Agent PERSONA layer — a generated identity/voice, applied on top of grounding.

A persona is an identity (name, role, tone, length, audience, behaviour rules,
greeting) that the agent ADOPTS when it speaks. It is GENERATED once from the
whole document corpus (`generate_from_knowledge`) so the voice fits what this
particular knowledge base is actually about, and an admin can edit + version it.

CRITICAL boundary: the persona governs HOW the agent talks (tone + identity),
never WHAT it knows. It is PREPENDED to the existing answer RULES as a
system-prompt prefix and explicitly tells the model that the grounding is
unchanged — answer strictly from the SOURCE PAGES + KNOWN FACTS, keep the
inline citations, and never invent a fact to fit the persona. Turning the
persona off (or generation failing) leaves the normal grounded behaviour intact.

Everything here is FAIL-SOFT: any error returns a safe value, never raises. The
generated dict is RETURNED, not saved — the caller persists it via
`appcfg.save_persona`.
"""
import json
import re

from .db import get_conn
from .catalog import build_catalog
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
)

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


# allowed enum values for the controlled persona fields
_TONES = {"formal", "warm", "casual"}
_LENGTHS = {"brief", "full", "exhaustive"}
_AUDIENCES = {"end-user", "it", "mixed"}

# a sensible generic persona when there's nothing to learn from (no docs / no client)
_DEFAULT_RULES = [
    "Answer strictly from the source pages and known facts; never invent.",
    "Quote exact codes, screen names, menu paths and values verbatim.",
    "Give the full step-by-step procedure when the question is a how-to.",
    "Cite the page for every claim and keep the citations.",
    "If it's not in the runbooks, say so kindly rather than guessing.",
]


def _generic_default() -> dict:
    """A safe, corpus-agnostic persona used when generation can't run. Enabled,
    warm, full-length, mixed audience. Never raises."""
    return {
        "enabled": True,
        "name": "Aria",
        "role": "company runbook & IT assistant",
        "covers": [],
        "tone": "warm",
        "length": "full",
        "audience": "mixed",
        "rules": list(_DEFAULT_RULES),
        "greeting": "Hi, I'm Aria — how can I help with your runbooks?",
        "version": 1,
        "generated_from": "0 documents",
    }


# ── title humanizer (prefer the shared one, fall back to a tiny local clean) ──
try:
    from .okf import _humanize as _okf_humanize
except Exception:  # pragma: no cover - import guard
    _okf_humanize = None

_EXT_RE = re.compile(r"\.(pdf|png|jpe?g)$", re.IGNORECASE)
_LEADCODE_RE = re.compile(r"^(?:[A-Za-z]+_)+\d+_")


def _humanize(name: str) -> str:
    """Readable title from a raw docs.name. Prefer okf._humanize; else strip the
    extension + leading SOP code, '_'→space, collapse whitespace. Fail-soft."""
    raw = (name or "").strip()
    if not raw:
        return ""
    if _okf_humanize is not None:
        try:
            out = (_okf_humanize(raw) or "").strip()
            if out:
                return out
        except Exception:
            pass
    s = _EXT_RE.sub("", raw)
    s = _LEADCODE_RE.sub("", s)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_json(raw: str):
    """Robustly parse the model's JSON: strip ```json fences, tolerate surrounding
    prose, fall back to the first {...} (or [...]) block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            m = re.search(r"\[.*\]", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# ── coercion helpers ─────────────────────────────────────────────────────────
def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v).strip()
    return str(v).strip()


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        # tolerate a comma / pipe / newline separated string
        parts = re.split(r"[\n,|]+", v)
    elif isinstance(v, (list, tuple)):
        parts = v
    else:
        parts = [v]
    out = []
    for p in parts:
        s = str(p).strip().lstrip("-*• ").strip()
        if s:
            out.append(s)
    return out


def _pick(v, allowed: set, default: str) -> str:
    s = _as_str(v).lower()
    return s if s in allowed else default


# ── 1) build the system-prompt prefix from a persona dict ────────────────────
def persona_block(p: dict) -> str:
    """Build a system-prompt PREFIX from a persona dict. Returns "" when the
    persona is disabled / missing. When enabled, produces ONE compact block
    (ending with a newline) that sets the IDENTITY + VOICE only and explicitly
    reaffirms that the grounding is unchanged. Empty fields are skipped
    gracefully. Fail-soft → "" on any error."""
    try:
        if not p or not p.get("enabled"):
            return ""

        name = _as_str(p.get("name")) or "the assistant"
        role = _as_str(p.get("role"))
        covers = _as_list(p.get("covers"))
        tone = _as_str(p.get("tone"))
        length = _as_str(p.get("length"))
        audience = _as_str(p.get("audience"))
        rules = _as_list(p.get("rules"))

        parts = []
        # identity sentence
        ident = f"PERSONA: You are {name}"
        if role:
            ident += f", {role}"
        ident += "."
        parts.append(ident)

        if covers:
            parts.append(f"You help with: {', '.join(covers)}.")

        # voice sentence — only include the clauses we actually have
        voice = []
        if tone:
            voice.append(f"a {tone} tone")
        if length:
            voice.append(f"give {length}-length answers")
        if audience:
            voice.append(f"for a {audience} audience")
        if voice:
            parts.append("Speak in " + ", ".join(voice) + ".")

        if rules:
            parts.append("Always: " + "; ".join(rules) + ".")

        parts.append(
            f"Stay in character as {name}. This persona governs TONE and "
            "IDENTITY ONLY — it NEVER changes the grounding: answer strictly "
            "from the SOURCE PAGES + KNOWN FACTS, keep the citations, and never "
            "invent facts to fit the persona."
        )
        return " ".join(parts).strip() + "\n"
    except Exception as e:  # pragma: no cover
        print(f"[persona] persona_block failed (non-fatal): {e!r}")
        return ""


# ── corpus signal gathering ──────────────────────────────────────────────────
_CTX_CAP = 6000
_SUMMARY_CAP = 200


def _gather_context() -> tuple[str, int]:
    """Build a compact corpus-signal string + the ready-doc count. Reads ready
    docs (humanized title · category · wiki summary), top entities by mention
    count, and the catalog categories + systems. Fail-soft → ("", 0)."""
    doc_count = 0
    lines: list[str] = []
    try:
        with get_conn() as conn:
            docs = conn.execute(
                "SELECT d.id, d.name, "
                "COALESCE(NULLIF(d.category,''), 'General') AS category, "
                "w.summary AS summary "
                "FROM docs d LEFT JOIN doc_wiki w ON w.doc_id = d.id "
                "WHERE d.status IN ('ready','ready_lite') "
                "ORDER BY d.category, d.name"
            ).fetchall()
            doc_count = len(docs)

            ents = conn.execute(
                "SELECT e.name, count(DISTINCT m.doc_id) AS docs "
                "FROM entities e JOIN entity_mention m ON m.entity_id = e.id "
                "GROUP BY e.name ORDER BY docs DESC, e.name LIMIT 25"
            ).fetchall()

        if docs:
            lines.append("DOCUMENTS:")
            for d in docs:
                title = _humanize(d["name"]) or "Untitled"
                cat = d.get("category") or "General"
                summ = (d.get("summary") or "").strip()
                if summ:
                    summ = re.sub(r"\s+", " ", summ)[:_SUMMARY_CAP]
                bit = f"- {title} [{cat}]"
                if summ:
                    bit += f": {summ}"
                lines.append(bit)

        if ents:
            names = [e["name"] for e in ents if (e.get("name") or "").strip()]
            if names:
                lines.append("")
                lines.append("KEY THINGS MENTIONED: " + ", ".join(names))
    except Exception as e:
        print(f"[persona] gather docs/entities failed (non-fatal): {e!r}")

    # catalog categories + systems (fail-soft, additive)
    try:
        cat = build_catalog() or {}
        cats = [g.get("name") for g in (cat.get("groups") or [])
                if isinstance(g, dict) and (g.get("name") or "").strip()]
        systems = [s.get("name") for s in (cat.get("systems") or [])
                   if isinstance(s, dict) and (s.get("name") or "").strip()]
        if cats:
            lines.append("")
            lines.append("CATEGORIES: " + ", ".join(cats))
        if systems:
            lines.append("SYSTEMS: " + ", ".join(systems))
    except Exception as e:
        print(f"[persona] gather catalog failed (non-fatal): {e!r}")

    ctx = "\n".join(lines).strip()
    if len(ctx) > _CTX_CAP:
        ctx = ctx[:_CTX_CAP] + " …[truncated]"
    return ctx, doc_count


_GEN_SYS = (
    "You design an assistant persona for a knowledge agent that answers ONLY "
    "from these company documents. Based on the corpus below, propose a "
    "helpful, professional persona. Reply STRICT JSON with keys: "
    "name (short, e.g. 'Aria'), "
    "role (one phrase describing what it is, grounded in the actual doc topics), "
    "covers (3-6 short topic chips from the real content), "
    "tone (one of formal|warm|casual), "
    "length (one of brief|full|exhaustive — prefer 'full' for procedural SOPs), "
    "audience (one of end-user|it|mixed), "
    "rules (4-6 short behaviour bullets appropriate to these docs, e.g. quoting "
    "exact codes/screens, giving full steps, citing the page, refusing "
    "off-topic kindly), "
    "greeting (one friendly sentence). "
    "Do NOT invent topics not in the corpus."
)


# ── 2) generate a persona from the whole corpus ──────────────────────────────
def generate_from_knowledge() -> dict:
    """Scan the whole corpus and PROPOSE a persona dict (does NOT save it — the
    caller persists via appcfg.save_persona). One temperature-0.3 LLM call over a
    compact corpus-signal context. Falls back to a sensible generic persona when
    there are no docs or no LLM client. Never raises."""
    try:
        ctx, doc_count = _gather_context()

        if not _client or doc_count == 0 or not ctx:
            d = _generic_default()
            d["generated_from"] = f"{doc_count} documents"
            return d

        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _GEN_SYS},
                {"role": "user", "content": ctx},
            ],
            max_tokens=1200,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, dict):
            print("[persona] generate: parsed JSON not an object — using default")
            d = _generic_default()
            d["generated_from"] = f"{doc_count} documents"
            return d

        default = _generic_default()
        out = {
            "enabled": True,
            "name": _as_str(data.get("name")) or default["name"],
            "role": _as_str(data.get("role")) or default["role"],
            "covers": _as_list(data.get("covers")),
            "tone": _pick(data.get("tone"), _TONES, "warm"),
            "length": _pick(data.get("length"), _LENGTHS, "full"),
            "audience": _pick(data.get("audience"), _AUDIENCES, "mixed"),
            "rules": _as_list(data.get("rules")) or list(_DEFAULT_RULES),
            "greeting": _as_str(data.get("greeting")) or default["greeting"],
            "version": 1,
            "generated_from": f"{doc_count} documents",
        }
        return out
    except Exception as e:
        print(f"[persona] generate_from_knowledge failed (non-fatal): {e!r}")
        d = _generic_default()
        try:
            _, n = _gather_context()
            d["generated_from"] = f"{n} documents"
        except Exception:
            pass
        return d
