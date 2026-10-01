"""GLOBAL / META-question answer path (Karpathy-wiki "global search").

Per-page keyword retrieval (FTS + trigram over `pages`) is built to find the
ONE page that answers a specific question. It cannot answer CORPUS-LEVEL / meta
questions — "summarize all the documents", "what do you cover", "list all SOPs",
"give me an overview", "what's in the knowledge base". Those need a view of the
WHOLE corpus, not a single page.

This module is that view. It builds a deterministic, grounded overview from data
the system already compiled at ingest — the document list + each doc's
`doc_wiki.summary` (falling back to its first compiled page) + the catalog — so
the answer is always traceable to real documents and NEVER hallucinated. It
bypasses both per-page retrieval and the scope gate.

Two public functions:
  - is_global_query(q)  -> bool   conservative meta-question detector
  - build_overview(...) -> dict   {answer, pages, doc_count}

Everything is FAIL-SOFT — any error returns a safe empty/partial result, never
raises.
"""
import re

from .db import get_conn

# Title cleanup — prefer the shared humanizer; fall back to a local cleanup.
try:
    from .okf import _humanize as _okf_humanize
except Exception:  # pragma: no cover - import guard
    _okf_humanize = None


# ── meta-question detection ──────────────────────────────────────────────────
# Tight allow-list of patterns that are CLEARLY about the whole corpus. Bias to
# precision: a specific content question ("how do I create a site", "what is the
# access code") must NOT match, so it falls through to normal retrieval.
_GLOBAL_PATTERNS = [
    # summarize/overview the whole corpus
    r"\bsummari[sz]e\s+(all|every|the\s+)?(docs?|documents?|sops?|runbooks?|everything|knowledge\s*base)\b",
    r"\bsummary\s+of\s+(all|every|the\s+)?(docs?|documents?|sops?|runbooks?|everything|knowledge\s*base)\b",
    r"\bsummari[sz]e\s+everything\b",
    # noun form ("summaries all documents") + bare "(all|every) documents/docs/sops"
    r"\bsummar(y|ies)\b.*\b(all|every|docs?|documents?|sops?|runbooks?|everything)\b",
    r"\b(all|every)\s+(of\s+)?(the\s+)?(docs?|documents?|sops?|runbooks?)\b",
    # list / what documents do you have
    r"\b(list|show|give\s+me)\s+(me\s+)?(all\s+(of\s+)?)?(the\s+)?(docs?|documents?|sops?|runbooks?)\b",
    r"\bwhat\s+(docs?|documents?|sops?|runbooks?)\s+(do\s+you\s+have|are\s+(there|loaded|available)|do\s+you\s+know)\b",
    r"\bwhat\s+(docs?|documents?|sops?|runbooks?)\b.*\b(have|loaded|available)\b",
    r"\bhow\s+many\s+(docs?|documents?|sops?|runbooks?)\b",
    # "what sops we have" (no auxiliary verb) / "which sops are there"
    r"^\s*(what|which)\s+(docs?|documents?|sops?|runbooks?)\s+(we|you|do\s+we|do\s+you|are\s+there)\b",

    # what do you cover / know / help with
    r"\bwhat\s+do\s+you\s+(cover|know|have)\b",
    r"\bwhat\s+can\s+you\s+(help|do)\b",
    r"\bwhat\s+(topics|areas|subjects)\s+do\s+you\s+(cover|know)\b",
    # overview / TOC / knowledge base / what's in here
    r"\boverview\b",
    r"\btable\s+of\s+contents\b",
    r"\bknowledge\s*base\b",
    r"\bwhat'?s?\s+(in\s+)?(here|the\s+(knowledge\s*base|corpus|library|brain))\b",
    r"\bwhat\s+is\s+(in\s+)?(here|the\s+(knowledge\s*base|corpus|library|brain))\b",
    # "what information / info / data / content do we|you have" — meta, no doc keyword
    r"\bwhat\s+(information|info|infos|data|content|knowledge)\b.*\b(do|does|we|you|i|have|got|available|there|cover)\b",
    r"\bwhat\s+(do|does)\s+(we|you|i)\s+have\b",
    r"\bwhat'?s?\s+available\b",
    # bare "give me a (detailed) summary/overview" with NO specific object after it
    # ('$' end-anchor → "summary of user creation" keeps falling through to retrieval)
    r"\b(give|show|provide|send)\s+(me\s+)?(a|an|the\s+)?(detailed|full|brief|quick|short|complete|high[- ]?level)?\s*(summary|overview|rundown|breakdown|recap|digest)\s*[.!?]*\s*$",
    r"^\s*(summari[sz]e|overview|recap|tl;?dr)\s*[.!?]*\s*$",
    # "tell me about everything / the docs / the knowledge base"
    r"\btell\s+me\s+(about\s+)?(everything|all\s+of\s+it|the\s+(docs?|documents?|sops?|runbooks?|knowledge\s*base|content|library))\b",
]
_GLOBAL_RE = [re.compile(p, re.IGNORECASE) for p in _GLOBAL_PATTERNS]

# Whole-message AREA listing ("list batch jobs runbooks", "what data management sops do
# we have"). Only global when the words name a real category — "show me the backup
# runbook" is a content question and must keep going to retrieval.
_AREA_RE = [re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(list|show)\s+(me\s+)?(all\s+)?(the\s+)?[\w&/,\- ]{1,40}?\s+(docs?|documents?|sops?|runbooks?)\s*[?.!]*\s*$",
    r"^\s*(what|which)\s+[\w&/,\- ]{1,40}?\s+(docs?|documents?|sops?|runbooks?)\s+(do\s+)?(we|you)\s+(have|cover)\b",
)]


def _categories() -> set:
    try:
        with get_conn() as conn:
            return {r["c"] for r in conn.execute(
                "SELECT DISTINCT COALESCE(NULLIF(category,''),'Uncategorized') AS c FROM docs").fetchall()}
    except Exception:
        return set()


def is_global_query(q: str) -> bool:
    """Conservative detector for corpus-level / meta questions. True only for
    questions clearly about the WHOLE corpus (summarize all, list all SOPs,
    overview, what do you cover, table of contents, knowledge base, ...). When
    unsure → False (falls through to normal retrieval). Never raises."""
    try:
        s = (q or "").strip().lower()
        if not s:
            return False
        if any(rx.search(s) for rx in _GLOBAL_RE):
            return True
        if any(rx.search(s) for rx in _AREA_RE):
            return _match_category(s, _categories()) is not None
        return False
    except Exception as e:  # pragma: no cover
        print(f"[global] is_global_query failed: {e!r}")
        return False


# ── title cleanup ────────────────────────────────────────────────────────────
_EXT_RE = re.compile(r"\.(pdf|png|jpe?g)$", re.IGNORECASE)
# a leading code like "SOP_IT_..._NNN_" — letters/digits/underscores ending in
# a numeric group, all before the human title.
_LEADCODE_RE = re.compile(r"^(?:[A-Za-z]+_)+\d+_")


def _humanize(name: str) -> str:
    """Readable title from a raw docs.name. Prefer okf._humanize; else strip the
    extension, drop a leading SOP_IT_..._NNN_ code, '_'→space, collapse space."""
    raw = (name or "").strip()
    if not raw:
        return "Untitled document"
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
    s = re.sub(r"\s+", " ", s).strip()
    return s or "Untitled document"


# ── summary helpers ──────────────────────────────────────────────────────────
_MD_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)


def _clean_snippet(md: str, limit: int = 280) -> str:
    """Turn raw markdown into a short plain-text summary sentence: drop headers
    and table pipes, collapse whitespace, truncate to ~limit chars."""
    s = md or ""
    s = _MD_HEADER_RE.sub("", s)
    s = s.replace("|", " ")
    s = re.sub(r"[`*_>#]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _first_page_summary(conn, doc_id) -> str:
    """Fallback summary = first compiled page's first ~280 chars."""
    try:
        row = conn.execute(
            "SELECT md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no LIMIT 1",
            (doc_id,),
        ).fetchone()
        if row and (row.get("md") or "").strip():
            return _clean_snippet(row["md"], 280)
    except Exception:
        pass
    return ""


def _first_page_coin(conn, doc_id):
    """Return (page_id, page_no, has_image) for a doc's FIRST page, or None."""
    try:
        row = conn.execute(
            "SELECT id, page_no, image_path FROM pages "
            "WHERE doc_id = %s ORDER BY page_no LIMIT 1",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        has_image = bool((row.get("image_path") or "").strip())
        return row["id"], row["page_no"], has_image
    except Exception:
        return None


# RBAC scope clause — single-tenant / super-admin (both None) sees everything.
_SCOPE_WHERE = (
    "status IN ('ready','ready_lite') "
    "AND (%(folders)s::bigint[] IS NULL OR folder_id = ANY(%(folders)s) "
    "     OR (folder_id IS NULL AND sector_id = ANY(%(sectors)s)))"
)

_MAX_COINS = 12


_FULL_LIST_MAX = 15     # above this, answer with a per-area digest instead of every doc
_PER_AREA = 5           # titles shown per area in the digest
_STOP = {"the", "and", "of", "for", "a", "an", "all", "my", "our", "list", "show", "me", "what",
         "which", "do", "we", "you", "have", "cover", "sop", "sops", "runbook", "runbooks",
         "doc", "docs", "document", "documents", "are", "there", "in", "&"}


def _words(s: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if w not in _STOP and len(w) > 1:
            out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)   # jobs→job
    return out


def _match_category(q: str | None, categories: set) -> str | None:
    """The single category the question names ("batch jobs" → "Batch Jobs"), else None."""
    qw = _words(q or "")
    if not qw:
        return None
    best, best_n = None, 0
    for c in categories:
        cw = _words(c)
        hit = len(cw & qw)
        if cw and hit and hit >= best_n and (hit == len(cw) or hit >= 2):
            if hit > best_n or (best and len(c) < len(best)):
                best, best_n = c, hit
    return best


def _compact_answer(docs: list) -> str:
    """Per-area digest for a big corpus: counts + a few titles per area."""
    by: dict = {}
    for d in docs:
        by.setdefault(d["category"], []).append(d["title"])
    areas = sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    lines = [f"I cover **{len(docs)} runbooks** in **{len(areas)} areas**:", ""]
    for cat, titles in areas:
        shown = " · ".join(titles[:_PER_AREA])
        more = f" · _+{len(titles) - _PER_AREA} more_" if len(titles) > _PER_AREA else ""
        lines.append(f"- **{cat}** ({len(titles)}) — {shown}{more}")
    example = areas[0][0] if areas else "Batch Jobs"
    lines += ["", f"To see a whole area, ask *\"list {example} runbooks\"* — or just ask your question."]
    return "\n".join(lines)


def build_overview(sectors=None, folders=None, q: str | None = None) -> dict:
    """Build a deterministic, grounded corpus overview for a meta-question.

    Returns {"answer": <markdown>, "pages": [coin...], "doc_count": int}. RBAC
    scoped: only docs the caller may see (sectors/folders both None = no filter).
    Each doc contributes a bullet (title · category · pages + summary) and one
    citation coin pointing at its first page. Fail-soft → empty-but-valid dict.
    """
    try:
        params = {"sectors": sectors, "folders": folders}
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT d.id, d.name, "
                "COALESCE(NULLIF(d.category,''), 'Uncategorized') AS category, "
                "COALESCE(d.page_count, 0) AS page_count, "
                "w.summary AS wiki_summary "
                "FROM docs d "
                "LEFT JOIN doc_wiki w ON w.doc_id = d.id "
                "WHERE " + _SCOPE_WHERE,
                params,
            ).fetchall()

            docs = []
            for r in rows:
                summary = (r.get("wiki_summary") or "").strip()
                if summary:
                    summary = _clean_snippet(summary, 280)
                else:
                    summary = _first_page_summary(conn, r["id"])
                # no compiled summary yet → show nothing rather than a placeholder
                docs.append({
                    "doc_id": r["id"],
                    "title": _humanize(r["name"]),
                    "category": r["category"] or "Uncategorized",
                    "page_count": int(r.get("page_count") or 0),
                    "summary": summary,
                })

            # stable, readable order: category then title
            docs.sort(key=lambda d: (d["category"].lower(), d["title"].lower()))

            # "list batch jobs runbooks" → only that area (word match on the category)
            area = _match_category(q, {d["category"] for d in docs})
            all_n, all_areas = len(docs), len({d["category"] for d in docs})
            if area:
                docs = [d for d in docs if d["category"] == area]
            compact = not area and len(docs) > _FULL_LIST_MAX

            # citation coins — one per doc → its FIRST page, capped
            pages = []
            for d in ([] if compact else docs):   # a 300-doc digest needs no coins
                if len(pages) >= _MAX_COINS:
                    break
                coin = _first_page_coin(conn, d["doc_id"])
                if not coin:
                    continue
                page_id, page_no, has_image = coin
                pages.append({
                    "page_id": page_id,
                    "page_no": page_no,
                    "doc_id": d["doc_id"],
                    "doc_name": d["title"],
                    "has_image": has_image,
                    "image_url": f"/api/pages/{page_id}",
                })

        # ── compose the markdown answer ──────────────────────────────────────
        n = len(docs)
        if n == 0:
            answer = ("No documents are loaded yet — upload an SOP to get "
                      "started.")
            return {"answer": answer, "pages": [], "doc_count": 0}

        if compact:
            return {"answer": _compact_answer(docs), "pages": pages, "doc_count": n}

        if area:
            lines = [f"**{area}** — {n} runbook{'s' if n != 1 else ''} "
                     f"(of {all_n} across {all_areas} areas):", ""]
        else:
            lines = [f"I cover **{n}** runbook{'s' if n != 1 else ''}:", ""]
        for d in docs:
            lines.append(f"- **{d['title']}** · {d['page_count']}p" if area
                         else f"- **{d['title']}** — {d['category']} · {d['page_count']}p")
            if d["summary"]:
                lines.append(f"  {d['summary']}")
        if area:
            answer = "\n".join(lines).strip()
            return {"answer": answer, "pages": pages, "doc_count": n}

        # optional trailing topics line from the catalog (fail-soft)
        try:
            from .catalog import build_catalog
            cat = build_catalog() or {}
            topics = []
            for s in (cat.get("systems") or [])[:6]:
                name = (s.get("name") or "").strip() if isinstance(s, dict) else ""
                if name:
                    topics.append(name)
            if not topics:
                seen = set()
                for d in docs:
                    c = d["category"]
                    if c and c != "Uncategorized" and c not in seen:
                        seen.add(c)
                        topics.append(c)
                    if len(topics) >= 6:
                        break
            if topics:
                lines.append("")
                lines.append("Topics: " + " · ".join(topics))
        except Exception:
            pass

        answer = "\n".join(lines).strip()
        return {"answer": answer, "pages": pages, "doc_count": n}
    except Exception as e:
        print(f"[global] build_overview failed: {e!r}")
        return {"answer": "", "pages": [], "doc_count": 0}
