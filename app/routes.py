import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Header, Request, Query, Body
from fastapi.responses import FileResponse, StreamingResponse, Response, RedirectResponse
from pydantic import BaseModel

from .config import UPLOADS_DIR, ROOT
from .db import get_conn
from .ingest import ingest_file
from . import storage
from .retrieve import search_pages, search_brain, agentic_search, AGENTIC_RETRIEVE, DEFAULT_K
from .agent import answer as brain_answer, stream_reply, parse_pages, map_cited, render_cited, parse_followups, strip_citations


def _cite_policy(clean: str, cited: list) -> tuple[str, list]:
    """Honour the per-deployment citation toggle (appcfg.features.citations).
    When OFF: strip any [N]/PAGES artefacts from the answer + drop the source
    pages so the UI shows no citation coins. When ON: pass through unchanged."""
    try:
        if not appcfg.citations_enabled():
            return strip_citations(clean), []
    except Exception:
        pass
    return clean, cited
from .answer_quality import cited_page_ids, resolve_query
from . import scope
from . import rbac
from .memory import (
    add_memory, list_memory, set_status, pending_count, touch_used_facts,
    update_memory,
)
from .learn import maybe_learn
from .auth.deps import (current_user, require_admin, require_superadmin, current_principal,
                        require_content_manager, require_knowledge_manager)
from . import embed as embed_mod
from . import okf as okf_mod
from .version import version_info
from . import notify
from . import conversations as convo
from . import audit as audit_mod
from . import digest as digest_mod
from . import dream as dream_mod
from . import dashboard as dashboard_mod
from . import keywords as keywords_mod
from . import analytics as analytics_mod
from . import usage_rollup
from . import appcfg

router = APIRouter()

# every data endpoint requires a logged-in user (JWT); page images stay public
require_key = current_user


def _friendly_doc(name: str) -> str:
    """Humanize a raw SOP filename for step labels (mirrors frontend parseDocName)."""
    base = re.sub(r"\.(pdf|png|jpe?g)$", "", name or "", flags=re.I)
    m = re.match(r"^(SOP[_-].*?)_([A-Z0-9]+)_(\d{2,4})_(.+)$", base)
    title = m.group(4) if m else base
    if not m:
        m2 = re.search(r"_(\d{2,4})_(.+)$", base)
        if m2:
            title = m2.group(2)
    return re.sub(r"[_]+", " ", title).strip()[:34] or base


class AskRequest(BaseModel):
    q: str
    k: int | None = None
    conversation_id: int | None = None
    mode: str = "quick"  # quick | deep | auto (auto → router picks quick/deep)
    session_id: str | None = None  # legacy, ignored


class MemoryRequest(BaseModel):
    value: str
    key: str | None = None


@router.get("/version")
def version():
    """Public: app version + changelog (drives the About / update panel)."""
    return version_info()


class FeedbackRequest(BaseModel):
    conversation_id: int | None = None
    vote: str
    text: str | None = None
    answer: str | None = None
    # richer feedback (optional, backward-compatible)
    question: str | None = None
    page_ids: list[int] | None = None
    note: str | None = None
    correction: str | None = None
    served_qa_id: int | None = None   # set when the answer came from the Q&A cache


def _is_downvote(vote: str | None) -> bool:
    return (vote or "").strip().lower() in ("down", "-1")


def _is_upvote(vote: str | None) -> bool:
    return (vote or "").strip().lower() in ("up", "1", "+1")


@router.post("/feedback")
def feedback(req: FeedbackRequest, user: dict = Depends(current_principal)):
    """Record a thumbs up/down on an answer (fail-soft). A downvote that carries
    a correction is also written as a PENDING memory fact (admin approves it in
    Brain → Facts; once active it OVERRIDES the docs on conflict)."""
    learned_id = None
    fb_id = None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "INSERT INTO message_feedback "
                "(user_id, conversation_id, vote, note, answer, "
                " question, page_ids, correction) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    user.get("id"), req.conversation_id, req.vote,
                    req.note or req.text, req.answer,
                    req.question,
                    json.dumps(req.page_ids) if req.page_ids is not None else None,
                    req.correction,
                ),
            ).fetchone()
            fb_id = row["id"] if row else None
    except Exception:
        pass

    # downvote + a correction → queue a pending fact for review + link it back
    correction = (req.correction or "").strip()
    if _is_downvote(req.vote) and correction:
        try:
            q = (req.question or "").strip()
            fact = f"Q: {q} -> {correction}" if q else correction
            learned_id = add_memory(
                fact, source="feedback",
                created_by=user.get("email"), status="pending",
                origin_question=q or None,
            )
            if fb_id is not None and learned_id is not None:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE message_feedback SET learned_memory_id = %s "
                        "WHERE id = %s",
                        (learned_id, fb_id),
                    )
            notify.emit("memory", "Correction from feedback (pending review)",
                        correction[:80], "info")
        except Exception as e:
            print(f"[feedback] learn skipped: {e!r}")

    # Close the cache loop: a 👎 on a cache-SERVED answer demotes that pair out of
    # active serve (→ pending) so it stops being handed out, and an admin can rewrite
    # or re-approve it in the Q&A queue. One downvote is enough — a served pair is a
    # curated answer, so a thumbs-down means it's wrong/unhelpful as-is.
    if _is_downvote(req.vote) and req.served_qa_id and user.get("role") == "admin":
        try:
            from . import qa as qa_mod
            if qa_mod.set_qa_status(req.served_qa_id, "pending"):
                audit_mod.log(user, "qa.demote_on_downvote", "qa", req.served_qa_id)
                notify.emit("memory", "Cached Q&A demoted after a downvote",
                            (req.question or "")[:80], "info")
        except Exception as e:
            print(f"[feedback] qa demote skipped: {e!r}")

    # Phase 2: harvest a Q&A pair FREE from an UPVOTED, SOURCED answer (zero LLM —
    # reuse the answer the user already liked). Only when it cited pages (grounded)
    # and we have both the question and the answer. Lands per chat governance
    # (default: pending → admin approves in the Q&A queue). Dedup in qa.add_qa.
    qa_id = None
    from .config import AUTO_QA_HARVEST
    if AUTO_QA_HARVEST and _is_upvote(req.vote):
        q = (req.question or "").strip()
        a = (req.answer or "").strip()
        pids = req.page_ids or []
        if len(q) >= 6 and len(a) >= 12 and pids:
            try:
                from . import qa as qa_mod, governance
                status = governance.decide_status(
                    "chat", None, is_admin=(user.get("role") == "admin"))
                qa_id = qa_mod.add_qa(
                    q, a, source="chat", status=status, confidence=None,
                    doc_id=None, page_ids=pids, created_by=user.get("email"),
                    origin="From chat (upvoted)")
                if qa_id:
                    notify.emit("memory", "Q&A harvested from chat"
                                + ("" if status == "active" else " (pending review)"),
                                q[:80], "info")
            except Exception as e:
                print(f"[feedback] qa harvest skipped: {e!r}")
    return {"ok": True, "learned_id": learned_id, "qa_id": qa_id}


@router.get("/stats/public")
def stats_public():
    """Public corpus counts (safe pre-login; never raises — login must not break)."""
    try:
        with get_conn() as conn:
            s = conn.execute(
                "SELECT (SELECT count(*) FROM docs) AS docs, "
                "(SELECT count(*) FROM pages) AS pages, "
                "(SELECT count(*) FROM nodes) AS sections, "
                "(SELECT count(*) FROM memory) AS facts"
            ).fetchone()
        return {
            "docs": int(s["docs"]),
            "pages": int(s["pages"]),
            "sections": int(s["sections"]),
            "facts": int(s["facts"]),
            "date": datetime.date.today().isoformat(),
        }
    except Exception:
        return {
            "docs": 0, "pages": 0, "sections": 0, "facts": 0,
            "date": datetime.date.today().isoformat(),
        }


# curated fallbacks — shown only when the corpus has no served Q&A yet (fresh install)
_STARTER_FALLBACK = [
    {"cat": "SETUP", "q": "How do I create a new site in Gold Central?"},
    {"cat": "USERS", "q": "How to disable a user account?"},
    {"cat": "BATCH", "q": "Night batch job ကျသွားရင် ဘာလုပ်ရမလဲ?"},
    {"cat": "DISCOVER", "q": "Where is the refund approval flow?"},
]


@router.get("/suggestions")
def suggestions(lang: str = "en"):
    """Home hero starter chips — zero LLM at request time.

    Primary source: the LLM-curated bilingual `starter_chips` table (rebuilt by a
    refresh job). lang='my' returns Burmese (falls back to EN per chip). If the
    table is empty (fresh install / never refreshed), fall through to the live
    corpus-derived logic below."""
    lang = "my" if (lang or "").lower().startswith("my") else "en"
    try:
        from . import starter_chips as _sc
        curated = _sc.read(lang=lang, limit=4)
        if curated:
            return {"suggestions": curated}
    except Exception:
        pass
    return _suggestions_live()


def _suggestions_live():
    """Live corpus-derived chips (zero LLM). Fallback when the curated table is empty.

    COVERAGE, not popularity: each of the 4 chips comes from a DIFFERENT document
    (and a different category where possible), so the chips represent the whole
    corpus instead of clustering on the few most-cited docs. The candidate pool is
    shuffled each load, so over repeated visits every document rotates through.
    Sources, in priority: proven answered Q&A (best question per doc) → doc-section
    titles for docs not yet covered → curated fallbacks. Public-safe; never raises."""
    import random

    def _norm(q: str) -> str:
        return re.sub(r"\s+", " ", (q or "").strip().lower())

    def _doc_topic(name: str) -> str:
        """Clean an SOP filename into a human topic.
        '92909c72__SOP_IT_CMHL_AMS_GLDCENTRAL_002_User Creation.pdf' -> 'User Creation'."""
        t = name or ""
        t = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", t)          # drop extension
        t = re.sub(r"^[0-9a-f]{6,}__", "", t)               # drop storage hash prefix
        t = re.sub(r"^SOP[_\s]+(?:[A-Z]+[_\s]+){0,6}\d+[_\s]+", "", t)  # drop SOP_..._NNN_
        t = re.sub(r"[_]+", " ", t).strip()
        return re.sub(r"\s+", " ", t)

    # empty corpus (fresh install / wiped) -> NO chips at all (don't show curated
    # fallbacks that look like real data when there are no documents).
    try:
        with get_conn() as conn:
            n = conn.execute("SELECT count(*) AS n FROM docs WHERE status='ready'").fetchone()
        if not (n or {}).get("n", 0):
            return {"suggestions": []}
    except Exception:
        pass

    # candidate pool: at most ONE entry per document (its best/most-cited question)
    pool: list[dict] = []
    seen_docs: set = set()
    try:
        with get_conn() as conn:
            # best proven question per document
            for r in conn.execute(
                "SELECT q.question AS q, q.doc_id AS doc_id, "
                "COALESCE(NULLIF(d.category,''), 'DOCS') AS cat "
                "FROM qa_pairs q LEFT JOIN docs d ON d.id = q.doc_id "
                "WHERE q.status='active' AND length(q.question) BETWEEN 8 AND 90 "
                # skip generic doc-mine boilerplate — these read as filler chips.
                "AND q.question !~* '(this sop|this document|this runbook|this procedure|purpose of)' "
                "ORDER BY q.cited_count DESC, q.last_cited_at DESC NULLS LAST, q.created_at DESC "
                "LIMIT 200"
            ).fetchall():
                key = r["doc_id"] if r["doc_id"] is not None else _norm(r["q"])
                if key in seen_docs:
                    continue
                seen_docs.add(key)
                pool.append({"q": r["q"], "cat": r["cat"], "doc_id": r["doc_id"]})
            # broaden: docs with no Q&A yet -> derive a chip from the DOCUMENT NAME.
            # (SOP section titles are generic boilerplate — Preface/Scope/Dos — so they
            #  read as unrelated to the doc; the filename is the real topic.)
            for r in conn.execute(
                "SELECT id AS doc_id, name, "
                "COALESCE(NULLIF(category,''), 'DOCS') AS cat "
                "FROM docs WHERE status='ready' ORDER BY id"
            ).fetchall():
                if r["doc_id"] in seen_docs:
                    continue
                seen_docs.add(r["doc_id"])
                topic = _doc_topic(r["name"])
                if 3 <= len(topic) <= 60:
                    pool.append({"q": f"What is the procedure for {topic}?", "cat": r["cat"], "doc_id": r["doc_id"]})
    except Exception:
        pass

    # rotate which docs surface across visits (each chip is still a distinct doc)
    random.shuffle(pool)

    out: list[dict] = []
    seen_q: set[str] = set()
    used_docs: set = set()
    used_cats: set = set()

    def _take(it: dict, require_new_cat: bool) -> None:
        if len(out) >= 4:
            return
        k = _norm(it["q"])
        if not k or k in seen_q:
            return
        if it["doc_id"] is not None and it["doc_id"] in used_docs:
            return
        if require_new_cat and it["cat"] in used_cats:
            return
        seen_q.add(k)
        if it["doc_id"] is not None:
            used_docs.add(it["doc_id"])
        used_cats.add(it["cat"])
        out.append({"cat": (it["cat"] or "DOCS").upper()[:10], "q": it["q"].strip()})

    # pass 1: distinct document AND distinct category (max spread)
    for it in pool:
        _take(it, require_new_cat=True)
    # pass 2: relax category — still distinct documents
    for it in pool:
        _take(it, require_new_cat=False)

    # top up with curated fallbacks (fresh install / sparse corpus)
    for f in _STARTER_FALLBACK:
        if len(out) >= 4:
            break
        k = _norm(f["q"])
        if k in seen_q:
            continue
        seen_q.add(k)
        out.append({"cat": (f["cat"] or "DOCS").upper()[:10], "q": f["q"]})

    return {"suggestions": out[:4]}


@router.post("/suggestions/click")
def suggestions_click(req: dict):
    """Record a starter-chip click (bandit reward signal). Public, fail-soft."""
    try:
        cid = int(req.get("id"))
    except (TypeError, ValueError):
        return {"ok": False}
    from . import starter_chips as _sc
    lang = "my" if str(req.get("lang", "")).lower().startswith("my") else "en"
    _sc.log_click(cid, lang)
    return {"ok": True}


@router.post("/suggestions/refresh", dependencies=[Depends(require_admin)])
def suggestions_refresh():
    """Rebuild the LLM-curated bilingual starter chips (one LLM call). Admin-only."""
    from . import starter_chips as _sc
    n = _sc.refresh()
    return {"ok": True, "chips": n}


@router.get("/notifications", dependencies=[Depends(require_key)])
def notifications(filter: str = "all"):
    return notify.list_notifications(filter)


@router.post("/notifications/read-all", dependencies=[Depends(require_key)])
def read_all():
    notify.mark_all_read()
    return {"ok": True}


def _pages_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.id AS page_id, p.doc_id AS doc_id, d.name AS doc_name, p.page_no, "
            "coalesce(p.image_path,'') <> '' AS has_image "
            "FROM pages p JOIN docs d ON d.id = p.doc_id "
            "WHERE p.id = ANY(%s)",
            (ids,),
        ).fetchall()
    by_id = {r["page_id"]: r for r in rows}
    out = []
    for i in ids:  # preserve agent's citation order
        r = by_id.get(i)
        if r:
            # OKF-imported pages are markdown-only (no rendered image) -> null
            # image_url so the UI shows a text-only source instead of a broken img
            out.append(
                {
                    "page_id": r["page_id"],
                    "doc_id": r["doc_id"],
                    "doc_name": r["doc_name"],
                    "page_no": r["page_no"],
                    "has_image": r["has_image"],
                    "image_url": f"/api/pages/{r['page_id']}" if r["has_image"] else None,
                }
            )
    return out


# follow-up cues → a short/anaphoric question that continues the prior turn
_FOLLOWUP_RE = re.compile(
    r"\b(more|detail|details|elaborate|expand|continue|again|also|above|previous|"
    r"earlier|explain|further|that|this|it|those|them|why|how about|what about)\b",
    re.I,
)


def _is_followup(q: str) -> bool:
    qs = (q or "").strip()
    return len(qs.split()) <= 6 or bool(_FOLLOWUP_RE.search(qs))


def _search_query(q: str, history: list[dict]) -> str:
    """For a follow-up, fold in the last user question so retrieval stays on-topic
    (e.g. 'provide more details' after 'what is CAR' → searches CAR pages)."""
    if not history or not _is_followup(q):
        return q
    prior = [m["text"] for m in history if m.get("role") == "user" and m.get("text")]
    return f"{prior[-1]}\n{q}" if prior else q


@router.post("/ask")
def ask(req: AskRequest, user: dict = Depends(current_principal)):
    """Ask (English or Burmese). Persisted into the user's conversation; the brain
    navigates the SOP trees, reads page images, and returns the answer + sources."""
    # resolve / create the conversation (owned by this user)
    if req.conversation_id:
        conv = convo.owned(req.conversation_id, user["id"])
        if not conv:
            raise HTTPException(status_code=404, detail="conversation not found")
    else:
        conv = convo.create(user["id"])
    conv_id = conv["id"]
    first_turn = convo.message_count(conv_id) == 0
    _sectors, _folders = rbac.scope_filter(user)   # row-level: (None,None)=all

    # GLOBAL / meta intent ("summarize all docs", "what do you cover", "list SOPs"):
    # answer from the doc list + per-doc summaries (deterministic, grounded), BEFORE
    # per-page retrieval + the scope gate — those refuse corpus-level questions.
    try:
        from . import global_answer
        if global_answer.is_global_query(req.q):
            ov = global_answer.build_overview(sectors=_sectors, folders=_folders)
            ans = ov.get("answer") or "No documents are loaded yet."
            cited = ov.get("pages") or []
            convo.add_message(conv_id, "user", req.q, [])
            convo.add_message(conv_id, "bot", ans, cited)
            title = convo.autotitle(conv_id, req.q) if first_turn else None
            return {"answer": ans, "pages": cited, "conversation_id": conv_id,
                    "title": title, "global": True}
    except Exception as e:
        print(f"[global] ask overview skipped: {e!r}")

    # Phase 4: serve a cached bank answer for a near-identical, non-follow-up question.
    # On a first turn there's no history, so a standalone question can't be a
    # follow-up — serve regardless of demonstratives ("this/that") that otherwise
    # trip _is_followup and silently disable the cache.
    from .config import AUTO_QA_SERVE_ENABLED, AUTO_QA_SERVE_MIN_SIM, AUTO_QA_SERVE_MIN_LEN
    if AUTO_QA_SERVE_ENABLED and req.mode != "deep" and (first_turn or not _is_followup(req.q)):
        try:
            from . import qa as qa_mod
            hit = qa_mod.serve_match(req.q, AUTO_QA_SERVE_MIN_SIM, AUTO_QA_SERVE_MIN_LEN,
                                     sectors=_sectors, folders=_folders)
        except Exception:
            hit = None
        if hit:
            _t0 = time.monotonic()
            ans = (hit.get("answer") or "").strip()
            cited = _pages_by_ids(hit.get("page_ids") or [])
            ans, cited = _cite_policy(ans, cited)
            convo.add_message(conv_id, "user", req.q, [])
            bot_id = convo.add_message(conv_id, "bot", ans, cited)
            qa_mod.bump_served(hit["id"])
            title = convo.autotitle(conv_id, req.q) if first_turn else None
            try:
                analytics_mod.record_metric(
                    message_id=bot_id, conversation_id=conv_id, user_id=user.get("id"),
                    model="qa-bank", mode="cache", ms_total=int((time.monotonic() - _t0) * 1000),
                    cited_n=len(cited), blind=(len(cited) == 0), cache_hit=True)
            except Exception:
                pass
            return {"answer": ans, "pages": cited, "conversation_id": conv_id,
                    "title": title, "served_from_bank": True,
                    "served_qa_id": hit["id"],
                    "followups": qa_mod.sibling_questions(hit["id"])}

    seed = search_pages(req.q, k=req.k or DEFAULT_K, sectors=_sectors, folders=_folders)
    _ok_scope, _refusal = scope.in_scope(req.q, lambda _q: seed)
    if not _ok_scope:
        # second chance: rephrase the (possibly broken-English) question into
        # runbook wording and re-search before refusing
        _rec_pages, _rec_q, _ = scope.try_recover(
            req.q, lambda s: search_pages(s, k=req.k or DEFAULT_K,
                                          sectors=_sectors, folders=_folders),
            sectors=_sectors, folders=_folders)
        if _rec_pages:
            seed, _ok_scope = _rec_pages, True
    if not _ok_scope:
        convo.add_message(conv_id, "user", req.q, [])
        convo.add_message(conv_id, "bot", _refusal, [])
        if first_turn:
            convo.autotitle(conv_id, req.q)
        return {"answer": _refusal, "pages": [], "conversation_id": conv_id}
    if not seed:
        answer = "No documents uploaded yet. Please upload SOPs/policies first."
        convo.add_message(conv_id, "user", req.q, [])
        convo.add_message(conv_id, "bot", answer, [])
        if first_turn:
            convo.autotitle(conv_id, req.q)
        return {"answer": answer, "pages": [], "conversation_id": conv_id}

    try:
        result = brain_answer(req.q, seed, mode=req.mode, session_id=str(conv_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"agent error: {e}")

    cited = _pages_by_ids(result["page_ids"]) or _pages_by_ids(
        [p["page_id"] for p in seed]
    )
    _text, cited = _cite_policy(result["text"], cited)
    convo.add_message(conv_id, "user", req.q, [])
    convo.add_message(conv_id, "bot", _text, cited)
    title = None
    if first_turn:
        title = convo.autotitle(conv_id, req.q)
    return {
        "answer": _text, "pages": cited,
        "conversation_id": conv_id, "title": title,
    }


@router.post("/ask/stream")
def ask_stream(req: AskRequest, user: dict = Depends(current_principal)):
    """Streamed answer (NDJSON lines): meta -> token* -> done. Persists + titles."""
    if req.conversation_id:
        conv = convo.owned(req.conversation_id, user["id"])
        if not conv:
            raise HTTPException(status_code=404, detail="conversation not found")
    else:
        conv = convo.create(user["id"])
    conv_id = conv["id"]
    first_turn = convo.message_count(conv_id) == 0
    history = convo.messages(conv_id)   # prior turns (before this one) → context for follow-ups
    _sectors, _folders = rbac.scope_filter(user)   # row-level: (None,None)=all

    # role-based answer depth (Phase 6): non-admins get the simplified end-user
    # rendering, admins/IT get the full procedure. Default expert when flag off.
    from .config import ROLE_DEPTH
    _audience = "simple" if (ROLE_DEPTH and user.get("role") != "admin") else "expert"

    _trace: dict = {}   # ordered label->detail, persisted with the answer so reopened chats show the trace
    def _step(label, detail="", state="done", reset=False):
        _trace[label] = detail
        d = {"type": "step", "label": label, "detail": detail, "state": state}
        if reset:
            d["reset"] = True
        return json.dumps(d) + "\n"

    def _trace_meta(t0):
        return {"steps": [{"label": k, "detail": v} for k, v in _trace.items()],
                "thought_ms": int((time.monotonic() - t0) * 1000)}

    kid = user.get("_embed_key_id")     # set only for embed-widget visitors

    def gen():
        from . import ingest_control as kill
        _t0_req = time.monotonic()
        yield json.dumps({"type": "meta", "conversation_id": conv_id}) + "\n"
        # per-group feature gate: chat must be enabled for this user's access group
        if not rbac.has_feature(user, "chat"):
            yield json.dumps({"type": "token", "v": "Chat isn't enabled for your access group. Ask an admin."}) + "\n"
            yield json.dumps({"type": "done", "pages": [], "title": None}) + "\n"
            return
        if kill.chat_stopped():           # master stop active — refuse fast
            yield json.dumps({"type": "token", "v": "⏸ Assistant is paused by an admin. Try again shortly."}) + "\n"
            yield json.dumps({"type": "done", "pages": [], "title": None}) + "\n"
            return
        if kid:
            embed_mod.bump_message(kid)  # counts toward this key's daily cap

        # ---- GLOBAL / meta intent: corpus overview from doc summaries (grounded) ----
        # "summarize all docs", "what do you cover", "list SOPs" — answered from the
        # doc list, BEFORE per-page retrieval + the scope gate (which refuse them).
        try:
            from . import global_answer
            if global_answer.is_global_query(req.q):
                yield _step("Reading the knowledge base", "building a corpus overview")
                ov = global_answer.build_overview(sectors=_sectors, folders=_folders)
                ans = ov.get("answer") or "No documents are loaded yet."
                cited = ov.get("pages") or []
                ans, cited = _cite_policy(ans, cited)
                for i in range(0, len(ans), 48):
                    yield json.dumps({"type": "token", "v": ans[i:i + 48]}) + "\n"
                convo.add_message(conv_id, "user", req.q, [])
                bot_id = convo.add_message(conv_id, "bot", ans, cited, meta=_trace_meta(_t0_req))
                title = convo.autotitle(conv_id, req.q) if first_turn else None
                yield json.dumps({"type": "done", "pages": cited, "title": title,
                                  "clean": ans, "blind": False, "nearest": None,
                                  "message_id": bot_id, "global": True,
                                  "tokens": {"in": 0, "out": 0, "total": 0},
                                  "cost": 0, "cited_n": len(cited),
                                  "citations": appcfg.citations_enabled(),
                                  "grounded": True, "followups": []}) + "\n"
                return
        except Exception as e:
            print(f"[global] stream overview skipped: {e!r}")

        # ---- Phase 4: serve a cached answer from the Q&A bank (instant, zero-agent) ----
        # Only when an APPROVED pair near-matches this exact question, it isn't a
        # context-dependent follow-up, and the user didn't ask for deep mode.
        from .config import AUTO_QA_SERVE_ENABLED, AUTO_QA_SERVE_MIN_SIM, AUTO_QA_SERVE_MIN_LEN
        if (AUTO_QA_SERVE_ENABLED and req.mode != "deep" and (first_turn or not _is_followup(req.q))):
            try:
                from . import qa as qa_mod
                hit = qa_mod.serve_match(req.q, AUTO_QA_SERVE_MIN_SIM, AUTO_QA_SERVE_MIN_LEN,
                                         sectors=_sectors, folders=_folders)
            except Exception as e:
                hit = None
                print(f"[qa] serve_match skipped: {e!r}")
            if hit:
                ans = (hit.get("answer") or "").strip()
                cited = _pages_by_ids(hit.get("page_ids") or [])
                ans, cited = _cite_policy(ans, cited)
                yield _step("Answered from the Q&A bank",
                            f"matched a saved answer ({int((hit.get('sim') or 0) * 100)}% match)")
                for i in range(0, len(ans), 48):   # chunk for a streamed feel
                    yield json.dumps({"type": "token", "v": ans[i:i + 48]}) + "\n"
                convo.add_message(conv_id, "user", req.q, [])
                bot_id = convo.add_message(conv_id, "bot", ans, cited, meta=_trace_meta(_t0_req))
                qa_mod.bump_served(hit["id"])
                try:
                    analytics_mod.record_metric(
                        message_id=bot_id, conversation_id=conv_id, user_id=user.get("id"),
                        model="qa-bank", mode="cache",
                        ms_total=int((time.monotonic() - _t0_req) * 1000),
                        cited_n=len(cited), blind=(len(cited) == 0), cache_hit=True)
                except Exception:
                    pass
                title = convo.autotitle(conv_id, req.q) if first_turn else None
                yield json.dumps({"type": "done", "pages": cited, "title": title,
                                  "clean": ans, "blind": False, "nearest": None,
                                  "message_id": bot_id,
                                  "tokens": {"in": 0, "out": 0, "total": 0},
                                  "cost": 0, "cited_n": len(cited),
                                  "followups": qa_mod.sibling_questions(hit["id"]),
                                  "served_from_bank": True,
                                  "served_qa_id": hit["id"],
                                  "grounded": len(cited) > 0}) + "\n"
                return

        # ---- auto complexity routing: resolve quick|deep|auto → quick|deep ----
        from .router import resolve_mode
        _eff_mode, _routed = resolve_mode(req.q, req.mode)
        req.mode = _eff_mode
        if _routed:
            yield _step("Auto-routing", f"chose {_eff_mode} mode for this question")

        # ---- telemetry: latency / tokens / retrieval (answer_metrics) ----
        t0 = time.monotonic()
        meter: dict = {}            # filled by stream_reply: model, tok_in, tok_out
        first_ms = [None]           # time-to-first-token (list = mutable in closure)
        seed_n = [0]                # pages from first retrieval
        wide_n = [0]                # extra pages on wider retry
        wider_fired = [False]

        # ---- live "thinking" trace: what the agent is actually doing ----
        yield _step("Understanding your question", req.q[:80], "running")
        # fold the prior question + inject history ONLY for a real follow-up
        # (anaphoric cue) — a bare new topic stands alone so it can't inherit the
        # previous topic's context (fix: short new Q answered the old topic)
        _sq, _use_hist = resolve_query(req.q, history, followup_re=_FOLLOWUP_RE)
        _hist = history if _use_hist else []
        if AGENTIC_RETRIEVE:
            seed, _subs = agentic_search(_sq, k=req.k or DEFAULT_K, sectors=_sectors, folders=_folders)
            if _subs:
                yield _step("Searching deeper", "also tried: " + " · ".join(_subs))
        else:
            seed = search_pages(_sq, k=req.k or DEFAULT_K, sectors=_sectors, folders=_folders)
        # retrieval funnel counts (scanned/pool/reranked) for answer_metrics
        try:
            from .retrieve import last_funnel
            _funnel = last_funnel()
        except Exception:
            _funnel = {}

        # off-topic gate: refuse clearly out-of-scope questions before answering.
        # Reuse the seed we already fetched (no extra search / query-expansion LLM
        # call). Conservative — only fires on weak retrieval + junk/zero pages.
        # Use the follow-up-folded query (_sq) so a short follow-up keeps its topic
        # and isn't wrongly refused for having no standalone keywords.
        _ok_scope, _refusal = scope.in_scope(_sq, lambda _q: seed)
        if not _ok_scope:
            # second chance: rephrase into runbook wording, re-search, only then
            # refuse. One LLM call, fires only on would-be refusals.
            yield _step("Rephrasing the question", "matching it to runbook wording", "running")
            _rec_pages, _rec_q, _rec_subs = scope.try_recover(
                _sq, lambda s: search_pages(s, k=req.k or DEFAULT_K,
                                            sectors=_sectors, folders=_folders),
                sectors=_sectors, folders=_folders)
            if _rec_pages:
                seed, _ok_scope = _rec_pages, True
                yield _step("Rephrasing the question",
                            f'searched as: "{_rec_q}"', "done")
            elif _rec_subs:
                yield _step("Rephrasing the question",
                            "tried " + " · ".join(_rec_subs[:3]) + " — still no match", "done")
        if not _ok_scope:
            yield _step("Outside the runbooks", "off-topic — declined", "done")
            yield json.dumps({"type": "token", "v": _refusal}) + "\n"
            convo.add_message(conv_id, "user", req.q, [])
            _bid = convo.add_message(conv_id, "bot", _refusal, [])
            if first_turn:
                convo.autotitle(conv_id, req.q)
            try:
                notify.emit("audit", "Off-topic question declined", req.q[:90],
                            "info", dedupe_hours=12)
            except Exception:
                pass
            yield json.dumps({"type": "done", "pages": [], "title": None,
                              "clean": _refusal, "blind": True, "nearest": None,
                              "message_id": _bid, "cited_n": 0,
                              "grounded": False}) + "\n"
            return

        if not seed:
            yield _step("Searched the knowledge base", "no matching runbooks found")
            msg = "No documents uploaded yet. Please upload SOPs/policies first."
            yield json.dumps({"type": "token", "v": msg}) + "\n"
            convo.add_message(conv_id, "user", req.q, [])
            convo.add_message(conv_id, "bot", msg, [])
            if first_turn:
                convo.autotitle(conv_id, req.q)
            yield json.dumps({"type": "done", "pages": [], "title": None}) + "\n"
            return

        docs_hit = {p["doc_name"] for p in seed}
        yield _step(f"Searched {len(docs_hit)} runbook{'s' if len(docs_hit) != 1 else ''}",
                    f"matched {len(seed)} relevant page{'s' if len(seed) != 1 else ''}")
        refs = " · ".join(f"{_friendly_doc(p['doc_name'])} p.{p['page_no']}" for p in seed[:4])
        if len(seed) > 4:
            refs += f" · +{len(seed) - 4}"
        yield _step(f"Reading {len(seed)} source page{'s' if len(seed) != 1 else ''}", refs)
        if req.mode == "deep":
            yield _step("Deep mode", "reading page images + navigating the outline", "running")
        yield _step("Composing the answer with citations", "", "running")

        def _compose(pages):
            """Stream one composition; yields ('tok', delta)* then ('acc', full)."""
            buf = ""
            for tok in stream_reply(req.q, pages, mode=req.mode,
                                    session_id=str(conv_id), history=_hist,
                                    meter=meter, audience=_audience):
                if kill.chat_stopped():    # master stop mid-answer → abort
                    break
                buf += tok
                yield ("tok", tok)
            yield ("acc", buf)

        # ---- pass 1 ----
        acc = ""
        try:
            for kind, val in _compose(seed):
                if kind == "tok":
                    if first_ms[0] is None:
                        first_ms[0] = int((time.monotonic() - t0) * 1000)
                    yield json.dumps({"type": "token", "v": val}) + "\n"
                else:
                    acc = val
        except Exception as e:
            yield json.dumps({"type": "error", "detail": f"agent error: {e}"}) + "\n"
            return

        BLIND_LOOP = os.getenv("BLINDSPOT_LOOP", "1") == "1"
        blind = False
        nearest = None
        if not BLIND_LOOP:
            # legacy: trust the answer, fall back to seed pages as citations
            clean, nums = parse_pages(acc, [])
            clean = render_cited(clean, seed)            # [N] -> [p.X] for display
            page_ids = map_cited(nums, seed) or [p["page_id"] for p in seed]
            cited = _pages_by_ids(page_ids) or _pages_by_ids([p["page_id"] for p in seed])
        else:
            # strict groundedness: PAGES:none -> [] (no seed-fallback masking, so a
            # truly unsourced answer is detectable instead of silently "cited")
            clean, _nums = parse_pages(acc, [])          # strips PAGES: line for display
            page_ids = cited_page_ids(acc, seed)         # inline [N] + PAGES: -> page_ids
            clean = render_cited(clean, seed)
            used = seed

            # blind-spot self-correction: model grounded nothing -> search WIDER and
            # re-compose ONCE (reset clears the first attempt on the client) before
            # we fall back to an honest "not in the runbooks" answer.
            if not page_ids:
                wide = search_pages(_sq,
                                    k=min(20, (req.k or DEFAULT_K) * 2 + 4), sectors=_sectors, folders=_folders)
                # agentic recovery: LLM reformulates the question into new angles and
                # we search each — finds pages plain widening misses (Vercel idea).
                reform: list[str] = []
                if AGENTIC_RETRIEVE:
                    from .retrieve import _reformulate
                    reform = _reformulate(req.q)
                    for s in reform:
                        wide += search_pages(s, k=req.k or DEFAULT_K, sectors=_sectors, folders=_folders)
                seed_ids = {p["page_id"] for p in seed}
                fresh, fseen = [], set(seed_ids)
                for p in wide:
                    if p["page_id"] not in fseen:
                        fseen.add(p["page_id"]); fresh.append(p)
                if fresh:
                    merged = (seed + fresh)[:20]
                    wider_fired[0] = True
                    wide_n[0] = len(fresh)
                    detail = f"pulled {len(fresh)} more page{'s' if len(fresh) != 1 else ''}"
                    if reform:
                        detail += " · tried: " + " · ".join(reform)
                    yield _step("No grounded source — searching wider", detail, "running", reset=True)
                    acc2 = ""
                    try:
                        for kind, val in _compose(merged):
                            if kind == "tok":
                                if first_ms[0] is None:
                                    first_ms[0] = int((time.monotonic() - t0) * 1000)
                                yield json.dumps({"type": "token", "v": val}) + "\n"
                            else:
                                acc2 = val
                    except Exception as e:
                        yield json.dumps({"type": "error", "detail": f"agent error: {e}"}) + "\n"
                        return
                    clean, _nums = parse_pages(acc2, [])
                    page_ids = cited_page_ids(acc2, merged)   # inline [N] + PAGES:
                    clean = render_cited(clean, merged)
                    used = merged

            if not page_ids:
                # genuine blind spot — don't pass an unsourced guess off as fact
                note = ("⚠️ I couldn't find this in the uploaded runbooks, so this isn't a "
                        "sourced answer — please verify before relying on it.\n\n")
                clean = note + (clean or "I don't have a runbook that covers this yet.")
                cited = []
                blind = True
                nearest = _friendly_doc(used[0]["doc_name"]) if used else None
                yield _step("No source in the knowledge base", "flagged as a blind spot")
                try:
                    notify.emit("audit", "Blind spot — answered with no source",
                                req.q[:90], "warn", dedupe_hours=12)
                except Exception:
                    pass
            else:
                cited = _pages_by_ids(page_ids) or _pages_by_ids([p["page_id"] for p in used])

        # followups are folded into the answer stream (FOLLOWUPS: line) — strip + extract,
        # so no second LLM call is needed for the suggestion chips
        clean, fups = parse_followups(clean)

        # per-deployment citation toggle: drop [N]/PAGES + source coins when OFF
        clean, cited = _cite_policy(clean, cited)

        convo.add_message(conv_id, "user", req.q, [])
        bot_id = convo.add_message(conv_id, "bot", clean, cited, meta=_trace_meta(_t0_req))

        # ---- persist answer telemetry (fail-soft; never breaks the stream) ----
        try:
            analytics_mod.record_metric(
                message_id=bot_id, conversation_id=conv_id, user_id=user["id"],
                model=meter.get("model"), mode=req.mode,
                ms_first_token=first_ms[0],
                ms_total=int((time.monotonic() - t0) * 1000),
                tok_in=meter.get("tok_in", 0), tok_out=meter.get("tok_out", 0),
                seed_n=len(seed), wide_n=wide_n[0], wider_fired=wider_fired[0],
                cited_n=len(cited), blind=blind,
                scanned=_funnel.get("scanned"), pool=_funnel.get("pool"),
                reranked=_funnel.get("reranked"),
            )
        except Exception as e:
            print(f"[metrics] record skipped: {e!r}")

        # freshness: bump cited_count for any active fact whose wording appears
        try:
            touch_used_facts(clean, question=req.q, conversation_id=str(conv_id))
        except Exception as e:
            print(f"[learn] touch skipped: {e!r}")

        # self-learning: Layer A (reactive, intent-gated). If it didn't fire,
        # Layer B (always-extract) mines the turn — only when AUTO_LEARN_ENABLED.
        try:
            learned = maybe_learn(req.q, clean, user.get("email"))
            if not learned:
                from .learn import auto_learn
                from .config import AUTO_LEARN_MODE
                if AUTO_LEARN_MODE == "per_answer":   # nightly mode mines via daemon instead
                    learned = auto_learn(req.q, clean, user.get("email"),
                                         is_admin=(user.get("role") == "admin"))
        except Exception as e:
            learned = None
            print(f"[learn] skipped: {e!r}")
        if learned:
            yield _step("Learned something new", f"saved for review: {learned[:80]}")
            notify.emit("memory", "Learned from chat (pending review)", learned[:80],
                        "info", dedupe_hours=1)

        title = convo.autotitle(conv_id, req.q) if first_turn else None
        # live token + cost + grounding readout for the chat UI
        tok_in = meter.get("tok_in", 0) or 0
        tok_out = meter.get("tok_out", 0) or 0
        try:
            price = float(appcfg.get_config().get("llm_price_per_mtok", 0.30))
        except Exception:
            price = 0.30
        cost = round((tok_in + tok_out) / 1_000_000 * price, 6)
        if kid:
            embed_mod.record_usage(kid, tok_in + tok_out, cost)  # daily $ ceiling
        # tell the client the PAGES line was stripped (clean replaces the raw stream)
        yield json.dumps({"type": "done", "pages": cited, "title": title,
                          "clean": clean, "blind": blind, "nearest": nearest,
                          "message_id": bot_id,
                          "tokens": {"in": tok_in, "out": tok_out, "total": tok_in + tok_out},
                          "cost": cost, "cited_n": len(cited),
                          "followups": fups,
                          "citations": appcfg.citations_enabled(),
                          "grounded": (len(cited) > 0 and not blind)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---------------- conversations (per-user chat history) ----------------
@router.get("/conversations", dependencies=[Depends(require_key)])
def list_conversations(user: dict = Depends(current_user)):
    return {"conversations": convo.list_for_user(user["id"])}


@router.post("/conversations", dependencies=[Depends(require_key)])
def new_conversation(user: dict = Depends(current_user)):
    return convo.create(user["id"])


@router.get("/conversations/{conv_id}", dependencies=[Depends(require_key)])
def get_conversation(conv_id: int, user: dict = Depends(current_user)):
    conv = convo.owned(conv_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation": {"id": conv["id"], "title": conv["title"]},
            "messages": convo.messages(conv_id)}


class RenameRequest(BaseModel):
    title: str


@router.patch("/conversations/{conv_id}", dependencies=[Depends(require_key)])
def rename_conversation(conv_id: int, req: RenameRequest, user: dict = Depends(current_user)):
    row = convo.rename(conv_id, user["id"], req.title.strip() or "New chat")
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    return row


@router.delete("/conversations/{conv_id}", dependencies=[Depends(require_key)])
def delete_conversation(conv_id: int, user: dict = Depends(current_user)):
    if not convo.delete(conv_id, user["id"]):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True, "deleted": conv_id}


@router.post("/upload")
def upload(file: UploadFile = File(...), folder_id: int | None = Form(None),
           user: dict = Depends(current_user)):
    """Accept a SOP/policy (PDF or image) and queue it for async ingest, optionally
    into a Sources folder. Returns instantly; the background worker renders + reads
    the pages and the doc flips queued -> processing -> ready."""
    # row-level access: super-admin (global) or sector-admin (own sector) may
    # upload. With RBAC off this is admin-only (back-compat). A doc lands in the
    # chosen folder; its sector comes from that folder (else the uploader's sector).
    _sec = rbac.user_write_sector(user)
    if folder_id is not None:
        with get_conn() as conn:
            fr = conn.execute("SELECT sector_id FROM folders WHERE id=%s", (folder_id,)).fetchone()
        if fr is None:
            raise HTTPException(status_code=404, detail="folder not found")
        _sec = fr["sector_id"]
    # content managers (admin-tier or a plain user in an empowered group) may upload;
    # otherwise fall back to the sector-RBAC write rule (super/sector-admin).
    if not (rbac.can_manage_content(user) or rbac.can_write(user, _sec)):
        raise HTTPException(status_code=403, detail="Not allowed to upload documents.")
    name = file.filename or "upload"
    # duplicate guard: refuse a doc whose name already exists and is live
    # (queued/processing/ready). Re-uploading a previously failed/cancelled name
    # is allowed (acts as a retry). Marker phrase "already exists" lets the UI
    # render a friendly "skipped" row instead of a hard error.
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT id FROM docs WHERE name = %s "
            "AND status IN ('queued', 'processing', 'ready') LIMIT 1",
            (name,),
        ).fetchone()
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f'A document named "{name}" already exists — upload skipped.',
        )
    try:
        key = storage.save_to_inbox(file.file, name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"save failed: {e}")
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, sector_id, folder_id, updated_at) "
            "VALUES (%s, 'queued', 0, %s, %s, %s, %s, now()) RETURNING id",
            (name, key, user.get("email"), _sec, folder_id),
        ).fetchone()
    notify.emit("ingest", f"Document queued · {name}", "processing…", "info")
    return {"ok": True, "name": name, "doc_id": row["id"], "status": "queued"}


# ---- Sources: folders (organize uploads; access-controlled when RBAC on) ----
class FolderPrincipal(BaseModel):
    type: str          # 'user' | 'group'
    id: int


class FolderBody(BaseModel):
    name: str
    sector_id: int | None = None
    parent_id: int | None = None            # nest under another folder; None = top level
    access_mode: str = "sector"             # sector | specific | org
    principals: list[FolderPrincipal] = []  # only for access_mode='specific'
    color: str | None = None                # hex like '#c2683f' (optional)
    icon: str | None = None                 # short emoji like '📊' (optional)


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: int | None = None            # move under a new parent (None = to top level)
    move: bool = False                      # set true to actually apply parent_id (else ignored)
    is_expanded: bool | None = None         # persist tree open/closed state
    color: str | None = None                # '' clears to NULL; None = leave unchanged
    icon: str | None = None                 # '' clears to NULL; None = leave unchanged


MAX_FOLDER_DEPTH = 6                          # guard against pathological nesting


class AccessBody(BaseModel):               # share/manage access (no name needed)
    access_mode: str = "sector"
    principals: list[FolderPrincipal] = []


@router.get("/folders")
def list_folders(user: dict = Depends(current_user)):
    """Folders the caller may see (scoped by sector when RBAC on), with doc counts."""
    secs = rbac.allowed_sectors(user)
    _sec = " AND (%(s)s::bigint[] IS NULL OR f.sector_id = ANY(%(s)s)) "
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT f.id, f.name, f.sector_id, f.parent_id, f.is_expanded, f.access_mode, "
            "f.color, f.icon, "
            "s.label AS sector_label, "
            "(SELECT count(*) FROM docs d WHERE d.folder_id = f.id) AS doc_count, "
            # subtree_count: docs in this folder PLUS every descendant folder
            "(SELECT count(*) FROM docs d WHERE d.folder_id IN ("
            "   WITH RECURSIVE sub(id) AS ("
            "     SELECT f.id "
            "     UNION ALL SELECT c.id FROM folders c JOIN sub ON c.parent_id = sub.id"
            "   ) SELECT id FROM sub)) AS subtree_count, "
            "(SELECT count(*) FROM folder_access fa WHERE fa.folder_id = f.id) AS access_n "
            "FROM folders f LEFT JOIN sectors s ON s.id = f.sector_id "
            "WHERE true" + _sec + " ORDER BY f.name",
            {"s": secs}).fetchall()
        unfiled = conn.execute(
            "SELECT count(*) AS n FROM docs d WHERE d.folder_id IS NULL "
            "AND (%(s)s::bigint[] IS NULL OR d.sector_id = ANY(%(s)s))",
            {"s": secs}).fetchone()["n"]
    return {"folders": [dict(r) for r in rows], "unfiled": unfiled}


@router.get("/principals")
def list_principals(user: dict = Depends(require_admin)):
    """Users + groups to populate the folder access picker (admin only)."""
    with get_conn() as conn:
        users = conn.execute(
            "SELECT id, email, role FROM users WHERE active = true ORDER BY email LIMIT 500"
        ).fetchall()
        try:
            groups = conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
        except Exception:
            groups = []
    return {"users": [dict(u) for u in users], "groups": [dict(g) for g in groups]}


def _folder_depth(conn, fid: int) -> int:
    """How many ancestors a folder has (top-level = 0). Bounded walk — a broken
    parent chain can never loop past the cap."""
    depth, cur = 0, fid
    for _ in range(MAX_FOLDER_DEPTH + 2):
        r = conn.execute("SELECT parent_id FROM folders WHERE id=%s", (cur,)).fetchone()
        if not r or r["parent_id"] is None:
            break
        cur, depth = r["parent_id"], depth + 1
    return depth


@router.post("/folders")
def create_folder(body: FolderBody, user: dict = Depends(current_user)):
    """Create a folder (or reuse an existing one with the same name under the same
    parent — get-or-create, so a directory import can call this per path segment
    without ever duplicating). Nest with `parent_id`; a child inherits its parent's
    sector. Super-admin (any sector) or sector-admin (own sector). access_mode:
    'sector' | 'org' | 'specific' (the users/groups in `principals`)."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name required.")
    parent = None
    if body.parent_id is not None:
        with get_conn() as conn:
            parent = conn.execute(
                "SELECT id, sector_id FROM folders WHERE id=%s", (body.parent_id,)).fetchone()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found.")
        sid = parent["sector_id"]                       # child inherits parent's sector
    else:
        sid = body.sector_id if body.sector_id is not None else rbac.user_write_sector(user)
    if not (rbac.can_manage_content(user) or rbac.can_write(user, sid)):
        raise HTTPException(status_code=403, detail="Not allowed to create folders.")
    mode = body.access_mode if body.access_mode in ("sector", "specific", "org") else "sector"
    # only a super-admin may grant org-wide (cross-sector) access
    if mode == "org" and not rbac.is_superadmin(user):
        raise HTTPException(status_code=403, detail="Only a super-admin can grant org-wide access.")
    with get_conn() as conn:
        if parent is not None and _folder_depth(conn, parent["id"]) + 1 >= MAX_FOLDER_DEPTH:
            raise HTTPException(status_code=400,
                                detail=f"Folders can nest at most {MAX_FOLDER_DEPTH} levels deep.")
        # get-or-create: same name, same parent, same sector → reuse (idempotent import)
        existing = conn.execute(
            "SELECT id, name, sector_id, parent_id, access_mode FROM folders "
            "WHERE lower(name)=lower(%s) AND sector_id=%s AND parent_id IS NOT DISTINCT FROM %s "
            "LIMIT 1",
            (name, sid, body.parent_id)).fetchone()
        if existing:
            return {"ok": True, "folder": dict(existing), "created": False}
        row = conn.execute(
            "INSERT INTO folders (sector_id, name, access_mode, parent_id, color, icon) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "RETURNING id, name, sector_id, parent_id, access_mode, color, icon",
            (sid, name, mode, body.parent_id,
             (body.color or None), (body.icon or None))).fetchone()
        if mode == "specific":
            for p in body.principals:
                if p.type in ("user", "group"):
                    conn.execute(
                        "INSERT INTO folder_access (folder_id, principal_type, principal_id) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (row["id"], p.type, p.id))
    return {"ok": True, "folder": dict(row), "created": True}


def _folder_or_403(fid: int, user: dict):
    with get_conn() as conn:
        f = conn.execute("SELECT id, name, sector_id, parent_id, access_mode FROM folders WHERE id=%s",
                         (fid,)).fetchone()
    if not f:
        raise HTTPException(status_code=404, detail="folder not found")
    if not (rbac.can_manage_content(user) or rbac.can_write(user, f["sector_id"])):
        raise HTTPException(status_code=403, detail="Not allowed to manage this folder.")
    return f


@router.get("/folders/{fid}/access")
def folder_access_get(fid: int, user: dict = Depends(current_user)):
    """Current sharing of a folder: mode + the users/groups it's shared with."""
    f = _folder_or_403(fid, user)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fa.principal_type AS type, fa.principal_id AS id, "
            "  CASE fa.principal_type WHEN 'user' THEN u.email ELSE g.name END AS label "
            "FROM folder_access fa "
            "LEFT JOIN users u ON fa.principal_type='user' AND u.id=fa.principal_id "
            "LEFT JOIN groups g ON fa.principal_type='group' AND g.id=fa.principal_id "
            "WHERE fa.folder_id=%s ORDER BY label", (fid,)).fetchall()
    return {"folder": dict(f), "access_mode": f["access_mode"],
            "principals": [dict(r) for r in rows]}


@router.put("/folders/{fid}/access")
def folder_access_set(fid: int, body: AccessBody, user: dict = Depends(current_user)):
    """Share / re-share a folder: set access_mode + replace the people/groups list."""
    _folder_or_403(fid, user)
    mode = body.access_mode if body.access_mode in ("sector", "specific", "org") else "sector"
    if mode == "org" and not rbac.is_superadmin(user):
        raise HTTPException(status_code=403, detail="Only a super-admin can grant org-wide access.")
    with get_conn() as conn:
        conn.execute("UPDATE folders SET access_mode=%s WHERE id=%s", (mode, fid))
        conn.execute("DELETE FROM folder_access WHERE folder_id=%s", (fid,))
        if mode == "specific":
            for p in body.principals:
                if p.type in ("user", "group"):
                    conn.execute(
                        "INSERT INTO folder_access (folder_id, principal_type, principal_id) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (fid, p.type, p.id))
    return {"ok": True}


@router.delete("/folders/{fid}")
def delete_folder(fid: int, user: dict = Depends(current_user)):
    """Delete a folder (admin / sector-admin via RBAC). Child folders are REPARENTED
    up to this folder's parent (nothing orphaned); this folder's own DOCUMENTS are
    un-filed (folder_id → NULL, moved to All documents) — never deleted."""
    f = _folder_or_403(fid, user)        # 404 if missing, 403 if not allowed to manage
    with get_conn() as conn:
        reparented = conn.execute(
            "UPDATE folders SET parent_id = %s WHERE parent_id = %s",
            (f.get("parent_id"), fid)).rowcount
        moved = conn.execute(
            "UPDATE docs SET folder_id = NULL WHERE folder_id = %s", (fid,)
        ).rowcount
        conn.execute("DELETE FROM folder_access WHERE folder_id = %s", (fid,))
        conn.execute("DELETE FROM folders WHERE id = %s", (fid,))
    audit_mod.log(user, "folder.delete", "folder", fid,
                  {"name": f.get("name"), "unfiled_docs": moved, "reparented_children": reparented})
    return {"ok": True, "deleted": fid, "unfiled_docs": moved or 0,
            "reparented_children": reparented or 0}


def _descendant_ids(conn, fid: int) -> set:
    """All folders below fid (recursive), bounded by the depth cap — used to block a
    move that would create a cycle (a folder inside its own subtree)."""
    seen, frontier = set(), [fid]
    for _ in range(MAX_FOLDER_DEPTH + 2):
        if not frontier:
            break
        rows = conn.execute(
            "SELECT id FROM folders WHERE parent_id = ANY(%s)", (frontier,)).fetchall()
        frontier = [r["id"] for r in rows if r["id"] not in seen]
        seen.update(frontier)
    return seen


@router.patch("/folders/{fid}")
def patch_folder(fid: int, body: FolderPatch, user: dict = Depends(current_user)):
    """Rename a folder, move it under a new parent (cycle-guarded, depth-capped), or
    persist its expand/collapse state. Only fields that are supplied change."""
    f = _folder_or_403(fid, user)
    sets, args = [], []
    if body.name is not None:
        nm = body.name.strip()
        if not nm:
            raise HTTPException(status_code=400, detail="Folder name cannot be empty.")
        sets.append("name = %s"); args.append(nm)
    if body.is_expanded is not None:
        sets.append("is_expanded = %s"); args.append(body.is_expanded)
    if body.color is not None:                      # '' clears to NULL
        sets.append("color = %s"); args.append(body.color.strip() or None)
    if body.icon is not None:                       # '' clears to NULL
        sets.append("icon = %s"); args.append(body.icon.strip() or None)
    if body.move:                                   # explicit opt-in to reparent
        new_parent = body.parent_id
        if new_parent is not None:
            with get_conn() as conn:
                p = conn.execute("SELECT id, sector_id FROM folders WHERE id=%s",
                                 (new_parent,)).fetchone()
                if not p:
                    raise HTTPException(status_code=404, detail="Target folder not found.")
                if new_parent == fid or new_parent in _descendant_ids(conn, fid):
                    raise HTTPException(status_code=400,
                                        detail="Cannot move a folder into itself or its own subfolder.")
                if _folder_depth(conn, new_parent) + 1 >= MAX_FOLDER_DEPTH:
                    raise HTTPException(status_code=400,
                                        detail=f"That would nest deeper than {MAX_FOLDER_DEPTH} levels.")
        sets.append("parent_id = %s"); args.append(new_parent)
    if not sets:
        return {"ok": True, "folder": dict(f)}
    args.append(fid)
    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE folders SET {', '.join(sets)} WHERE id=%s "
            "RETURNING id, name, sector_id, parent_id, is_expanded, access_mode, color, icon",
            tuple(args)).fetchone()
    return {"ok": True, "folder": dict(row)}


@router.get("/ingest/scan")
def ingest_scan_preview(user: dict = Depends(require_admin)):
    """Report what a server-folder import WOULD queue (no writes) — for the modal."""
    from .config import IMPORT_DIR
    base = IMPORT_DIR
    exts = {".pdf", ".png", ".jpg", ".jpeg"}
    if not base.exists():
        return {"ok": True, "dir": str(base), "exists": False, "found": 0, "new": 0, "skipped": 0}
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    found = new = 0
    for root, _, files in os.walk(base):
        for fn in files:
            if fn.startswith(".") or os.path.splitext(fn)[1].lower() not in exts:
                continue
            found += 1
            if fn not in existing:
                new += 1
    return {"ok": True, "dir": str(base), "exists": True, "found": found, "new": new, "skipped": found - new}


@router.post("/ingest/scan")
def ingest_scan(user: dict = Depends(require_admin)):
    """Scan the server import folder (config IMPORT_DIR) and queue every doc not
    already ingested. Dedup by filename. Reuses the async worker (status=queued)."""
    from .config import IMPORT_DIR
    base = IMPORT_DIR
    exts = {".pdf", ".png", ".jpg", ".jpeg"}
    if not base.exists():
        raise HTTPException(status_code=400, detail=f"import folder not found: {base}")
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    found = queued = skipped = 0
    for root, _, files in os.walk(base):
        for fn in files:
            if fn.startswith(".") or os.path.splitext(fn)[1].lower() not in exts:
                continue
            found += 1
            if fn in existing:
                skipped += 1
                continue
            try:
                with open(Path(root) / fn, "rb") as fh:
                    key = storage.save_to_inbox(fh, fn)
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, updated_at) "
                        "VALUES (%s, 'queued', 0, %s, %s, now())",
                        (fn, key, user.get("email")),
                    )
                existing.add(fn)
                queued += 1
            except Exception as e:
                print(f"[scan] {fn} failed: {e!r}")
    if queued:
        notify.emit("ingest", f"Imported {queued} document(s) from server folder",
                    f"{skipped} already present", "info")
    audit_mod.log(user, "doc.import_scan", "doc", 0, {"queued": queued, "skipped": skipped, "found": found})
    return {"ok": True, "found": found, "queued": queued, "skipped": skipped, "dir": str(base)}


@router.get("/ingest/s3-scan")
def ingest_s3_preview(user: dict = Depends(require_admin)):
    """What an S3 bulk-import WOULD queue from the configured bucket/prefix (no writes)."""
    from . import s3_import
    return s3_import.preview()


@router.post("/ingest/s3-import")
def ingest_s3_import(user: dict = Depends(require_admin)):
    """Pull every new document from the S3 import prefix and queue it for ingest."""
    from . import s3_import
    if not s3_import.s3client.enabled():
        raise HTTPException(status_code=400, detail="S3 not configured (set S3_BUCKET)")
    res = s3_import.import_all(uploaded_by=user.get("email"))
    if res.get("queued"):
        notify.emit("ingest", f"Imported {res['queued']} document(s) from S3",
                    f"{res.get('skipped', 0)} already present", "info")
    audit_mod.log(user, "doc.s3_import", "doc", 0, res)
    return res


# ---- Microsoft 365 credentials (Settings → Integrations → Microsoft 365) ----
@router.get("/integrations/microsoft/config")
def ms_config(user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.creds_config()


@router.post("/integrations/microsoft/config")
def ms_save_config(body: dict, user: dict = Depends(require_superadmin)):
    from . import sharepoint
    cfg = sharepoint.save_creds(body or {})
    audit_mod.log(user, "microsoft.config", "config", 0,
                  {"tenant": cfg.get("tenant_id"), "sp": cfg.get("sp_enabled"),
                   "od": cfg.get("od_enabled")})
    return cfg


@router.post("/integrations/microsoft/secret/clear")
def ms_clear_secret(user: dict = Depends(require_superadmin)):
    from . import sharepoint
    cfg = sharepoint.clear_creds_secret()
    audit_mod.log(user, "microsoft.secret_clear", "config", 0, {})
    return cfg


@router.post("/integrations/microsoft/test")
def ms_test(user: dict = Depends(require_superadmin)):
    from . import sharepoint
    return sharepoint.test_creds()


# ---- SharePoint / OneDrive (Microsoft Graph) ingest lane — per-source location ----
def _graph_kind(kind: str) -> str:
    if kind not in ("sharepoint", "onedrive"):
        raise HTTPException(status_code=404, detail="unknown source")
    return kind


@router.get("/ingest/graph/{kind}/config")
def graph_config(kind: str, user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.config(_graph_kind(kind))


@router.post("/ingest/graph/{kind}/config")
def graph_save_config(kind: str, body: dict, user: dict = Depends(require_superadmin)):
    from . import sharepoint
    kind = _graph_kind(kind)
    cfg = sharepoint.save_config(body or {}, kind=kind)
    audit_mod.log(user, f"{kind}.config", "config", 0,
                  {"loc": cfg.get("site_path") or cfg.get("user_upn")})
    return cfg


@router.post("/ingest/graph/{kind}/test")
def graph_test(kind: str, user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.test_connection(_graph_kind(kind))


@router.get("/ingest/graph/{kind}/scan")
def graph_preview(kind: str, user: dict = Depends(require_admin)):
    """What an import WOULD queue (no writes)."""
    from . import sharepoint
    return sharepoint.preview(_graph_kind(kind))


@router.post("/ingest/graph/{kind}/import")
def graph_import(kind: str, user: dict = Depends(require_admin)):
    """Pull every new file from the configured source and queue it."""
    from . import sharepoint
    kind = _graph_kind(kind)
    if not sharepoint.enabled(kind):
        raise HTTPException(status_code=400,
                            detail=f"{kind} not ready — set up Microsoft 365 in Settings and a location here")
    res = sharepoint.import_all(uploaded_by=user.get("email"), kind=kind)
    if res.get("queued"):
        label = "SharePoint" if kind == "sharepoint" else "OneDrive"
        notify.emit("ingest", f"Imported {res['queued']} document(s) from {label}",
                    f"{res.get('skipped', 0)} already present", "info")
    audit_mod.log(user, f"doc.{kind}_import", "doc", 0, res)
    return res


# ==== Unified SharePoint connector (push / device-code / app) ================
# ONE config + schedule + status, three methods. See app/sp_connector.py.
@router.get("/connector/sp")
def sp_get(user: dict = Depends(require_admin)):
    from . import sp_connector
    return sp_connector.config()


@router.post("/connector/sp")
def sp_save(body: dict, user: dict = Depends(require_superadmin)):
    from . import sp_connector
    body = body or {}
    if "site_url" in body and not sp_connector.valid_site_url(body.get("site_url")):
        raise HTTPException(status_code=400,
                            detail="site URL must be an https *.sharepoint.com address")
    cfg = sp_connector.save_config(body)
    audit_mod.log(user, "connector.sp.config", "config", 0,
                  {"method": cfg.get("method"), "site": cfg.get("site_url")})
    return cfg


@router.get("/connector/sp/status")
def sp_status(user: dict = Depends(require_admin)):
    from . import sp_connector
    return sp_connector.status()


@router.post("/connector/sp/sync-now")
def sp_sync_now(user: dict = Depends(require_admin)):
    from . import sp_connector
    res = sp_connector.sync_now()
    audit_mod.log(user, "connector.sp.sync_now", "connector", 0,
                  {"queued": res.get("queued"), "ok": res.get("ok")})
    return res


@router.post("/connector/sp/token/rotate")
def sp_rotate_token(user: dict = Depends(require_admin)):
    from . import sp_connector
    audit_mod.log(user, "connector.sp.token_rotate", "connector", 0, {})
    return sp_connector.rotate_token()


@router.get("/connector/sp/agent-script")
def sp_agent_script(request: Request, os: str = "mac", user: dict = Depends(require_admin)):
    """Generate the Desktop-Sync folder-watch script (carries the push token)."""
    from . import sp_connector
    from .config import PUBLIC_URL
    cfg = sp_connector._raw()
    tok = cfg.get("push_token")
    if not tok:
        tok = sp_connector.rotate_token()["token"]
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    out = sp_connector.agent_script(os, base, tok)
    audit_mod.log(user, "connector.sp.agent_script", "connector", 0, {"os": os})
    return out


@router.post("/connector/sp/device/start")
def sp_device_start(user: dict = Depends(require_admin)):
    from . import sp_graph
    return sp_graph.device_start()


@router.post("/connector/sp/device/poll")
def sp_device_poll(body: dict = Body(default=None), user: dict = Depends(require_admin)):
    from . import sp_graph
    dc = (body or {}).get("device_code", "")
    res = sp_graph.device_poll(dc)
    res["connected"] = sp_graph.connected()
    return res


@router.post("/connector/sp/device/disconnect")
def sp_device_disconnect(user: dict = Depends(require_admin)):
    from . import sp_graph
    audit_mod.log(user, "connector.sp.device_disconnect", "connector", 0, {})
    return sp_graph.disconnect()


@router.post("/connector/sp/app/test")
def sp_app_test(user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.test_connection("sharepoint")


# Desktop agent push lane — TOKEN auth (NOT a logged-in admin). The connector
# token is carried in X-Connector-Token (or ?token=). Files land in the inbox.
@router.post("/connector/push")
async def sp_connector_push(
    request: Request,
    file: UploadFile = File(...),
    x_connector_token: str = Header(default=""),
    token: str = Query(default=""),
):
    from . import sp_connector
    tok = x_connector_token or token
    if not sp_connector.verify_push_token(tok):
        raise HTTPException(status_code=401, detail="invalid connector token")
    if not sp_connector.push_rate_ok():
        raise HTTPException(status_code=429, detail="push rate limit exceeded")
    res = sp_connector.accept_push(file.file, file.filename or "upload.pdf")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("detail", "push failed"))
    return res


# ---- back-compat aliases (old SharePoint-only endpoints) ----
@router.get("/ingest/sharepoint/config")
def sharepoint_config(user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.config("sharepoint")


@router.post("/ingest/sharepoint/config")
def sharepoint_save_config(body: dict, user: dict = Depends(require_superadmin)):
    from . import sharepoint
    cfg = sharepoint.save_config(body or {}, kind="sharepoint")
    audit_mod.log(user, "sharepoint.config", "config", 0, {"site": cfg.get("site_path")})
    return cfg


@router.post("/ingest/sharepoint/test")
def sharepoint_test(user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.test_connection("sharepoint")


@router.get("/ingest/sharepoint/scan")
def sharepoint_preview(user: dict = Depends(require_admin)):
    from . import sharepoint
    return sharepoint.preview("sharepoint")


@router.post("/ingest/sharepoint/import")
def sharepoint_import(user: dict = Depends(require_admin)):
    from . import sharepoint
    if not sharepoint.enabled("sharepoint"):
        raise HTTPException(status_code=400,
                            detail="SharePoint not configured (set creds + GRAPH_CLIENT_SECRET)")
    res = sharepoint.import_all(uploaded_by=user.get("email"), kind="sharepoint")
    if res.get("queued"):
        notify.emit("ingest", f"Imported {res['queued']} document(s) from SharePoint",
                    f"{res.get('skipped', 0)} already present", "info")
    audit_mod.log(user, "doc.sharepoint_import", "doc", 0, res)
    return res


# ---------------- Storage config (super-admin, Settings → Storage) ----------------
@router.get("/storage/config", dependencies=[Depends(require_superadmin)])
def storage_config():
    """Effective storage config (DB over env), secret masked."""
    from .appcfg import get_s3
    return get_s3(reveal=False)


@router.post("/storage/config")
def storage_save(req: dict, user: dict = Depends(require_superadmin)):
    """Save storage config; reset the S3 client and create the bucket if needed."""
    from .appcfg import save_s3
    from . import s3client
    cfg = save_s3(req or {})
    s3client.reset()
    if cfg.get("backend") == "s3" and s3client.enabled():
        s3client.ensure_bucket()
    audit_mod.log(user, "storage.config", "config", 0,
                  {k: v for k, v in cfg.items() if k != "secret_access_key"})
    return cfg


@router.post("/storage/test")
def storage_test(req: dict | None = None, user: dict = Depends(require_admin)):
    """Test reaching the bucket with the CURRENTLY SAVED config (Save first)."""
    from . import s3client
    s3client.reset()
    return s3client.test_connection()


@router.post("/documents/{doc_id}/retry", dependencies=[Depends(require_content_manager)])
def retry_doc(doc_id: int, user: dict = Depends(require_content_manager)):
    """Re-queue a failed (or stuck) document for ingest."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE docs SET status = 'queued', progress = 0, pages_done = 0, "
            "error = NULL WHERE id = %s RETURNING id",
            (doc_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="document not found")
    from . import ingest_control as kill
    kill.clear_doc(doc_id)   # drop any stale cancel request so it can re-ingest
    audit_mod.log(user, "doc.retry", "doc", doc_id)
    return {"ok": True, "id": doc_id, "status": "queued"}


# ── Enrichment Agent (deferred PHASE-2 lane) ────────────────────────────────────
@router.get("/enrich/status")
def enrich_status(user: dict = Depends(current_user)):
    from . import enrich_agent
    return enrich_agent.status()


@router.post("/enrich/pause")
def enrich_pause(user: dict = Depends(require_admin)):
    from . import enrich_agent
    enrich_agent.pause()
    return {"ok": True, "paused": True}


@router.post("/enrich/resume")
def enrich_resume(user: dict = Depends(require_admin)):
    from . import enrich_agent
    enrich_agent.resume()
    return {"ok": True, "paused": False}


@router.post("/enrich/concurrency")
def enrich_concurrency(body: dict, user: dict = Depends(require_admin)):
    from . import enrich_agent
    n = enrich_agent.set_concurrency((body or {}).get("concurrency", 2))
    return {"ok": True, "concurrency": n}


@router.post("/enrich/doc/{doc_id}/skip")
def enrich_skip(doc_id: int, user: dict = Depends(require_admin)):
    """Accept the doc as-is (answerable) and stop its background enrichment."""
    from . import ingest_control as kill
    kill.cancel_doc(doc_id)          # halt if mid-enrich
    with get_conn() as conn:
        conn.execute(
            "UPDATE docs SET status='ready', updated_at=now() WHERE id=%s AND status='ready_lite'",
            (doc_id,),
        )
    audit_mod.log(user, "enrich.skip", "doc", doc_id)
    return {"ok": True, "id": doc_id}


# ── Eval Agent (offline answer-quality scoring) ─────────────────────────────────
@router.get("/eval/status")
def eval_status(user: dict = Depends(current_user)):
    from . import eval_agent
    return eval_agent.status()


@router.get("/eval/docs")
def eval_docs(user: dict = Depends(current_user)):
    from . import eval_agent
    return {"docs": eval_agent.doc_list()}


@router.get("/eval/doc/{doc_id}")
def eval_doc(doc_id: int, user: dict = Depends(current_user)):
    from . import eval_agent
    return eval_agent.doc_detail(doc_id)


@router.post("/eval/run")
def eval_run(user: dict = Depends(require_admin)):
    """Force a manual eval pass now (scores ALL ready docs)."""
    from . import eval_agent
    started = eval_agent.force_run()
    audit_mod.log(user, "eval.run", "eval", 0)
    return {"ok": True, "started": started,
            "detail": "queued" if started else "a run is already in progress"}


@router.post("/eval/pause")
def eval_pause(user: dict = Depends(require_admin)):
    from . import eval_agent
    eval_agent.pause()
    return {"ok": True, "paused": True}


@router.post("/eval/resume")
def eval_resume(user: dict = Depends(require_admin)):
    from . import eval_agent
    eval_agent.resume()
    return {"ok": True, "paused": False}


@router.post("/eval/concurrency")
def eval_concurrency(body: dict, user: dict = Depends(require_admin)):
    from . import eval_agent
    n = eval_agent.set_concurrency((body or {}).get("concurrency", 2))
    return {"ok": True, "concurrency": n}


_STAGES = ["Queued", "Render", "Read", "Structure", "Compile", "Enrich", "Ready"]


def _stage_of(status: str, progress: int) -> int:
    """Map status/progress → pipeline stage index (mirrors the Sources right panel)."""
    if status == "ready":
        return 6
    if status in ("failed", "cancelled"):
        return 0
    p = progress or 0
    if p < 10:
        return 0          # queued
    if p < 30:
        return 1          # render
    if p < 80:
        return 2          # read (vision)
    if p < 86:
        return 3          # structure (PageIndex)
    if p < 96:
        return 4          # compile (wiki)
    return 5              # enrich


@router.get("/documents/{doc_id}/processing", dependencies=[Depends(require_key)])
def doc_processing(doc_id: int):
    """Everything the Sources right-side process panel needs: live status + stage,
    per-page vision state, knowledge-enricher counts, and this doc's recent log."""
    with get_conn() as conn:
        d = conn.execute(
            "SELECT id, name, status, progress, pages_done, page_count, error, "
            "doc_type, folder_id, sector_id, uploaded_by, lang, ready_at, created_at "
            "FROM docs WHERE id = %s", (doc_id,)).fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="document not found")
        pages = conn.execute(
            "SELECT page_no, (coalesce(vision_text,'') <> '') AS read "
            "FROM pages WHERE doc_id = %s ORDER BY page_no", (doc_id,)).fetchall()
        enr = conn.execute(
            "SELECT "
            "(SELECT count(*) FROM doc_pages_md WHERE doc_id=%(d)s) AS compiled, "
            "(SELECT count(*) FROM doc_playbook WHERE doc_id=%(d)s) AS playbook, "
            "(SELECT count(*) FROM doc_lookup WHERE doc_id=%(d)s) AS lookup, "
            "(SELECT count(*) FROM doc_dependency WHERE from_doc=%(d)s) AS dependencies, "
            "(SELECT count(*) FROM doc_tree WHERE doc_id=%(d)s) AS tree, "
            "(SELECT count(*) FROM entity_mention WHERE doc_id=%(d)s) AS entities, "
            "(SELECT count(*) FROM qa_pairs WHERE doc_id=%(d)s) AS qa",
            {"d": doc_id}).fetchone()
        log = conn.execute(
            "SELECT id, step, msg, level, ts FROM ingest_log WHERE doc_id=%s "
            "ORDER BY id DESC LIMIT 40", (doc_id,)).fetchall()
    stage = _stage_of(d["status"], d["progress"])
    return {
        "doc": {"id": d["id"], "name": d["name"], "status": d["status"],
                "progress": d["progress"] or 0, "pages_done": d["pages_done"] or 0,
                "page_count": d["page_count"] or 0, "error": d["error"],
                "doc_type": d["doc_type"], "folder_id": d["folder_id"],
                "uploaded_by": d["uploaded_by"], "lang": d["lang"]},
        "stage": stage, "stages": _STAGES,
        "pages": [{"page_no": p["page_no"], "read": bool(p["read"])} for p in pages],
        "enrichers": dict(enr),
        "log": [{"id": r["id"], "step": r["step"], "msg": r["msg"],
                 "level": r["level"] or "info",
                 "ts": r["ts"].isoformat() if r["ts"] else None}
                for r in reversed(log)],
    }


class MoveBody(BaseModel):
    folder_id: int | None = None


@router.patch("/documents/{doc_id}", dependencies=[Depends(require_key)])
def move_document(doc_id: int, body: MoveBody, user: dict = Depends(current_user)):
    """Move a document into a folder (or out, folder_id=null). Sector follows the
    target folder. Super-admin or sector-admin of the target sector only."""
    target_sector = None
    if body.folder_id is not None:
        with get_conn() as conn:
            fr = conn.execute("SELECT sector_id FROM folders WHERE id=%s",
                              (body.folder_id,)).fetchone()
        if fr is None:
            raise HTTPException(status_code=404, detail="folder not found")
        target_sector = fr["sector_id"]
    if not (rbac.can_manage_content(user) or rbac.can_write(user, target_sector)):
        raise HTTPException(status_code=403, detail="Not allowed to move into this folder.")
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE docs SET folder_id=%s, sector_id=%s, updated_at=now() "
            "WHERE id=%s RETURNING id", (body.folder_id, target_sector, doc_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True, "id": doc_id, "folder_id": body.folder_id}


@router.post("/documents/{doc_id}/categorize", dependencies=[Depends(require_content_manager)])
def recategorize_doc(doc_id: int, user: dict = Depends(current_user)):
    """Re-run the LLM topic categorizer for one document (admin)."""
    from .categorize import categorize_doc
    cat = categorize_doc(doc_id)
    audit_mod.log(user, "doc.categorize", "doc", doc_id, {"category": cat})
    return {"ok": True, "id": doc_id, "category": cat}


@router.post("/documents/categorize-all", dependencies=[Depends(require_content_manager)])
def categorize_all_docs(force: bool = False, user: dict = Depends(current_user)):
    """Backfill topic categories for all ready docs (force=re-tag already-tagged) (admin)."""
    from .categorize import categorize_all
    res = categorize_all(force=force)
    audit_mod.log(user, "doc.categorize_all", "doc", 0, {"n": res.get("categorized")})
    return res


@router.post("/ingest/stop", dependencies=[Depends(require_admin)])
def ingest_stop(user: dict = Depends(current_user)):
    """MASTER KILL: pause the queue, cancel the in-flight doc, abort chat/LLM."""
    from . import ingest_control as kill
    st = kill.stop_all()
    audit_mod.log(user, "ingest.stop", "ingest", 0)
    notify.emit("ops", "Ingest stopped by admin", "Master stop — queue paused, current doc cancelled", "warn")
    return {"ok": True, **st}


@router.post("/ingest/resume", dependencies=[Depends(require_admin)])
def ingest_resume(user: dict = Depends(current_user)):
    """Resume after a master stop — worker starts claiming queued docs again."""
    from . import ingest_control as kill
    st = kill.resume()
    audit_mod.log(user, "ingest.resume", "ingest", 0)
    return {"ok": True, **st}


@router.get("/ingest/state", dependencies=[Depends(require_key)])
def ingest_state():
    """Kill-switch state + live queue counts (for the Stop-all button + banner)."""
    from . import ingest_control as kill
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, count(*) n FROM docs "
            "WHERE status IN ('queued','processing','cancelled') GROUP BY status"
        ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    return {**kill.get_state(), "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0), "cancelled": counts.get("cancelled", 0)}


@router.post("/documents/{doc_id}/cancel", dependencies=[Depends(require_content_manager)])
def cancel_doc_route(doc_id: int, user: dict = Depends(current_user)):
    """Cancel ONE document: if queued → drop it now; if processing → signal abort."""
    from . import ingest_control as kill
    kill.cancel_doc(doc_id)
    with get_conn() as conn:
        # a queued doc isn't being touched by the worker → cancel it immediately
        conn.execute(
            "UPDATE docs SET status = 'cancelled', progress = 0, error = 'cancelled by admin' "
            "WHERE id = %s AND status = 'queued'",
            (doc_id,),
        )
    audit_mod.log(user, "doc.cancel", "doc", doc_id)
    return {"ok": True, "id": doc_id}


@router.post("/memory")
def teach(req: MemoryRequest, user: dict = Depends(require_knowledge_manager)):
    """Teach DocSensei a fact (self-learning). Recalled on every /ask.
    Status follows the governance policy for source='human' (default: auto-approve)."""
    from . import governance
    status = governance.decide_status("human", None, is_admin=(user.get("role") == "admin"))
    mid = add_memory(req.value, req.key, source="human",
                     created_by=user.get("email"), status=status)
    notify.emit("memory", "Fact taught", (req.value or "")[:80], "info")
    return {"ok": True, "id": mid, "status": status}


@router.get("/memory", dependencies=[Depends(require_key)])
def get_memory(status: str | None = None):
    """List facts. Optional ?status=active|pending|rejected. Also returns the
    pending count so the Facts tab can badge the review queue."""
    return {"memory": list_memory(status=status), "pending": pending_count()}


@router.get("/governance", dependencies=[Depends(require_key)])
def get_governance():
    """Per-source fact approval policy (auto vs review + confidence floor)."""
    from . import governance
    return {"policy": governance.get_policy(), "sources": governance.SOURCES}


@router.post("/governance")
def save_governance(req: dict, user: dict = Depends(require_superadmin)):
    """Update the per-source approval policy (admin)."""
    from . import governance
    pol = governance.save_policy(req or {})
    audit_mod.log(user, "governance.update", "config", 0, req)
    return {"ok": True, "policy": pol}


@router.get("/settings/features", dependencies=[Depends(require_key)])
def get_features():
    """Per-deployment feature toggles (e.g. citations on/off) — admin-editable so
    one codebase serves different projects with no redeploy."""
    return {"features": appcfg.get_features()}


@router.post("/settings/features")
def save_features(req: dict, user: dict = Depends(require_superadmin)):
    """Update feature toggles (admin). Body: {citations: bool, ...}."""
    feats = appcfg.save_features(req or {})
    audit_mod.log(user, "features.update", "config", 0, req)
    return {"ok": True, "features": feats}


@router.get("/settings/wiki-schema", dependencies=[Depends(require_key)])
def get_wiki_schema():
    """Wiki / knowledge schema (Karpathy LLM-wiki Layer 3) — the per-project
    conventions the knowledge base is maintained to. Read by compiler +
    contradiction + temporal + browse phases."""
    return {"schema": appcfg.get_wiki_schema(), "defaults": appcfg._WIKI_SCHEMA_DEFAULTS}


@router.post("/settings/wiki-schema")
def save_wiki_schema(req: dict, user: dict = Depends(require_superadmin)):
    """Update the wiki schema (admin). Body = a partial schema patch."""
    schema = appcfg.save_wiki_schema(req or {})
    audit_mod.log(user, "wiki_schema.update", "config", 0, req)
    return {"ok": True, "schema": schema}


@router.get("/settings/persona", dependencies=[Depends(require_key)])
def get_persona():
    """The agent persona (identity/voice) — generated from the corpus, applied on
    top of grounding (tone only, never changes the facts)."""
    return {"persona": appcfg.get_persona()}


@router.post("/settings/persona")
def save_persona(req: dict, user: dict = Depends(require_superadmin)):
    """Save the persona (admin). Body = a partial persona patch. Bumps version."""
    p = appcfg.save_persona(req or {})
    audit_mod.log(user, "persona.update", "config", 0, {"version": p.get("version")})
    return {"ok": True, "persona": p}


@router.post("/settings/persona/generate")
def generate_persona(user: dict = Depends(require_admin)):
    """Full knowledge scan → propose a persona draft (NOT saved; admin reviews + saves)."""
    from . import persona as persona_mod
    return {"persona": persona_mod.generate_from_knowledge()}


@router.get("/settings/persona/history", dependencies=[Depends(require_key)])
def persona_history():
    """Prior persona versions (for display / future rollback)."""
    return {"history": appcfg.persona_history()}


@router.get("/contradictions", dependencies=[Depends(require_key)])
def list_contradictions(status: str = "pending"):
    """Semantic contradictions between docs (Karpathy-wiki Phase 1). status =
    pending | resolved | all. doc_a = the NEWER doc, doc_b = the existing one."""
    where = ""
    if status == "pending":
        where = "WHERE c.status = 'pending'"
    elif status == "resolved":
        where = "WHERE c.status <> 'pending'"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.entity, c.attribute, c.severity, c.detail, c.status, c.created_at, "
            "c.doc_a, c.page_a, c.value_a, c.doc_b, c.page_b, c.value_b, "
            "da.name AS doc_a_name, db.name AS doc_b_name "
            "FROM claim_conflict c "
            "LEFT JOIN docs da ON da.id = c.doc_a "
            "LEFT JOIN docs db ON db.id = c.doc_b "
            f"{where} ORDER BY c.created_at DESC LIMIT 500",
        ).fetchall()
        counts = conn.execute(
            "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, "
            "count(*) FILTER (WHERE status <> 'pending') AS resolved FROM claim_conflict"
        ).fetchone()
    return {"conflicts": rows, "counts": counts}


@router.get("/wiki/index", dependencies=[Depends(require_key)])
def wiki_index():
    """Browsable wiki landing (Karpathy Phase 3): top entities + doc catalog."""
    from . import wiki
    return wiki.wiki_index()


@router.get("/wiki/resolve", dependencies=[Depends(require_key)])
def wiki_resolve(name: str = ""):
    """Resolve an entity name (from an auto-link click) to its hub id."""
    from . import wiki
    eid = wiki.resolve_entity(name)
    if not eid:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"id": eid}


@router.get("/wiki/entity/{entity_id}", dependencies=[Depends(require_key)])
def wiki_entity(entity_id: int):
    """Entity hub: appears-in, asserted values (claims), conflicts, related."""
    from . import wiki
    hub = wiki.entity_hub(entity_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return hub


@router.get("/wiki/doc/{doc_id}", dependencies=[Depends(require_key)])
def wiki_doc(doc_id: int):
    """A document as a wiki page: auto-linked markdown + backlinks + prerequisites."""
    from . import wiki
    view = wiki.doc_view(doc_id)
    if view is None:
        raise HTTPException(status_code=404, detail="document not found")
    return view


@router.get("/claims/superseded", dependencies=[Depends(require_key)])
def superseded_claims():
    """Temporal history — claims invalidated by a resolved contradiction (kept for
    'what was the rule before the change'). Proves invalidate-don't-delete."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT dc.id, dc.entity, dc.attribute, dc.value, dc.valid_from, "
            "dc.superseded_at, dc.supersede_reason, dc.doc_id, d.name AS doc_name, "
            "dc.superseded_by_doc, w.name AS superseded_by_name "
            "FROM doc_claims dc "
            "LEFT JOIN docs d ON d.id = dc.doc_id "
            "LEFT JOIN docs w ON w.id = dc.superseded_by_doc "
            "WHERE dc.superseded_at IS NOT NULL ORDER BY dc.superseded_at DESC LIMIT 200"
        ).fetchall()
    return {"superseded": rows}


@router.post("/contradictions/{cid}/resolve")
def resolve_contradiction(cid: int, req: dict, user: dict = Depends(require_admin)):
    """Resolve a contradiction. choice = new | old | both | dismiss.

    Phase 2 (BITEMPORAL): on new/old, the LOSING claim is superseded
    (invalidate-don't-delete — superseded_at stamped, history stays queryable) and
    the WINNING value is promoted to an active fact, which overrides the docs in
    answers so the resolution actually takes effect."""
    from .config import BITEMPORAL
    choice = (req or {}).get("choice", "")
    status = {"new": "kept_new", "old": "kept_old", "both": "both",
              "dismiss": "dismissed"}.get(choice)
    if not status:
        raise HTTPException(status_code=400, detail="choice must be new|old|both|dismiss")
    with get_conn() as conn:
        c = conn.execute(
            "SELECT entity, attribute, claim_a, claim_b, doc_a, doc_b, value_a, value_b "
            "FROM claim_conflict WHERE id = %s", (cid,),
        ).fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="contradiction not found")
        conn.execute(
            "UPDATE claim_conflict SET status = %s, resolved_by = %s, resolved_at = now() "
            "WHERE id = %s", (status, user.get("email"), cid),
        )

    superseded = None
    promoted_fact = None
    if BITEMPORAL and choice in ("new", "old"):
        if choice == "new":
            loser, win_val, win_doc = c["claim_b"], c["value_a"], c["doc_a"]
        else:
            loser, win_val, win_doc = c["claim_a"], c["value_b"], c["doc_b"]
        # invalidate-don't-delete: stamp the losing claim as superseded
        if loser:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE doc_claims SET superseded_at = now(), superseded_by_doc = %s, "
                    "supersede_reason = %s WHERE id = %s AND superseded_at IS NULL",
                    (win_doc, f"contradiction #{cid} resolved ({status})", loser),
                )
            superseded = loser
        # promote the winning value to an ACTIVE fact → overrides docs in answers
        try:
            from . import memory as _mem
            fact = f"{c['entity']} {c['attribute']} = {win_val}".strip()
            promoted_fact = _mem.add_memory(
                fact, source="contradiction", created_by=user.get("email"),
                status="active")
        except Exception as e:
            print(f"[contradiction] promote-fact skipped: {e!r}")

    audit_mod.log(user, "contradiction.resolve", "conflict", cid,
                  {"choice": choice, "superseded_claim": superseded,
                   "promoted_fact": promoted_fact})
    return {"ok": True, "status": status, "superseded_claim": superseded,
            "promoted_fact": promoted_fact}


@router.post("/memory/approve-bulk")
def approve_bulk(req: dict, user: dict = Depends(require_knowledge_manager)):
    """Bulk-approve pending facts: by explicit ids, or all of a source, or all."""
    ids = req.get("ids")
    source = req.get("source")
    n = 0
    with get_conn() as conn:
        if ids:
            for i in ids:
                conn.execute("UPDATE memory SET status='active' WHERE id=%s AND status='pending'", (i,))
                n += 1
        elif source:
            r = conn.execute("UPDATE memory SET status='active' WHERE status='pending' AND source=%s", (source,))
            n = r.rowcount if hasattr(r, "rowcount") else 0
        else:
            r = conn.execute("UPDATE memory SET status='active' WHERE status='pending'")
            n = r.rowcount if hasattr(r, "rowcount") else 0
    audit_mod.log(user, "memory.approve_bulk", "memory", 0, {"n": n, "source": source})
    return {"ok": True, "approved": n}


@router.post("/memory/{mem_id}/approve", dependencies=[Depends(require_knowledge_manager)])
def approve_fact(mem_id: int, user: dict = Depends(require_knowledge_manager)):
    """Approve a pending (chat-learned) fact so it counts in answers."""
    ok = set_status(mem_id, "active")
    if not ok:
        raise HTTPException(status_code=404, detail="fact not found")
    notify.emit("memory", "Fact approved", f"#{mem_id} now active", "success")
    audit_mod.log(user, "fact.approve", "fact", mem_id)
    return {"ok": True, "id": mem_id, "status": "active"}


@router.post("/memory/{mem_id}/reject", dependencies=[Depends(require_knowledge_manager)])
def reject_fact(mem_id: int, user: dict = Depends(require_knowledge_manager)):
    """Reject a pending fact (keeps the row, marks it rejected)."""
    ok = set_status(mem_id, "rejected")
    if not ok:
        raise HTTPException(status_code=404, detail="fact not found")
    audit_mod.log(user, "fact.reject", "fact", mem_id)
    return {"ok": True, "id": mem_id, "status": "rejected"}


class MemoryEdit(BaseModel):
    key: str | None = None
    value: str


@router.patch("/memory/{mem_id}", dependencies=[Depends(require_knowledge_manager)])
def edit_fact(mem_id: int, body: MemoryEdit, user: dict = Depends(require_knowledge_manager)):
    """Edit a fact's key and/or value in place."""
    ok = update_memory(mem_id, body.key, body.value)
    if not ok:
        raise HTTPException(status_code=404, detail="fact not found or empty value")
    audit_mod.log(user, "fact.edit", "fact", mem_id, {"value": (body.value or "")[:120]})
    return {"ok": True, "id": mem_id}


# ---------------- Q&A bank (auto-mined question/answer pairs) ----------------
@router.get("/qa", dependencies=[Depends(require_key)])
def get_qa(status: str | None = None, limit: int = 200):
    """List Q&A pairs. Optional ?status=active|pending|rejected. Returns status
    counts so the review queue can badge pending pairs."""
    from . import qa as qa_mod
    return {"qa": qa_mod.list_qa(limit=limit, status=status),
            "counts": qa_mod.qa_counts(),
            "pending": qa_mod.pending_qa_count()}


@router.post("/qa/{qa_id}/approve", dependencies=[Depends(require_knowledge_manager)])
def approve_qa(qa_id: int, user: dict = Depends(require_knowledge_manager)):
    """Approve a pending Q&A pair so it can serve/seed answers."""
    from . import qa as qa_mod
    if not qa_mod.set_qa_status(qa_id, "active"):
        raise HTTPException(status_code=404, detail="qa pair not found")
    audit_mod.log(user, "qa.approve", "qa", qa_id)
    return {"ok": True, "id": qa_id, "status": "active"}


@router.post("/qa/{qa_id}/reject", dependencies=[Depends(require_knowledge_manager)])
def reject_qa(qa_id: int, user: dict = Depends(require_knowledge_manager)):
    """Reject a pending Q&A pair (keeps the row, marks it rejected)."""
    from . import qa as qa_mod
    if not qa_mod.set_qa_status(qa_id, "rejected"):
        raise HTTPException(status_code=404, detail="qa pair not found")
    audit_mod.log(user, "qa.reject", "qa", qa_id)
    return {"ok": True, "id": qa_id, "status": "rejected"}


@router.post("/qa/approve-bulk")
def approve_qa_bulk(req: dict, user: dict = Depends(require_knowledge_manager)):
    """Bulk-approve pending Q&A pairs: by explicit ids, or all pending."""
    ids = req.get("ids")
    n = 0
    with get_conn() as conn:
        if ids:
            for i in ids:
                conn.execute("UPDATE qa_pairs SET status='active' WHERE id=%s AND status='pending'", (i,))
                n += 1
        else:
            r = conn.execute("UPDATE qa_pairs SET status='active' WHERE status='pending'")
            n = r.rowcount if hasattr(r, "rowcount") else 0
    audit_mod.log(user, "qa.approve_bulk", "qa", 0, {"n": n})
    return {"ok": True, "approved": n}


class QaEdit(BaseModel):
    question: str | None = None
    answer: str | None = None


@router.patch("/qa/{qa_id}", dependencies=[Depends(require_knowledge_manager)])
def edit_qa(qa_id: int, body: QaEdit, user: dict = Depends(require_knowledge_manager)):
    """Edit a Q&A pair's question and/or answer in place."""
    from . import qa as qa_mod
    if not qa_mod.update_qa(qa_id, body.question, body.answer):
        raise HTTPException(status_code=404, detail="qa pair not found or empty")
    audit_mod.log(user, "qa.edit", "qa", qa_id)
    return {"ok": True, "id": qa_id}


@router.delete("/qa/{qa_id}", dependencies=[Depends(require_knowledge_manager)])
def delete_qa_pair(qa_id: int, user: dict = Depends(require_knowledge_manager)):
    """Delete a Q&A pair permanently."""
    from . import qa as qa_mod
    if not qa_mod.delete_qa(qa_id):
        raise HTTPException(status_code=404, detail="qa pair not found")
    audit_mod.log(user, "qa.delete", "qa", qa_id)
    return {"ok": True, "id": qa_id}


@router.post("/qa/gap-fill")
def qa_gap_fill(user: dict = Depends(require_knowledge_manager)):
    """Run one gap-driven Q&A fill pass now (Phase 3, admin). Honors the per-day
    cap; returns how many grounded pending pairs were created."""
    from . import auto_qa_gaps
    n = auto_qa_gaps.run_gap_fill()
    audit_mod.log(user, "qa.gap_fill", "qa", 0, {"saved": n})
    return {"ok": True, "saved": n}


# ---------------- Coverage Audit + weekly digest (cadence) ----------------
@router.get("/audit/coverage", dependencies=[Depends(require_key)])
def audit_coverage(days: int = 30):
    """Self-audit: blind spots, stale facts, downvotes, leverage-ranked gaps."""
    return audit_mod.coverage_report(days=days)


@router.get("/digest/latest", dependencies=[Depends(require_key)])
def digest_latest():
    return {"digest": digest_mod.latest()}


@router.post("/digest/run", dependencies=[Depends(require_admin)])
def digest_run(force: bool = True):
    """Build + emit a weekly digest now (admin). force=True ignores the interval."""
    return digest_mod.run_now(force=force)


@router.get("/dream/latest", dependencies=[Depends(require_key)])
def dream_latest():
    """Most recent dream-cycle (nightly self-improvement) run: stats + touched ids."""
    return {"dream": dream_mod.latest()}


@router.get("/dream/runs", dependencies=[Depends(require_key)])
def dream_runs(limit: int = 10):
    """Recent dream-cycle runs (newest first) for the Brain-health UI."""
    return {"runs": dream_mod.recent_runs(limit=limit)}


@router.get("/dream/config", dependencies=[Depends(require_admin)])
def dream_config_get():
    """Effective dream-cycle config (DB over env)."""
    return appcfg.get_dream()


@router.post("/dream/config", dependencies=[Depends(require_admin)])
def dream_config_save(patch: dict = Body(...)):
    """Update dream-cycle config live (admin). Flags apply on the next run."""
    return appcfg.save_dream(patch)


@router.post("/dream/run", dependencies=[Depends(require_admin)])
def dream_run(force: bool = True):
    """Run one dream cycle now (admin): dedup/promote/retire facts, resolve
    conflicts, score salience, auto-link entities, fill gaps. force ignores the
    interval gate."""
    return dream_mod.run_dream(force=force)


@router.get("/ingest/log", dependencies=[Depends(require_key)])
def ingest_log(after: int = 0, limit: int = 60):
    """Live per-step ingest/activity log for the floating robot UI.

    after=0 → most recent `limit` rows (history on first poll). after>0 →
    incremental rows WHERE id > after. cursor = max id returned (or `after` if
    the batch is empty) — client passes it back as ?after=<cursor>.
    """
    limit = max(1, min(int(limit or 60), 200))
    with get_conn() as conn:
        if after and after > 0:
            rows = conn.execute(
                "SELECT l.id, l.doc_id, l.ts, l.step, l.msg, l.level, d.name AS doc_name "
                "FROM ingest_log l LEFT JOIN docs d ON d.id = l.doc_id "
                "WHERE l.id > %s ORDER BY l.id ASC LIMIT %s",
                (after, limit),
            ).fetchall()
        else:
            # newest `limit`, then re-order ascending so the UI appends in order
            rows = conn.execute(
                "SELECT * FROM ("
                "  SELECT l.id, l.doc_id, l.ts, l.step, l.msg, l.level, d.name AS doc_name "
                "  FROM ingest_log l LEFT JOIN docs d ON d.id = l.doc_id "
                "  ORDER BY l.id DESC LIMIT %s"
                ") s ORDER BY s.id ASC",
                (limit,),
            ).fetchall()
    lines = [
        {
            "id": r["id"],
            "doc_id": r["doc_id"],
            "doc_name": _friendly_doc(r["doc_name"]) if r.get("doc_name") else None,
            "ts": r["ts"].isoformat() if r.get("ts") else None,
            "step": r["step"],
            "msg": r["msg"],
            "level": r["level"] or "info",
        }
        for r in rows
    ]
    cursor = lines[-1]["id"] if lines else (after if after and after > 0 else 0)
    return {"lines": lines, "cursor": cursor}


@router.get("/ingest/active", dependencies=[Depends(require_key)])
def ingest_active():
    """The doc currently being processed + its progress ({} if none)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, status, progress, pages_done, page_count "
            "FROM docs WHERE status = 'processing' ORDER BY id LIMIT 1"
        ).fetchone()
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": _friendly_doc(row["name"]) if row.get("name") else None,
        "status": row["status"],
        "progress": row["progress"],
        "pages_done": row["pages_done"],
        "page_count": row["page_count"],
    }


@router.get("/documents", dependencies=[Depends(require_key)])
def list_docs(limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0),
              folder_id: int | None = Query(None), deep: bool = Query(True),
              user: dict = Depends(current_user)):
    # Paginated + bounded. Was an unbounded scan of every doc (+ per-doc
    # subqueries + a full messages-usage GROUP BY) on every poll. Returns `total`
    # so the client can page. Defaults (500/0) keep existing callers working.
    _sectors, _folders = rbac.scope_filter(user)   # row-level: (None,None)=all
    _sec = (" AND (%(folders)s::bigint[] IS NULL OR d.folder_id = ANY(%(folders)s) "
            "      OR (d.folder_id IS NULL AND d.sector_id = ANY(%(sectors)s))) ")
    if folder_id == 0:                      # Sources: "Unfiled" (no folder)
        _sec += " AND d.folder_id IS NULL "
    elif folder_id is not None and deep:    # Sources: this folder + ALL descendants
        _sec += (" AND d.folder_id IN ("
                 "   WITH RECURSIVE sub(id) AS ("
                 "     SELECT %(folder)s::bigint "
                 "     UNION ALL SELECT c.id FROM folders c JOIN sub ON c.parent_id = sub.id"
                 "   ) SELECT id FROM sub) ")
    elif folder_id is not None:             # Sources: exact folder only (deep=false)
        _sec += " AND d.folder_id = %(folder)s "
    with get_conn() as conn:
        total = conn.execute(
            "SELECT count(*) AS n FROM docs d WHERE true" + _sec,
            {"sectors": _sectors, "folders": _folders, "folder": folder_id}).fetchone()["n"]
        rows = conn.execute(
            "SELECT d.id, d.name, d.lang, d.page_count, d.created_at, d.category, "
            "d.status, d.progress, d.pages_done, d.error, d.ready_at, "
            "d.uploaded_by, d.updated_at, d.doc_type, d.folder_id, "
            "(SELECT fo.name FROM folders fo WHERE fo.id = d.folder_id) AS folder_name, "
            "(SELECT count(*) FROM doc_playbook pb WHERE pb.doc_id = d.id) AS has_playbook, "
            "(SELECT count(*) FROM nodes n WHERE n.doc_id = d.id) AS sections, "
            "(SELECT count(*) FROM pages p WHERE p.doc_id = d.id AND coalesce(p.vision_text,'') <> '') AS vision_pages, "
            "(SELECT count(*) FROM pages p WHERE p.doc_id = d.id AND coalesce(p.text_layer,'') <> '') AS text_pages, "
            "(SELECT p.id FROM pages p WHERE p.doc_id = d.id ORDER BY p.page_no, p.id LIMIT 1) AS cover_page_id "
            "FROM docs d WHERE true" + _sec + " ORDER BY d.id DESC LIMIT %(lim)s OFFSET %(off)s",
            {"sectors": _sectors, "folders": _folders, "folder": folder_id, "lim": limit, "off": offset},
        ).fetchall()
        # per-doc usage (how often the doc answered a question), SCOPED to the
        # page of docs we're returning so this doesn't scan all messages per doc.
        doc_ids = [r["id"] for r in rows]
        usage = []
        if doc_ids:
            usage = conn.execute(
                "SELECT p.doc_id, count(DISTINCT m.id) AS used_count, max(m.created_at) AS last_used_at "
                "FROM messages m "
                "JOIN jsonb_array_elements(m.pages) e ON true "
                "JOIN pages p ON p.id = (e->>'page_id')::int "
                "WHERE m.role = 'bot' AND p.doc_id = ANY(%s) "
                "GROUP BY p.doc_id",
                (doc_ids,),
            ).fetchall()
    umap = {u["doc_id"]: u for u in usage}
    for r in rows:
        u = umap.get(r["id"])
        r["used_count"] = u["used_count"] if u else 0
        r["last_used_at"] = u["last_used_at"] if u else None
    return {"docs": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/documents/{doc_id}/images", dependencies=[Depends(require_key)])
def doc_images_list(doc_id: int):
    """Per-embedded-image detailed explanations (screen/shows/element/action)."""
    from . import doc_images
    return {"images": doc_images.images_for(doc_id)}


@router.post("/documents/images/backfill", dependencies=[Depends(require_content_manager)])
def doc_images_backfill(user: dict = Depends(current_user)):
    """Explain embedded screenshots for all ready docs from their stored PDF
    (no re-ingest). Runs in the background (vision per image is slow); poll
    GET /documents/{id}/images for results."""
    import threading
    from . import doc_images
    threading.Thread(target=doc_images.backfill_all, daemon=True).start()
    try:
        audit_mod.log(user, "doc_images.backfill", "doc", None, {})
    except Exception:
        pass
    return {"started": True}


@router.get("/doc-images/{image_id}")
def doc_image_serve(image_id: int):
    """Serve one extracted screenshot PNG (public, like page images)."""
    with get_conn() as conn:
        row = conn.execute("SELECT image_path FROM doc_image WHERE id = %s",
                           (image_id,)).fetchone()
    if not row or not row["image_path"]:
        raise HTTPException(status_code=404, detail="image not found")
    path = row["image_path"]
    import os as _os
    if not _os.path.exists(path):
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/documents/{doc_id}/playbook", dependencies=[Depends(require_key)])
def doc_playbook(doc_id: int):
    """User-facing playbook for a doc (goal/who/prereqs/steps/verify/if-fails/escalate).
    Returns {playbook: null} when the doc has none compiled yet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT goal, who, prerequisites, steps, verify, if_fails, escalate, "
            "chars, created_at FROM doc_playbook WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()
    return {"playbook": row}


@router.get("/documents/{doc_id}/dependencies", dependencies=[Depends(require_key)])
def doc_dependencies(doc_id: int):
    """Prerequisite documents for this doc (procedure chaining, Phase 5)."""
    from . import dependencies as deps_mod
    return {"dependencies": deps_mod.dependencies_for(doc_id)}


@router.get("/documents/{doc_id}/tree", dependencies=[Depends(require_key)])
def doc_tree(doc_id: int):
    """Interactive troubleshooting decision tree for a doc (Phase 6.2).
    Returns {tree: null} when the doc has no branching tree compiled."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tree FROM doc_tree WHERE doc_id = %s", (doc_id,)
        ).fetchone()
    return {"tree": (row or {}).get("tree") if row else None}


@router.get("/documents/{doc_id}/lookup", dependencies=[Depends(require_key)])
def doc_lookup_rows(doc_id: int):
    """Reference-doc lookup table (term -> value + page) for the Brain reader."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT term, value, page_id FROM doc_lookup WHERE doc_id = %s "
            "ORDER BY term", (doc_id,),
        ).fetchall()
    return {"lookup": rows}


# ---- cross-doc entity index (Phase 3) ----
@router.get("/entities", dependencies=[Depends(require_key)])
def list_entities(q: str = "", limit: int = 200):
    """Entities ranked by how many docs mention them (cross-doc connectors first)."""
    limit = max(1, min(int(limit or 200), 1000))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT e.id, e.name, e.kind, "
            "count(DISTINCT m.doc_id) AS doc_count "
            "FROM entities e LEFT JOIN entity_mention m ON m.entity_id = e.id "
            "WHERE (%s = '' OR e.name ILIKE '%%' || %s || '%%') "
            "GROUP BY e.id, e.name, e.kind "
            "ORDER BY doc_count DESC, e.name ASC LIMIT %s",
            (q, q, limit),
        ).fetchall()
    return {"entities": rows}


@router.get("/entities/{entity_id}", dependencies=[Depends(require_key)])
def entity_detail(entity_id: int):
    """One entity + every document (and page) that mentions it."""
    with get_conn() as conn:
        ent = conn.execute(
            "SELECT id, name, kind FROM entities WHERE id = %s", (entity_id,)
        ).fetchone()
        docs = conn.execute(
            "SELECT m.doc_id, d.name AS doc_name, m.page_id "
            "FROM entity_mention m JOIN docs d ON d.id = m.doc_id "
            "WHERE m.entity_id = %s ORDER BY d.name", (entity_id,),
        ).fetchall()
    return {"entity": ent, "mentions": docs}


# ---- knowledge-base health: conflicts + freshness (Phase 4) ----
@router.get("/kb/conflicts", dependencies=[Depends(require_key)])
def kb_conflicts(status: str = "open"):
    from . import kb_lint
    return {"conflicts": kb_lint.list_conflicts(status)}


@router.post("/kb/lint")
def kb_lint_run(user: dict = Depends(require_admin)):
    from . import kb_lint
    return {"conflicts": kb_lint.run_lint()}


@router.post("/kb/conflicts/{conflict_id}/dismiss")
def kb_conflict_dismiss(conflict_id: int, user: dict = Depends(require_admin)):
    from . import kb_lint
    return {"ok": kb_lint.dismiss_conflict(conflict_id)}


@router.get("/kb/stale", dependencies=[Depends(require_key)])
def kb_stale(days: int | None = None):
    from . import kb_lint
    return {"stale": kb_lint.stale_docs(days)}


# ---- master catalog: "what Aria can help with" (Phase 7) ----
@router.get("/catalog", dependencies=[Depends(require_key)])
def catalog():
    from . import catalog as catalog_mod
    return catalog_mod.build_catalog()


@router.get("/documents/{doc_id}", dependencies=[Depends(require_key)])
def doc_detail(doc_id: int):
    """Doc meta + extraction stats + PageIndex outline (for the Brain detail view)."""
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT id, name, lang, page_count, created_at FROM docs WHERE id = %s",
            (doc_id,),
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        stats = conn.execute(
            "SELECT (SELECT count(*) FROM pages WHERE doc_id = %(d)s) AS pages, "
            "(SELECT count(*) FROM pages WHERE doc_id = %(d)s AND coalesce(vision_text,'') <> '') AS vision_pages, "
            "(SELECT count(*) FROM pages WHERE doc_id = %(d)s AND coalesce(text_layer,'') <> '') AS text_pages, "
            "(SELECT count(*) FROM nodes WHERE doc_id = %(d)s) AS sections",
            {"d": doc_id},
        ).fetchone()
        outline = conn.execute(
            "SELECT page_no, title, summary FROM nodes WHERE doc_id = %s "
            "ORDER BY page_no, id",
            (doc_id,),
        ).fetchall()
        usage = conn.execute(
            "SELECT count(DISTINCT m.id) AS used_count, max(m.created_at) AS last_used_at "
            "FROM messages m "
            "JOIN jsonb_array_elements(m.pages) e ON true "
            "JOIN pages p ON p.id = (e->>'page_id')::int "
            "WHERE m.role = 'bot' AND p.doc_id = %s",
            (doc_id,),
        ).fetchone()
        # weak pages: vision model produced no text for these page numbers
        weak_rows = conn.execute(
            "SELECT page_no FROM pages "
            "WHERE doc_id = %s AND coalesce(vision_text,'') = '' "
            "ORDER BY page_no",
            (doc_id,),
        ).fetchall()
        # per-page extraction sizes (text layer vs vision), sorted by page_no
        char_rows = conn.execute(
            "SELECT page_no, "
            "length(coalesce(text_layer,'')) AS text, "
            "length(coalesce(vision_text,'')) AS vision "
            "FROM pages WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
        # votes on bot answers that cite ANY page of this doc.
        # message_feedback only stores conversation_id + answer text (no message_id,
        # no page ids), so we re-join feedback -> the matching bot message
        # (same conversation_id + identical answer text) -> its cited pages -> doc.
        votes = conn.execute(
            "SELECT "
            "  count(*) FILTER (WHERE f.vote = 'up')   AS up, "
            "  count(*) FILTER (WHERE f.vote = 'down') AS down "
            "FROM message_feedback f "
            "WHERE EXISTS ("
            "  SELECT 1 FROM messages m "
            "  JOIN jsonb_array_elements(m.pages) e ON true "
            "  JOIN pages p ON p.id = (e->>'page_id')::int "
            "  WHERE m.role = 'bot' AND p.doc_id = %s "
            "    AND m.conversation_id = f.conversation_id "
            "    AND m.text = f.answer"
            ")",
            (doc_id,),
        ).fetchone()
    stats["has_tree"] = stats["sections"] > 0
    stats["lang"] = doc["lang"]
    stats["used_count"] = (usage or {}).get("used_count") or 0
    stats["last_used_at"] = (usage or {}).get("last_used_at")
    stats["weak_pages"] = [r["page_no"] for r in weak_rows]
    stats["page_chars"] = [
        {"page_no": r["page_no"], "text": r["text"], "vision": r["vision"]}
        for r in char_rows
    ]
    stats["votes"] = {
        "up": int((votes or {}).get("up") or 0),
        "down": int((votes or {}).get("down") or 0),
    }
    return {"doc": doc, "stats": stats, "outline": outline}


@router.get("/documents/{doc_id}/text", dependencies=[Depends(require_key)])
def doc_text(doc_id: int):
    """Every page's extracted text (text_layer + vision_text) — Full-text tab + export."""
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM docs WHERE id = %s", (doc_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="document not found")
        pages = conn.execute(
            "SELECT page_no, text_layer, vision_text FROM pages "
            "WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
    return {"pages": pages}


@router.get("/documents/{doc_id}/pages", dependencies=[Depends(require_key)])
def doc_pages(doc_id: int):
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT id, name, lang, page_count FROM docs WHERE id = %s", (doc_id,)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        pages = conn.execute(
            "SELECT id AS page_id, page_no FROM pages WHERE doc_id = %s "
            "ORDER BY page_no",
            (doc_id,),
        ).fetchall()
    return {"doc": doc, "pages": pages}


@router.delete("/documents/{doc_id}", dependencies=[Depends(require_key)])
def delete_doc(doc_id: int, user: dict = Depends(current_user)):
    """Delete a document + its pages + nodes (cascade) + page images.

    Ownership: super-admin (any doc), the uploader (own doc), or a sector/folder
    admin of the doc's sector — see rbac.can_delete_doc. Others get 403."""
    with get_conn() as conn:
        name = conn.execute(
            "SELECT name, uploaded_by, sector_id FROM docs WHERE id = %s", (doc_id,)
        ).fetchone()
        if not name:
            raise HTTPException(status_code=404, detail="document not found")
        if not (rbac.can_delete_doc(user, name) or rbac.can_manage_content(user)):
            raise HTTPException(
                status_code=403,
                detail="only the uploader or an admin can delete this document")
        imgs = conn.execute(
            "SELECT image_path FROM pages WHERE doc_id = %s", (doc_id,)
        ).fetchall()
        # qa_pairs has NO FK cascade → orphaned pairs would survive deletion and get
        # served as stale/unsourced cache answers (dangling page_ids). Purge them +
        # any claim_conflict rows referencing this doc (also FK-less).
        conn.execute("DELETE FROM qa_pairs WHERE doc_id = %s", (doc_id,))
        conn.execute("DELETE FROM claim_conflict WHERE doc_a = %s OR doc_b = %s", (doc_id, doc_id))
        row = conn.execute(
            "DELETE FROM docs WHERE id = %s RETURNING id", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="document not found")
    for r in imgs:
        p = r["image_path"] or ""
        try:
            if p.startswith("s3:"):
                from . import s3client
                s3client.delete(p[3:])
            else:
                Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    audit_mod.log(user, "doc.delete", "doc", doc_id, {"name": (name or {}).get("name")})
    return {"ok": True, "deleted": doc_id}


@router.delete("/memory/{mem_id}", dependencies=[Depends(require_knowledge_manager)])
def delete_memory(mem_id: int, user: dict = Depends(require_knowledge_manager)):
    with get_conn() as conn:
        conn.execute("DELETE FROM memory WHERE id = %s", (mem_id,))
    audit_mod.log(user, "fact.delete", "fact", mem_id)
    return {"ok": True, "deleted": mem_id}


@router.get("/usage", dependencies=[Depends(require_key)])
def usage():
    """Lightweight stats for the admin screen."""
    with get_conn() as conn:
        s = conn.execute(
            "SELECT (SELECT count(*) FROM docs) AS docs, "
            "(SELECT count(*) FROM pages) AS pages, "
            "(SELECT count(*) FROM pages WHERE vision_text <> '') AS vision_pages, "
            "(SELECT count(*) FROM nodes) AS sections, "
            "(SELECT count(*) FROM memory) AS facts, "
            "(SELECT count(*) FROM messages WHERE role = 'user') AS questions"
        ).fetchone()
        # recent = last user questions paired with the bot reply
        recent = conn.execute(
            "SELECT u.text AS q, left(b.text, 160) AS answer, u.created_at, b.pages "
            "FROM messages u "
            "LEFT JOIN LATERAL ("
            "  SELECT text, pages FROM messages b WHERE b.conversation_id = u.conversation_id "
            "  AND b.role = 'bot' AND b.id > u.id ORDER BY b.id LIMIT 1"
            ") b ON true "
            "WHERE u.role = 'user' ORDER BY u.id DESC LIMIT 10"
        ).fetchall()
    # derive a short "source" label from the first cited page of each answer
    for r in recent:
        pages = r.pop("pages", None) or []
        src = None
        if isinstance(pages, list) and pages:
            first = pages[0] or {}
            name = _friendly_doc(first.get("doc_name") or "") or (first.get("doc_name") or "")
            pno = first.get("page_no")
            if name and pno is not None:
                src = f"{name} · p.{pno}"
            elif name:
                src = name
        r["source"] = src
    return {"stats": s, "recent": recent}


@router.get("/dashboard/me", dependencies=[Depends(require_key)])
def dashboard_me(days: int = 30, user: dict = Depends(current_user)):
    """Personal dashboard — scoped to the caller's own conversations / votes /
    taught facts. Available to every authenticated user."""
    return dashboard_mod.personal(user["id"], user.get("name"), user.get("email"), days)


@router.get("/dashboard/admin", dependencies=[Depends(require_admin)])
def dashboard_admin(days: int = 30):
    """Org-wide cockpit — admin only."""
    return dashboard_mod.org(days)


@router.get("/dashboard/graph/me", dependencies=[Depends(require_key)])
def dashboard_graph_me(user: dict = Depends(current_user)):
    """Personal ego knowledge map — scoped to the caller."""
    return dashboard_mod.graph_me(user["id"], user.get("name"), user.get("email"))


@router.get("/dashboard/graph/people", dependencies=[Depends(require_admin)])
def dashboard_graph_people():
    """Org who-knows-what bipartite (users ↔ docs) — admin only."""
    return dashboard_mod.graph_people()


@router.get("/dashboard/keywords", dependencies=[Depends(require_admin)])
def dashboard_keywords(days: int = 30):
    """Privacy-safe keyword/topic analysis of all questions — NO raw chat text.
    Aggregate counts only, rare terms dropped (k-anonymity). Admin only."""
    return keywords_mod.keyword_analysis(days)


@router.get("/dashboard/cockpit", dependencies=[Depends(require_admin)])
def dashboard_cockpit(days: int = 30):
    """Command-center rollup — alerts, health rings, KPIs, trends. Admin only."""
    usage_rollup.ensure(days)
    return dashboard_mod.cockpit(days)


@router.get("/dashboard/vitals", dependencies=[Depends(require_admin)])
def dashboard_vitals():
    """Live system vitals (DB size, ingest queue, daemon liveness, active-now)."""
    return dashboard_mod.vitals()


@router.get("/ops/answers/recent", dependencies=[Depends(require_admin)])
def ops_answers_recent(limit: int = 20):
    """Live feed of the most recent answers for the Operations Cockpit — per-answer
    latency, cache-hit, retrieval funnel (seed/wide/cited) and the question text.
    Pure reads from answer_metrics ⋈ messages. Admin only, fail-soft."""
    limit = max(1, min(limit, 100))
    out = []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT am.created_at, am.message_id, am.model, am.mode, am.ms_total, "
                "       am.cache_hit, am.cited_n, am.seed_n, am.wide_n, am.wider_fired, "
                "       am.blind, am.scanned, am.pool, am.reranked, m.text AS answer, "
                "       (SELECT um.text FROM messages um "
                "         WHERE um.conversation_id = am.conversation_id "
                "           AND um.role='user' AND um.id < am.message_id "
                "         ORDER BY um.id DESC LIMIT 1) AS question "
                "FROM answer_metrics am "
                "LEFT JOIN messages m ON m.id = am.message_id "
                "ORDER BY am.created_at DESC LIMIT %s",
                (limit,)).fetchall()
        for r in rows:
            seed = int(r.get("seed_n") or 0)
            wide = int(r.get("wide_n") or 0)
            cited = int(r.get("cited_n") or 0)
            # real funnel columns when present; fall back to seed/wide proxies
            # for legacy rows (and cache serves, which never run retrieval).
            scanned = r["scanned"] if r.get("scanned") is not None else (seed + wide)
            pool = r["pool"] if r.get("pool") is not None else scanned
            reranked = (r["reranked"] if r.get("reranked") is not None
                        else (seed if seed else scanned))
            out.append({
                "ts": r["created_at"].isoformat() if r.get("created_at") else None,
                "q": (r.get("question") or "").strip()[:240],
                "mode": r.get("mode") or "—",
                "model": (r.get("model") or "—").split("/")[-1].split(":")[0],
                "ms": int(r.get("ms_total") or 0),
                "cache_hit": bool(r.get("cache_hit")),
                "cited": cited,
                "scanned": int(scanned or 0),
                "pool": int(pool or 0),
                "reranked": int(reranked or 0),
                "blind": bool(r.get("blind")),
                "wider": bool(r.get("wider_fired")),
            })
    except Exception:
        pass
    return {"answers": out}


@router.get("/documents/{doc_id}/log", dependencies=[Depends(require_admin)])
def document_log(doc_id: int, limit: int = 200):
    """Per-document ingest event timeline for the Cockpit ingest panel. Reads the
    ingest_events table oldest-first (so it reads as a timeline). Admin only,
    fail-soft (returns {events:[]} on any error)."""
    limit = max(1, min(limit, 1000))
    events = []
    try:
        with get_conn() as conn:
            # newest `limit` rows, then re-order oldest-first for display
            rows = conn.execute(
                "SELECT ts, stage, msg FROM ingest_events "
                "WHERE doc_id = %s ORDER BY id DESC LIMIT %s",
                (doc_id, limit)).fetchall()
        for r in reversed(rows):
            events.append({
                "ts": r["ts"].isoformat() if r.get("ts") else None,
                "stage": r.get("stage") or "",
                "msg": r.get("msg") or "",
            })
    except Exception:
        pass
    return {"events": events}


@router.get("/dashboard/activity-heatmap", dependencies=[Depends(require_admin)])
def dashboard_activity_heatmap(days: int = 30):
    """Question volume by weekday × hour (counts only — privacy-safe)."""
    from . import analytics as analytics_mod
    return analytics_mod.activity_heatmap(days)


# ---------------- usage analytics (management + per-user monitor) ----------------
@router.get("/analytics/management", dependencies=[Depends(require_admin)])
def analytics_management(days: int = 30):
    """Executive usage scorecard — adoption/engagement/quality/value/cost. Admin."""
    usage_rollup.ensure(days)
    return analytics_mod.management(days)


@router.get("/insights/overview", dependencies=[Depends(require_admin)])
def insights_overview(days: int = 30,
                      date_from: str | None = Query(None, alias="from"),
                      date_to: str | None = Query(None, alias="to")):
    """Productivity / CEO analytics — totals, trends, domains, departments,
    funnel, gaps, people. Pure reads, fail-soft (see app/productivity.py)."""
    from . import productivity
    days = max(1, min(days, 365))
    return productivity.overview(days=days, date_from=date_from, date_to=date_to)


@router.get("/insights/people.xlsx", dependencies=[Depends(require_admin)])
def insights_people_xlsx(days: int = 30,
                         date_from: str | None = Query(None, alias="from"),
                         date_to: str | None = Query(None, alias="to")):
    """All-users productivity workbook (summary + per-department sheets)."""
    from fastapi.responses import Response
    from . import insights_export
    days = max(1, min(days, 365))
    try:
        data = insights_export.people_xlsx(days=days, date_from=date_from, date_to=date_to)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    fname = f"aria-people-{date_from or ''}-{date_to or ''}".strip("-") if date_from else f"aria-people-{days}d"
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})


@router.get("/analytics/perf", dependencies=[Depends(require_admin)])
def analytics_perf(days: int = 30):
    """Performance scorecard — latency p50/p95, real token spend, retrieval
    hit-rate + wider-retry rate, model split, slowest answers. Admin only."""
    return analytics_mod.perf(days)


@router.get("/admin/audit", dependencies=[Depends(require_admin)])
def admin_audit(days: int = 30, action: str = "", page: int = 1, page_size: int = 50):
    """Governance trail — who approved/rejected/edited/deleted facts, deleted or
    retried docs, changed config. Admin only."""
    return audit_mod.recent(days, action, page, page_size)


@router.get("/analytics/docs", dependencies=[Depends(require_admin)])
def analytics_docs(days: int = 30):
    """Per-document scorecard — cites, helpful%, votes, age, indexed%, plus
    orphan (never cited) and cold (going stale) detection. Admin only."""
    return analytics_mod.doc_performance(days)


@router.get("/analytics/doc-eval", dependencies=[Depends(require_admin)])
def analytics_doc_eval():
    """Latest per-doc UAT accuracy run (grounded/right-doc/page-hit/faithful per
    doc) + a `running` flag while a run is in flight. Admin only."""
    from . import doc_eval
    return doc_eval.latest()


@router.post("/analytics/doc-eval/run", dependencies=[Depends(require_admin)])
def analytics_doc_eval_run(max_q: int = 6, user: dict = Depends(current_user)):
    """Kick off a background per-doc accuracy eval over each doc's golden Q&A.
    Returns immediately; poll GET /analytics/doc-eval for the result."""
    from . import doc_eval
    started = doc_eval.start_eval(max_q)
    try:
        audit_mod.log(user, "doc_eval.run", "eval", None)
    except Exception:
        pass
    return {"started": started, "running": True}


@router.get("/analytics/selfheal", dependencies=[Depends(require_admin)])
def selfheal_status():
    """Self-Heal agent status: per-doc queue, accuracy climb, banked + review."""
    from . import selfheal
    return selfheal.status()


@router.get("/analytics/selfheal/logs", dependencies=[Depends(require_admin)])
def selfheal_logs(doc_id: int | None = None, after: int = 0):
    """Live activity log (mine/eval/heal/judge/bank) for the Self-Heal panel."""
    from . import selfheal
    return {"lines": selfheal.logs(doc_id, after)}


@router.post("/analytics/selfheal/run", dependencies=[Depends(require_admin)])
def selfheal_run(doc_id: int | None = None, user: dict = Depends(current_user)):
    """Kick the Self-Heal loop (one doc, or all eligible ready docs if omitted)."""
    from . import selfheal
    started = selfheal.start_selfheal(doc_id)
    try:
        audit_mod.log(user, "selfheal.run", "doc", doc_id)
    except Exception:
        pass
    return {"started": started}


def _dash_version() -> int:
    """Cheap monotonically-growing cursor over the tables a dashboard reflects.
    Changes whenever a new answer / notification / metric lands."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT (SELECT coalesce(max(id),0) FROM messages) "
            "     + (SELECT coalesce(max(id),0) FROM notifications) "
            "     + (SELECT coalesce(max(id),0) FROM answer_metrics) AS v"
        ).fetchone()
    return int((r or {}).get("v") or 0)


@router.get("/stream/dashboard")
async def stream_dashboard(user: dict = Depends(require_admin)):
    """Server-Sent Events: pushes a 'data:' tick whenever the dashboard cursor
    changes (≈3s latency) so live charts update on real activity instead of a
    blind 15s poll. Heartbeat comment keeps the connection warm."""
    import asyncio

    async def gen():
        last = _dash_version()
        yield "retry: 5000\n\n"
        while True:
            await asyncio.sleep(3)
            try:
                v = _dash_version()
            except Exception:
                continue
            if v != last:
                last = v
                yield f"data: {v}\n\n"
            else:
                yield ": ping\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/analytics/low-accuracy", dependencies=[Depends(require_admin)])
def analytics_low_accuracy(threshold: int = 70, days: int = 30):
    """Verified answers scoring below threshold — the Review fix-list (closes the
    verify loop: measured accuracy now becomes actionable work)."""
    from . import verify as verify_mod
    return verify_mod.low_accuracy(threshold, days)


@router.post("/answer/verify/{message_id}", dependencies=[Depends(require_key)])
def answer_verify_one(message_id: int):
    """On-demand citation check for ONE chat answer — judges whether each cited
    page actually backs the answer, returns a trust score %. Any signed-in user."""
    from . import verify as verify_mod
    return verify_mod.verify_message(message_id)


@router.get("/analytics/verify", dependencies=[Depends(require_admin)])
def analytics_verify(days: int = 30):
    """Citation-accuracy report — avg trust score, support/refute rate, weakest
    answers, unverified backlog. Admin only."""
    return analytics_mod.verify_report(days)


@router.get("/ops", dependencies=[Depends(require_admin)])
def ops_report():
    """System health — ingest failure rate + stuck docs, daemon liveness, DB
    growth. Pure reads, no LLM. Admin only."""
    from . import ops as ops_mod
    return ops_mod.report()


@router.get("/analytics/engagement", dependencies=[Depends(require_admin)])
def analytics_engagement(days: int = 30):
    """Session depth, follow-up rate, D1/D7/D30 retention, dormant users. Admin."""
    usage_rollup.ensure(days)
    return analytics_mod.engagement(days)


@router.get("/analytics/learning", dependencies=[Depends(require_admin)])
def analytics_learning(days: int = 30):
    """Self-learning funnel (extracted→pending→approved→cited), reject-rate,
    fact-usefulness leaderboard. Admin only."""
    return analytics_mod.learning(days)


@router.get("/analytics/learning-overview", dependencies=[Depends(require_admin)])
def analytics_learning_overview(days: int = 30):
    """Rich learning view for the Workspace: funnel + totals + auto/hand split +
    confidence buckets + learned-per-day + recent facts. Admin only."""
    return analytics_mod.learning_overview(days)


class OpsAsk(BaseModel):
    question: str
    days: int = 30


@router.post("/ops/ask", dependencies=[Depends(require_admin)])
def ops_ask(body: OpsAsk):
    """AI admin agent: natural-language ops Q&A over a live metrics snapshot.
    No raw SQL / no chat text — safe + admin-only."""
    from . import ops_agent
    return ops_agent.ask(body.question, body.days)


# ---------------- shareable answer permalinks ----------------
@router.post("/messages/{message_id}/share")
def share_answer(message_id: int, user: dict = Depends(current_user)):
    """Mint a read-only share link for one of MY answers (or any, if admin)."""
    from .config import SHARE_ENABLED
    if not SHARE_ENABLED:
        raise HTTPException(status_code=403, detail="Sharing disabled")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT c.user_id FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.id = %s", (message_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    if user.get("role") != "admin" and row["user_id"] != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your conversation")
    from . import share as share_mod
    return {"token": share_mod.create(message_id, user.get("email"))}


@router.post("/messages/{message_id}/unshare")
def unshare_answer(message_id: int, user: dict = Depends(current_user)):
    from . import share as share_mod
    with get_conn() as conn:
        conn.execute("UPDATE shares SET revoked = true WHERE message_id = %s", (message_id,))
    return {"ok": True}


# ---------------- Microsoft Teams integration ----------------
@router.post("/teams/messages")
async def teams_messages(request: Request, authorization: str | None = Header(default=None)):
    """Bot Framework messaging endpoint. Validates inbound JWT, then answers async."""
    from .adapters import teams as teams_mod
    if not teams_mod.enabled():
        raise HTTPException(status_code=403, detail="Teams integration not configured")
    if not teams_mod.verify_inbound(authorization):
        raise HTTPException(status_code=401, detail="Invalid bot token")
    try:
        activity = await request.json()
    except Exception:
        activity = {}
    return teams_mod.on_activity(activity)


def _teams_manifest_dict() -> dict:
    """Build the Teams app manifest (manifest.json) with the configured bot App ID filled in."""
    from .appcfg import get_teams
    from urllib.parse import urlparse
    t = get_teams()
    app_id = t.get("app_id") or "00000000-0000-0000-0000-000000000000"
    host = urlparse(t.get("public_url") or "").netloc or "example.com"
    return {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
        "manifestVersion": "1.16",
        "version": "1.0.0",
        "id": app_id,
        "packageName": "com.cityagent.aria",
        "developer": {"name": "City Agent", "websiteUrl": t.get("public_url") or "https://example.com",
                      "privacyUrl": (t.get("public_url") or "https://example.com") + "/login",
                      "termsOfUseUrl": (t.get("public_url") or "https://example.com") + "/login"},
        "name": {"short": "Aria", "full": "City Agent Aria"},
        "description": {"short": "Ask your runbooks & SOPs in Teams.",
                        "full": "City Agent Aria answers questions from your uploaded runbooks/SOPs and taught facts, with citations."},
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#1A1A18",
        "bots": [{"botId": app_id, "scopes": ["personal", "team", "groupchat"],
                  "supportsFiles": False, "isNotificationOnly": False}],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [host],
    }


# minimal valid 1x1 transparent PNG (fallback when brand icons are missing)
_FALLBACK_PNG = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00,
    0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x62, 0x00, 0x01, 0x00, 0x00,
    0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49,
    0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
])


def _icon_bytes(filename: str) -> bytes:
    """Read an icon from frontend/build; fall back to a minimal valid PNG."""
    for base in (ROOT / "frontend" / "build", ROOT / "frontend" / "static"):
        p = base / filename
        try:
            if p.is_file():
                return p.read_bytes()
        except Exception:
            pass
    return _FALLBACK_PNG


@router.get("/teams/manifest", dependencies=[Depends(require_admin)])
def teams_manifest():
    """Teams app manifest (manifest.json) with the configured bot App ID filled in."""
    return _teams_manifest_dict()


@router.get("/teams/manifest.zip", dependencies=[Depends(require_admin)])
def teams_manifest_zip():
    """Downloadable Teams app package: manifest.json + color.png + outline.png, zipped in-memory."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(_teams_manifest_dict(), indent=2))
        zf.writestr("color.png", _icon_bytes("brand-logo.png"))
        zf.writestr("outline.png", _icon_bytes("favicon.png"))
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="aria-teams.zip"'})


@router.get("/teams/config", dependencies=[Depends(require_admin)])
def teams_get_config():
    from .appcfg import get_teams
    t = get_teams()
    return {**t, "app_password": "", "has_password": bool(t.get("app_password"))}


@router.post("/teams/config", dependencies=[Depends(require_admin)])
def teams_save_config(body: dict):
    from .appcfg import save_teams
    # don't wipe the stored secret when the UI sends an empty password field
    if not body.get("app_password"):
        body.pop("app_password", None)
    t = save_teams(body)
    return {**t, "app_password": "", "has_password": bool(t.get("app_password"))}


@router.get("/share/{token}")
def get_share(token: str, authorization: str | None = Header(default=None)):
    """Public read-only view of a shared answer. Members-only unless SHARE_PUBLIC_ENABLED."""
    from .config import SHARE_PUBLIC_ENABLED
    if not SHARE_PUBLIC_ENABLED:
        from .auth.security import decode_token
        tok = (authorization or "").replace("Bearer ", "").strip()
        if not (tok and decode_token(tok)):
            raise HTTPException(status_code=401, detail="Sign in to view this shared answer")
    from . import share as share_mod
    data = share_mod.get(token)
    if not data:
        raise HTTPException(status_code=404, detail="This link is invalid or was revoked")
    return data


@router.get("/analytics/security", dependencies=[Depends(require_admin)])
def analytics_security(days: int = 30):
    """Auth events — failed logins, top offenders, role changes, recent feed,
    last-login per user. Admin only."""
    return analytics_mod.security(days)


@router.get("/analytics/corpus", dependencies=[Depends(require_admin)])
def analytics_corpus():
    """Corpus hygiene — duplicate + conflicting active facts. Admin only."""
    return analytics_mod.corpus()


@router.post("/analytics/verify/run", dependencies=[Depends(require_admin)])
def analytics_verify_run(limit: int = 10, user: dict = Depends(require_admin)):
    """Run the citation verify pass now over up to `limit` unverified answers.
    Lets an admin populate trust scores without enabling the daemon. Admin only."""
    from . import verify as verify_mod
    res = verify_mod.run_batch(limit)
    audit_mod.log(user, "verify.run", "verify", None, {"limit": limit, **res})
    return res


@router.get("/analytics/users", dependencies=[Depends(require_admin)])
def analytics_users(days: int = 30, sort: str = "questions", order: str = "desc",
                    q: str = "", status: str = "all", role: str = "", auth: str = "",
                    page: int = 1, page_size: int = 25):
    """Per-user usage monitor (by id, NO chat text) — paginated/sortable. Admin."""
    usage_rollup.ensure(days)
    return analytics_mod.users_monitor(days, sort, order, q, status, role, auth, page, page_size)


@router.get("/analytics/users/export", dependencies=[Depends(require_admin)])
def analytics_users_export(days: int = 30, status: str = "all", role: str = "", auth: str = ""):
    """CSV export of the user monitor (no chat text). Admin."""
    usage_rollup.ensure(days)
    res = analytics_mod.users_monitor(days, "questions", "desc", "", status, role, auth, 1, 2000)
    cols = ["id", "name", "email", "role", "auth_source", "status", "questions",
            "active_days", "multi_pct", "helpful_pct", "sourced_pct", "blind", "top_intent", "last_active"]
    lines = [",".join(cols)]
    for u in res["users"]:
        row = []
        for c in cols:
            v = u.get(c)
            v = "" if v is None else str(v)
            if "," in v or '"' in v:
                v = '"' + v.replace('"', '""') + '"'
            row.append(v)
        lines.append(",".join(row))
    csv = "\n".join(lines)
    return StreamingResponse(iter([csv]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=aria-users.csv"})


@router.get("/analytics/users/{user_id}", dependencies=[Depends(require_admin)])
def analytics_user_profile(user_id: int, days: int = 90):
    """Single user's usage profile (counts/topics/docs, NO chat text). Admin."""
    usage_rollup.ensure(days)
    return analytics_mod.user_profile(user_id, days)


@router.get("/analytics/config", dependencies=[Depends(require_admin)])
def analytics_config_get():
    return appcfg.get_config()


class UsageConfig(BaseModel):
    minutes_saved_per_answer: float | None = None
    llm_price_per_mtok: float | None = None
    tokens_per_answer: int | None = None


@router.post("/analytics/config", dependencies=[Depends(require_admin)])
def analytics_config_set(req: UsageConfig, user: dict = Depends(require_admin)):
    patch = req.model_dump(exclude_none=True)
    saved = appcfg.save_config(patch)
    audit_mod.log(user, "config.save", "config", "roi", patch)
    return saved


@router.get("/feedback/corrections", dependencies=[Depends(require_admin)])
def feedback_corrections(limit: int = 30):
    """Downvotes that came with a proposed correction — the review work-queue.
    Each may be linked to a pending memory fact (learned_memory_id) to approve."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT f.id, f.question, f.correction, f.note, f.status, f.created_at, "
            "  f.learned_memory_id, m.status AS fact_status "
            "FROM message_feedback f "
            "LEFT JOIN memory m ON m.id = f.learned_memory_id "
            "WHERE f.vote='down' AND f.correction IS NOT NULL AND f.correction <> '' "
            "ORDER BY f.created_at DESC LIMIT %s",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return {"corrections": rows}


@router.post("/analytics/rollup/run", dependencies=[Depends(require_admin)])
def analytics_rollup_run(days: int = 90):
    """Manually (re)build the usage_daily rollup for the last N days. Admin."""
    n = usage_rollup.rebuild_window(days)
    return {"ok": True, "rows": n, "window_days": days}


def _resolve_image(path: str) -> Path:
    """Stored paths may be absolute from a different root (host vs container).
    Remap anything under .../data/pages/... onto the current ROOT."""
    p = Path(path)
    if p.is_file():
        return p
    marker = "data/pages/"
    i = path.find(marker)
    if i != -1:
        cand = ROOT / path[i:]
        if cand.is_file():
            return cand
    return p


@router.get("/pages/{page_id}")
def get_page_image(page_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT image_path FROM pages WHERE id = %s", (page_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    path = row["image_path"] or ""
    # page images are immutable per page id → cache hard so repeat views are free
    cache = {"Cache-Control": "public, max-age=86400"}
    # S3-backed page image: presigned redirect (browser pulls direct) or proxy-stream
    if path.startswith("s3:"):
        from . import s3client
        key = path[3:]
        if s3client.presign_enabled():
            try:
                return RedirectResponse(s3client.presign_get(key), status_code=307)
            except Exception:
                pass
        try:
            body, ctype = s3client.get_stream(key)
        except Exception:
            raise HTTPException(status_code=404, detail="page image missing in S3")
        return StreamingResponse(body, media_type=ctype or "image/png", headers=cache)
    img = _resolve_image(path)
    if not img.is_file():
        raise HTTPException(status_code=404, detail="page image missing on disk")
    return FileResponse(img, media_type="image/png", headers=cache)


# ---- one-logo white-labeling ----------------------------------------------
def _brand_payload() -> dict:
    """Full public brand JSON: appcfg defaults+overrides, with asset URLs pointing
    at uploaded files when present (else the static defaults)."""
    from . import brand as brand_mod
    b = appcfg.get_brand()
    out = {
        "name": b.get("name"), "short_name": b.get("short_name"),
        "tagline": b.get("tagline"), "footer": b.get("footer"),
        "assistant_label": b.get("assistant_label"),
        "accent": b.get("accent"), "accent_dk": b.get("accent_dk"),
        "logo_url": b.get("logo_url", "/brand-logo.png"),
        "mark_url": "/favicon.png", "favicon_url": "/favicon.png",
        "icon192_url": "/icon-192.png", "icon512_url": "/icon-512.png",
        "custom": bool(b.get("custom")),
    }
    # repoint to uploaded assets when they exist on disk
    if brand_mod.asset_path("logo.png"):
        out["logo_url"] = "/api/brand/asset/logo.png"
    if brand_mod.asset_path("mark.png"):
        out["mark_url"] = "/api/brand/asset/mark.png"
    if brand_mod.asset_path("favicon.png"):
        out["favicon_url"] = "/api/brand/asset/favicon.png"
    if brand_mod.asset_path("icon-192.png"):
        out["icon192_url"] = "/api/brand/asset/icon-192.png"
    if brand_mod.asset_path("icon-512.png"):
        out["icon512_url"] = "/api/brand/asset/icon-512.png"
    return out


@router.get("/brand")
def get_brand_public():
    """PUBLIC: current white-label brand. Never raises — falls back to defaults."""
    try:
        return _brand_payload()
    except Exception:
        return {
            "name": "City Agent Aria", "short_name": "Aria",
            "tagline": "Your runbook intelligence — answered with the source page.",
            "footer": "© 2026 City Agent Aria · Runbooks & IT Assistance",
            "assistant_label": "ARIA 1.0",
            "accent": "#c2683f", "accent_dk": "#a8542f",
            "logo_url": "/brand-logo.png", "mark_url": "/favicon.png",
            "favicon_url": "/favicon.png", "icon192_url": "/icon-192.png",
            "icon512_url": "/icon-512.png", "custom": False,
        }


@router.post("/admin/brand")
async def save_brand_admin(
    logo: UploadFile | None = File(None),
    name: str | None = Form(None),
    short_name: str | None = Form(None),
    tagline: str | None = Form(None),
    footer: str | None = Form(None),
    assistant_label: str | None = Form(None),
    accent: str | None = Form(None),
    user: dict = Depends(require_admin),
):
    """Admin: save white-label brand. Optional logo (PNG raster derives mark/icons +
    auto-accent; SVG stored as-is). Text/accent fields merge into the brand config."""
    from . import brand as brand_mod
    patch: dict = {
        "name": name, "short_name": short_name, "tagline": tagline,
        "footer": footer, "assistant_label": assistant_label,
    }

    if logo is not None:
        raw = await logo.read()
        if not raw:
            raise HTTPException(status_code=400, detail="empty logo upload")
        fname = (logo.filename or "").lower()
        ctype = (logo.content_type or "").lower()
        is_svg = fname.endswith(".svg") or "svg" in ctype
        if is_svg:
            # store SVG as-is; skip Pillow derive, keep prior/auto accent
            brand_mod._ensure_dir()
            (Path(brand_mod.BRAND_DIR) / "logo.svg").write_bytes(raw)
            patch["logo_url"] = "/api/brand/asset/logo.svg"
        else:
            try:
                res = brand_mod.process_logo(raw)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            patch["logo_url"] = "/api/brand/asset/logo.png"
            if not accent:  # no explicit accent → use the one extracted from the logo
                accent = res.get("accent")

    if accent:
        accent = str(accent).strip()
        patch["accent"] = accent
        patch["accent_dk"] = brand_mod.darken(accent)

    patch["custom"] = True
    appcfg.save_brand(patch)
    return _brand_payload()


@router.get("/brand/asset/{name}")
def get_brand_asset(name: str):
    """PUBLIC: serve an uploaded brand asset (whitelisted names only)."""
    from . import brand as brand_mod
    if name == "logo.svg":
        p = Path(brand_mod.BRAND_DIR) / "logo.svg"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(p, media_type="image/svg+xml",
                            headers={"Cache-Control": "public, max-age=300"})
    p = brand_mod.asset_path(name)
    if p is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(p, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=300"})


@router.get("/pages/{page_id}/text", dependencies=[Depends(current_principal)])
def get_page_text(page_id: int):
    """Inspect what was extracted/vision-read for a page (verify vision quality)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.id, d.name AS doc_name, p.page_no, p.text_layer, "
            "p.vision_text FROM pages p JOIN docs d ON d.id = p.doc_id "
            "WHERE p.id = %s",
            (page_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    return row


def _one_line(s: str, n: int = 180) -> str:
    """Collapse whitespace to a single line and trim to ~n chars."""
    return re.sub(r"\s+", " ", (s or "")).strip()[:n]


@router.get("/answer/sources", dependencies=[Depends(current_principal)])
def answer_sources(ids: str = ""):
    """Grouped citation sources for a set of page_ids (comma-separated).
    Returns docs (in first-appearance order) -> their cited pages (by page_no),
    each with a compiled-markdown snippet + the public page-image url. Used by
    the citation drawer / sources panel."""
    page_ids: list[int] = []
    for tok in (ids or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            page_ids.append(int(tok))
    if not page_ids:
        return {"sources": []}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.id AS page_id, p.doc_id AS doc_id, p.page_no, "
            "d.name AS doc_name, "
            "coalesce(p.image_path,'') <> '' AS has_image, "
            "coalesce(m.md, left(p.text, 180)) AS snippet, "
            "w.summary AS summary "
            "FROM pages p "
            "JOIN docs d ON d.id = p.doc_id "
            "LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no "
            "LEFT JOIN doc_wiki w ON w.doc_id = p.doc_id "
            "WHERE p.id = ANY(%s)",
            (page_ids,),
        ).fetchall()

    by_id = {r["page_id"]: r for r in rows}
    # docs in the order their first page appears in the input ids
    doc_order: list[int] = []
    docs: dict[int, dict] = {}
    for pid in page_ids:
        r = by_id.get(pid)
        if not r:
            continue
        did = r["doc_id"]
        if did not in docs:
            doc_order.append(did)
            docs[did] = {
                "doc_id": did,
                "title": _friendly_doc(r["doc_name"]),
                "summary": r["summary"] or "",
                "pages": [],
                "_seen": set(),
            }
        if r["page_id"] in docs[did]["_seen"]:
            continue
        docs[did]["_seen"].add(r["page_id"])
        docs[did]["pages"].append({
            "page_id": r["page_id"],
            "page_no": r["page_no"],
            "snippet": _one_line(r["snippet"]),
            "has_image": r["has_image"],
            "image_url": f"/api/pages/{r['page_id']}" if r["has_image"] else None,
        })

    out = []
    for did in doc_order:
        d = docs[did]
        d.pop("_seen", None)
        d["pages"].sort(key=lambda p: (p["page_no"] is None, p["page_no"]))
        out.append(d)
    return {"sources": out}


def _ids_param(ids: str) -> list[int]:
    return [int(t) for t in (ids or "").split(",") if t.strip().isdigit()]


@router.get("/answer/related", dependencies=[Depends(require_key)])
def answer_related(ids: str = ""):
    """Graph-grounded 'related runbooks' for an answer's cited page_ids: other docs
    most often cited ALONGSIDE the cited docs in past answers (co-citation neighbors).
    Powers click-to-ask chips that are grounded in real connections, not LLM guesses."""
    page_ids = _ids_param(ids)
    if not page_ids:
        return {"related": []}
    with get_conn() as conn:
        crows = conn.execute(
            "SELECT DISTINCT doc_id FROM pages WHERE id = ANY(%s)", (page_ids,)
        ).fetchall()
        cited_docs = [r["doc_id"] for r in crows]
        if not cited_docs:
            return {"related": []}
        rows = conn.execute(
            "SELECT d.id, d.name, count(DISTINCT m.id) AS freq "
            "FROM messages m "
            " CROSS JOIN LATERAL jsonb_array_elements(m.pages) e "
            " JOIN pages p ON p.id = (e->>'page_id')::int "
            " JOIN docs d ON d.id = p.doc_id "
            "WHERE m.role='bot' AND m.pages::text <> '[]' "
            " AND d.id <> ALL(%s) "
            " AND m.id IN (SELECT m2.id FROM messages m2 "
            "    CROSS JOIN LATERAL jsonb_array_elements(m2.pages) e2 "
            "    JOIN pages p2 ON p2.id = (e2->>'page_id')::int "
            "    WHERE m2.role='bot' AND p2.doc_id = ANY(%s)) "
            "GROUP BY d.id, d.name ORDER BY freq DESC LIMIT 5",
            (cited_docs, cited_docs),
        ).fetchall()
    related = [{"doc_id": r["id"], "title": _friendly_doc(r["name"]), "freq": r["freq"]} for r in rows]
    return {"related": related}


@router.get("/answer/graph", dependencies=[Depends(require_key)])
def answer_graph(ids: str = ""):
    """Tiny provenance graph for the Sources drawer: the answer's cited PAGES, the
    DOCS they belong to, and 1-hop co-cited neighbor docs — {nodes,links}."""
    page_ids = _ids_param(ids)
    nodes: list[dict] = []
    links: list[dict] = []
    seen: set = set()

    def add(nid, ntype, label, val, **extra):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "type": ntype, "label": label, "val": val, **extra})

    if not page_ids:
        return {"nodes": nodes, "links": links}
    with get_conn() as conn:
        prows = conn.execute(
            "SELECT p.id, p.page_no, p.doc_id, d.name AS doc_name FROM pages p "
            "JOIN docs d ON d.id = p.doc_id WHERE p.id = ANY(%s)", (page_ids,)
        ).fetchall()
        cited_docs = sorted({r["doc_id"] for r in prows})
        for r in prows:
            fr = _friendly_doc(r["doc_name"])
            add(f"d{r['doc_id']}", "doc", fr, 24, doc_id=r["doc_id"])
            add(f"p{r['id']}", "page", f"{fr} · p.{r['page_no']}", 12, doc_id=r["doc_id"])
            links.append({"source": f"d{r['doc_id']}", "target": f"p{r['id']}", "kind": "contains"})
        # neighbor docs (co-cited with the cited docs)
        nrows = conn.execute(
            "SELECT d.id, d.name, count(DISTINCT m.id) AS freq FROM messages m "
            " CROSS JOIN LATERAL jsonb_array_elements(m.pages) e JOIN pages p ON p.id=(e->>'page_id')::int "
            " JOIN docs d ON d.id=p.doc_id "
            "WHERE m.role='bot' AND d.id <> ALL(%s) AND m.id IN ("
            "  SELECT m2.id FROM messages m2 CROSS JOIN LATERAL jsonb_array_elements(m2.pages) e2 "
            "  JOIN pages p2 ON p2.id=(e2->>'page_id')::int WHERE p2.doc_id = ANY(%s)) "
            "GROUP BY d.id, d.name ORDER BY freq DESC LIMIT 5",
            (cited_docs, cited_docs),
        ).fetchall()
        for r in nrows:
            add(f"d{r['id']}", "doc", _friendly_doc(r["name"]), 14, doc_id=r["id"])
            for cd in cited_docs:
                links.append({"source": f"d{cd}", "target": f"d{r['id']}", "kind": "cocite", "value": r["freq"]})
    return {"nodes": nodes, "links": links}


@router.get("/brain/search", dependencies=[Depends(require_key)])
def brain_search(q: str = "", type: str = "all"):
    """Unified brain search across document pages, taught facts, and doc summaries.

    type filter:
      • all  → everything (pages + facts + doc summaries)
      • doc  → document-sourced knowledge (pages + wiki summaries)
      • fact → taught facts only
    Empty q → recent/top items (top active facts + recent ready docs) so the
    brain view is never blank. Each item carries a uniform shape (see below)."""
    type = (type or "all").strip().lower()
    if type not in ("all", "doc", "fact"):
        type = "all"

    items: list[dict] = []

    if not (q or "").strip():
        # no query → seed with recent/top items so the view isn't empty
        if type in ("all", "fact"):
            for f in list_memory(limit=12, status="active"):
                text = f"{f['key']}: {f['value']}" if f.get("key") else f["value"]
                items.append({
                    "type": "fact", "id": f["id"], "title": (text or "")[:60],
                    "snippet": text, "doc_id": None, "page_no": None,
                    "image_url": None, "source": f.get("source"),
                    "status": f.get("status"), "score": 0.0,
                })
        if type in ("all", "doc"):
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT d.id, d.name, coalesce(w.summary,'') AS summary "
                    "FROM docs d LEFT JOIN doc_wiki w ON w.doc_id = d.id "
                    "WHERE d.status = 'ready' ORDER BY d.id DESC LIMIT 12"
                ).fetchall()
            for r in rows:
                items.append({
                    "type": "doc", "id": r["id"], "title": _friendly_doc(r["name"]),
                    "snippet": (r["summary"] or "")[:180], "doc_id": r["id"],
                    "page_no": None, "image_url": None, "source": None,
                    "status": None, "score": 0.0,
                })
        return {"items": items}

    brain = search_brain(q)
    for it in brain:
        t = it.get("type")
        if type == "fact" and t != "fact":
            continue
        if type == "doc" and t not in ("page", "doc"):
            continue
        page_id = it.get("page_id") if t == "page" else None
        items.append({
            "type": t,
            "id": it["id"],
            "title": it.get("title") or "",
            "snippet": (it.get("snippet") or "")[:300],
            "doc_id": it.get("doc_id"),
            "page_no": it.get("page_no"),
            "image_url": f"/api/pages/{page_id}" if page_id else None,
            "source": it.get("source"),
            "status": it.get("status"),
            "score": round(float(it.get("score") or 0.0), 4),
        })
    return {"items": items}


def _require_superadmin(user: dict) -> None:
    # strict top tier: only 'superadmin' (the env first user) — an 'admin' sees all
    # sectors but cannot create sectors / toggle multi-tenant / manage the top config.
    if (user or {}).get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super-admin only.")


@router.get("/admin/rbac", dependencies=[Depends(require_superadmin)])
def admin_rbac_get():
    """Current multi-tenant RBAC switch (DB runtime config over the env default)."""
    return {"enabled": appcfg.get_rbac_enabled()}


@router.post("/admin/rbac", dependencies=[Depends(require_superadmin)])
def admin_rbac_set(body: dict = Body(...), user: dict = Depends(current_user)):
    """Turn multi-tenant access (sectors + folders) on/off live — no restart.
    Super-admin only. When OFF the app is single-tenant (everyone sees all)."""
    _require_superadmin(user)
    on = bool(body.get("enabled"))
    appcfg.set_rbac_enabled(on)
    audit_mod.log(user, "rbac.toggle", "config", None, {"enabled": on})
    return {"enabled": on}


@router.get("/admin/sectors", dependencies=[Depends(require_superadmin)])
def admin_sectors_list():
    from . import admin_rbac
    return {"sectors": admin_rbac.list_sectors()}


@router.post("/admin/sectors", dependencies=[Depends(require_superadmin)])
def admin_sectors_create(body: dict = Body(...), user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    return admin_rbac.create_sector(body.get("name", ""), body.get("label"))


@router.delete("/admin/sectors/{sid}", dependencies=[Depends(require_superadmin)])
def admin_sectors_delete(sid: int, user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    try:
        return {"ok": admin_rbac.delete_sector(sid)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/admin/users", dependencies=[Depends(require_admin)])
def admin_users_list(limit: int = 500):
    from . import admin_rbac
    return {"users": admin_rbac.list_users(limit)}


@router.patch("/admin/users/{uid}", dependencies=[Depends(require_superadmin)])
def admin_users_set(uid: int, body: dict = Body(...), user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    try:
        return admin_rbac.set_user(uid, body.get("role"), body.get("sector_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/groups", dependencies=[Depends(require_superadmin)])
def admin_groups_list():
    from . import admin_rbac
    return {"groups": admin_rbac.list_groups()}


@router.post("/admin/groups", dependencies=[Depends(require_superadmin)])
def admin_groups_create(body: dict = Body(...), user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    return admin_rbac.create_group(
        body.get("name", ""), bool(body.get("all_sectors")),
        description=body.get("description"),
        features=body.get("features"),
        manage_content=bool(body.get("manage_content")),
        teach_knowledge=bool(body.get("teach_knowledge")),
    )


@router.patch("/admin/groups/{gid}", dependencies=[Depends(require_superadmin)])
def admin_groups_update(gid: int, body: dict = Body(...), user: dict = Depends(current_user)):
    """Partial-update a group (name / description / all_sectors / features /
    manage_content / teach_knowledge). Only the keys present in the body change.
    Super-admin only."""
    _require_superadmin(user)
    from . import admin_rbac
    kw = {}
    for k in ("name", "description", "all_sectors", "features",
              "manage_content", "teach_knowledge"):
        if k in body:
            kw[k] = body[k]
    try:
        return admin_rbac.update_group(gid, **kw)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/admin/groups/{gid}", dependencies=[Depends(require_superadmin)])
def admin_groups_delete(gid: int, user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    return {"ok": admin_rbac.delete_group(gid)}


@router.put("/admin/groups/{gid}/features", dependencies=[Depends(require_superadmin)])
def admin_group_features(gid: int, body: dict = Body(...), user: dict = Depends(current_user)):
    """Set which app features (tabs) a group's members may use. Super-admin only.
    features = subset of chat|sources|workspace|eval|wiki."""
    _require_superadmin(user)
    from . import admin_rbac
    try:
        return admin_rbac.set_group_features(gid, body.get("features") or [])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/admin/groups/{gid}/members/{uid}", dependencies=[Depends(require_admin)])
def admin_group_add(gid: int, uid: int, user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    return {"ok": admin_rbac.add_group_member(gid, uid)}


@router.delete("/admin/groups/{gid}/members/{uid}", dependencies=[Depends(require_admin)])
def admin_group_remove(gid: int, uid: int, user: dict = Depends(current_user)):
    _require_superadmin(user)
    from . import admin_rbac
    return {"ok": admin_rbac.remove_group_member(gid, uid)}


@router.post("/graphrag/build", dependencies=[Depends(require_admin)])
def graphrag_build(user: dict = Depends(current_user)):
    """Backfill GraphRAG entity relationships across all ready docs (run once
    after enabling GRAPHRAG_ENABLED). 1 LLM pass/doc. Admin only."""
    from . import graphrag
    res = graphrag.link_all()
    try:
        audit_mod.log(user, "graphrag.build", "graph", None, res)
    except Exception:
        pass
    return res


@router.get("/graphrag/stats", dependencies=[Depends(require_key)])
def graphrag_stats():
    """Edge + entity counts for the relationship graph."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT (SELECT count(*) FROM entity_edge) AS edges, "
            "(SELECT count(*) FROM entities) AS entities, "
            "(SELECT count(DISTINCT rel) FROM entity_edge) AS rel_types, "
            "(SELECT count(*) FROM community_summary) AS communities").fetchone()
    return dict(r)


@router.get("/answer/kg", dependencies=[Depends(require_key)])
def answer_kg(ids: str = ""):
    """Answer-scoped REAL knowledge subgraph for the Sources drawer (#5 style):
    entities mentioned in the cited pages' docs + 1-hop typed neighbours."""
    from . import graphrag
    return graphrag.graph_for_pages(_ids_param(ids), hops=1, limit=60)


@router.get("/graphrag/graph", dependencies=[Depends(require_key)])
def graphrag_graph(limit: int = 300):
    """Nodes + typed edges for the Knowledge Graph view (#5)."""
    from . import graphrag
    return graphrag.graph_data(limit)


@router.get("/graphrag/entity", dependencies=[Depends(require_key)])
def graphrag_entity(id: int | None = None, name: str | None = None):
    """Entity profile: relationships + docs (#3). Resolve by id or name."""
    from . import graphrag
    eid = id if id is not None else graphrag.resolve_entity(name or "")
    if not eid:
        raise HTTPException(status_code=404, detail="entity not found")
    return graphrag.entity_profile(eid)


@router.get("/graphrag/path", dependencies=[Depends(require_key)])
def graphrag_path(a: str, b: str):
    """Shortest relationship path + plain-English explanation between two things (#2)."""
    from . import graphrag
    return graphrag.path_between(a, b)


@router.post("/graphrag/communities/build", dependencies=[Depends(require_admin)])
def graphrag_communities_build(user: dict = Depends(current_user)):
    """Cluster the graph + summarise each community (for global queries, #1)."""
    from . import graphrag
    res = graphrag.build_communities()
    try:
        audit_mod.log(user, "graphrag.communities", "graph", None, res)
    except Exception:
        pass
    return res


@router.get("/graphrag/global", dependencies=[Depends(require_key)])
def graphrag_global(q: str):
    """Whole-corpus / 'summarize everything about Z' answer from community summaries (#1)."""
    from . import graphrag
    return graphrag.global_answer(q)


@router.get("/brain/graph", dependencies=[Depends(require_key)])
def brain_graph(docs: int = 1, pages: int = 1, facts: int = 1, limit: int = 400):
    """Obsidian-style force-graph of the brain: docs, pages and taught facts as
    nodes; contains / cocite / cite edges. All SQL, fail-soft, each edge class
    capped so a huge corpus still renders.

    Node ids are namespaced: docs 'd{doc_id}', pages 'p{page_id}', facts
    'f{memory_id}'. `limit` caps PAGE nodes (top-N by citation/usage); docs and
    facts are few so they're always included. Only links whose BOTH endpoints
    are in the included node set are emitted."""
    want_docs, want_pages, want_facts = bool(docs), bool(pages), bool(facts)
    limit = max(1, min(int(limit or 400), 5000))
    COCITE_CAP = 4000
    SEQ_CAP = 6000          # consecutive-page edges (<= total pages, generous)
    SIMILAR_CAP = 200       # doc<->doc edges
    SIMILAR_MIN = 6         # if co-citation yields fewer, add summary-similarity
    SIM_THRESHOLD = 0.15    # trigram summary similarity fallback

    nodes: list[dict] = []
    links: list[dict] = []
    node_ids: set[str] = set()
    page_to_doc: dict[int, int] = {}    # page_id -> doc_id (only included pages)
    doc_ids: set[int] = set()

    def add_node(nid: str, ntype: str, label: str, val: float, doc_id, **extra):
        if nid in node_ids:
            return
        node_ids.add(nid)
        n = {"id": nid, "type": ntype, "label": (label or "")[:60],
             "val": float(max(val, 1)), "doc_id": doc_id}
        n.update(extra)
        nodes.append(n)

    # ---- doc usage (times a doc's pages were cited) for node sizing ----
    doc_used: dict[int, int] = {}
    if want_docs:
        try:
            with get_conn() as conn:
                urows = conn.execute(
                    "SELECT pg.doc_id AS doc_id, count(*) AS used "
                    "FROM messages m JOIN jsonb_array_elements(m.pages) e ON true "
                    "JOIN pages pg ON pg.id = (e->>'page_id')::int "
                    "WHERE m.role = 'bot' GROUP BY pg.doc_id"
                ).fetchall()
            for r in urows:
                doc_used[r["doc_id"]] = r["used"] or 0
        except Exception as e:
            print(f"[graph] doc usage failed: {e!r}")

    # ---- doc nodes (few; always all) ----
    if want_docs:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, name, coalesce(page_count,"
                    "  (SELECT count(*) FROM pages p WHERE p.doc_id = d.id)) AS pc "
                    "FROM docs d WHERE status = 'ready' ORDER BY id"
                ).fetchall()
            for r in rows:
                doc_ids.add(r["id"])
                add_node(f"d{r['id']}", "doc", _friendly_doc(r["name"]),
                         r["pc"] or 1, r["id"], used=int(doc_used.get(r["id"], 0)))
        except Exception as e:
            print(f"[graph] docs failed: {e!r}")

        # explicit doc->doc links (from OKF import cross-links), both ends included
        try:
            with get_conn() as conn:
                lrows = conn.execute(
                    "SELECT src_doc, dst_doc FROM doc_links").fetchall()
            for lr in lrows:
                if lr["src_doc"] in doc_ids and lr["dst_doc"] in doc_ids:
                    links.append({"source": f"d{lr['src_doc']}",
                                  "target": f"d{lr['dst_doc']}", "kind": "link"})
        except Exception as e:
            print(f"[graph] doc_links failed: {e!r}")

    # ---- page nodes (capped: top-N by citation count) ----
    if want_pages:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT p.id AS page_id, p.doc_id, p.page_no, d.name AS doc_name, "
                    "  coalesce(c.cites, 0) AS cites "
                    "FROM pages p JOIN docs d ON d.id = p.doc_id "
                    "LEFT JOIN ("
                    "  SELECT (e->>'page_id')::int AS page_id, count(*) AS cites "
                    "  FROM messages m JOIN jsonb_array_elements(m.pages) e ON true "
                    "  WHERE m.role = 'bot' GROUP BY 1"
                    ") c ON c.page_id = p.id "
                    "WHERE d.status = 'ready' "
                    "ORDER BY c.cites DESC NULLS LAST, p.id "
                    "LIMIT %s",
                    (limit,),
                ).fetchall()
            for r in rows:
                label = f"{_friendly_doc(r['doc_name'])} p.{r['page_no']}"
                add_node(f"p{r['page_id']}", "page", label, r["cites"] or 1,
                         r["doc_id"])
                page_to_doc[r["page_id"]] = r["doc_id"]
        except Exception as e:
            print(f"[graph] pages failed: {e!r}")

    # ---- fact nodes (few; always all active) ----
    fact_ids: set[int] = set()
    if want_facts:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, key, value, coalesce(cited_count,0) AS cc "
                    "FROM memory WHERE status = 'active' ORDER BY id"
                ).fetchall()
            for r in rows:
                fact_ids.add(r["id"])
                label = (f"{r['key']}: {r['value']}" if r["key"] else r["value"]) or ""
                add_node(f"f{r['id']}", "fact", label[:40], r["cc"] or 1, None)
        except Exception as e:
            print(f"[graph] facts failed: {e!r}")

    # ---- contains: page -> its doc (only for included pages whose doc is a node) ----
    if want_pages and want_docs:
        for pid, did in page_to_doc.items():
            tgt = f"d{did}"
            if tgt in node_ids:
                links.append({"source": f"p{pid}", "target": tgt, "kind": "contains"})

    # ---- cocite: pages cited together in the same answer (deduped pairs, capped) ----
    if want_pages:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT m.id, array_agg(DISTINCT (e->>'page_id')::int) AS pids "
                    "FROM messages m JOIN jsonb_array_elements(m.pages) e ON true "
                    "WHERE m.role = 'bot' "
                    "GROUP BY m.id HAVING count(*) > 1"
                ).fetchall()
            seen_pairs: set[tuple] = set()
            for r in rows:
                pids = [p for p in (r["pids"] or []) if f"p{p}" in node_ids]
                if len(pids) < 2:
                    continue
                # star to the first included page → keeps it sparse + renderable
                anchor = pids[0]
                for other in pids[1:]:
                    pair = (anchor, other) if anchor < other else (other, anchor)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    links.append({"source": f"p{pair[0]}", "target": f"p{pair[1]}",
                                  "kind": "cocite"})
                    if len(seen_pairs) >= COCITE_CAP:
                        break
                if len(seen_pairs) >= COCITE_CAP:
                    break
        except Exception as e:
            print(f"[graph] cocite failed: {e!r}")

    # ---- cite: fact -> page (from feedback page_ids) and fact -> doc (derived) ----
    if want_facts:
        # fact -> page from message_feedback.page_ids where a learned_memory_id is set
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT learned_memory_id AS mid, (e)::int AS page_id "
                    "FROM message_feedback f "
                    "JOIN jsonb_array_elements_text(f.page_ids) e ON true "
                    "WHERE f.learned_memory_id IS NOT NULL"
                ).fetchall()
            for r in rows:
                src = f"f{r['mid']}"
                tgt = f"p{r['page_id']}"
                if src in node_ids and tgt in node_ids:
                    links.append({"source": src, "target": tgt, "kind": "cite"})
                    # also tie the fact to that page's doc when both are nodes
                    did = page_to_doc.get(r["page_id"])
                    if did is not None and f"d{did}" in node_ids:
                        links.append({"source": src, "target": f"d{did}",
                                      "kind": "cite"})
        except Exception as e:
            print(f"[graph] fact->page cite failed: {e!r}")

    # ---- sequence: consecutive pages within the same doc (reading spine) ----
    # link page_no N -> N+1 when BOTH page nodes are included. Capped.
    if want_pages:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT p.id AS a, n.id AS b "
                    "FROM pages p JOIN docs d ON d.id = p.doc_id "
                    "JOIN pages n ON n.doc_id = p.doc_id "
                    "  AND n.page_no = p.page_no + 1 "
                    "WHERE d.status = 'ready' "
                    "ORDER BY p.doc_id, p.page_no "
                    "LIMIT %s",
                    (SEQ_CAP,),
                ).fetchall()
            seq_n = 0
            for r in rows:
                src = f"p{r['a']}"
                tgt = f"p{r['b']}"
                if src in node_ids and tgt in node_ids:
                    links.append({"source": src, "target": tgt,
                                  "kind": "sequence"})
                    seq_n += 1
                    if seq_n >= SEQ_CAP:
                        break
        except Exception as e:
            print(f"[graph] sequence failed: {e!r}")

    # ---- similar: doc<->doc relatedness (co-citation primary, summary-sim fallback) ----
    # only between included doc nodes; undirected dedupe (a<b); capped.
    if want_docs:
        sim_pairs: set[tuple] = set()
        # (a) docs whose pages were co-cited in the same answer (2+ docs)
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT m.id, array_agg(DISTINCT pg.doc_id) AS dids "
                    "FROM messages m "
                    "JOIN jsonb_array_elements(m.pages) e ON true "
                    "JOIN pages pg ON pg.id = (e->>'page_id')::int "
                    "WHERE m.role = 'bot' "
                    "GROUP BY m.id HAVING count(DISTINCT pg.doc_id) > 1"
                ).fetchall()
            for r in rows:
                dids = sorted(d for d in (r["dids"] or [])
                              if f"d{d}" in node_ids)
                for i in range(len(dids)):
                    for j in range(i + 1, len(dids)):
                        sim_pairs.add((dids[i], dids[j]))
                        if len(sim_pairs) >= SIMILAR_CAP:
                            break
                    if len(sim_pairs) >= SIMILAR_CAP:
                        break
                if len(sim_pairs) >= SIMILAR_CAP:
                    break
        except Exception as e:
            print(f"[graph] similar co-cite failed: {e!r}")
        # (b) fall back to summary trigram similarity if co-citation is thin
        if len(sim_pairs) < SIMILAR_MIN:
            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT a.doc_id AS lo, b.doc_id AS hi "
                        "FROM doc_wiki a JOIN doc_wiki b ON a.doc_id < b.doc_id "
                        "WHERE similarity(coalesce(a.summary,''), "
                        "  coalesce(b.summary,'')) > %s "
                        "ORDER BY similarity(coalesce(a.summary,''), "
                        "  coalesce(b.summary,'')) DESC "
                        "LIMIT %s",
                        (SIM_THRESHOLD, SIMILAR_CAP),
                    ).fetchall()
                for r in rows:
                    lo, hi = r["lo"], r["hi"]
                    if f"d{lo}" in node_ids and f"d{hi}" in node_ids:
                        sim_pairs.add((lo, hi))
                        if len(sim_pairs) >= SIMILAR_CAP:
                            break
            except Exception as e:
                print(f"[graph] similar summary-sim failed: {e!r}")
        for lo, hi in sim_pairs:
            links.append({"source": f"d{lo}", "target": f"d{hi}",
                          "kind": "similar"})

    # ---- requires: doc->doc prerequisite edges (Phase 5/5.4) ----
    if want_docs:
        try:
            with get_conn() as conn:
                drows = conn.execute(
                    "SELECT from_doc, to_doc, reason FROM doc_dependency"
                ).fetchall()
            for r in drows:
                if f"d{r['from_doc']}" in node_ids and f"d{r['to_doc']}" in node_ids:
                    links.append({"source": f"d{r['from_doc']}",
                                  "target": f"d{r['to_doc']}", "kind": "requires",
                                  "reason": r.get("reason") or ""})
        except Exception as e:
            print(f"[graph] requires edges failed: {e!r}")

    return {"nodes": nodes, "links": links}


def _rel_time(dt) -> str:
    """Friendly 'last cited/used' label; tolerant of None / naive datetimes."""
    if not dt:
        return "never"
    try:
        if isinstance(dt, str):
            return dt[:19]
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _doc_label(name) -> str:
    return _friendly_doc(name)


def _page_label(friendly: str, page_no) -> str:
    return f"{friendly} · p.{page_no}" if page_no is not None else friendly


def _rel_doc(doc_id: int, conn) -> list[dict]:
    """Grouped+explained relations for a doc node. Each group fail-soft."""
    rels: list[dict] = []
    # contains -> up to 8 of its pages
    try:
        rows = conn.execute(
            "SELECT id, page_no FROM pages WHERE doc_id = %s "
            "ORDER BY page_no, id LIMIT 8",
            (doc_id,),
        ).fetchall()
        items = [{"id": f"p{r['id']}", "type": "page",
                  "label": (f"p.{r['page_no']}" if r["page_no"] is not None else "page")}
                 for r in rows]
        if items:
            rels.append({"kind": "contains", "title": "Pages",
                         "why": "Pages inside this document.", "items": items})
    except Exception as e:
        print(f"[rel] doc contains failed: {e!r}")
    # requires -> prerequisite documents (Phase 5/5.4)
    try:
        rows = conn.execute(
            "SELECT dd.to_doc, d.name FROM doc_dependency dd "
            "JOIN docs d ON d.id = dd.to_doc WHERE dd.from_doc = %s", (doc_id,),
        ).fetchall()
        items = [{"id": f"d{r['to_doc']}", "type": "doc",
                  "label": _friendly_doc(r["name"])} for r in rows]
        if items:
            rels.append({"kind": "requires", "title": "Complete first",
                         "why": "These procedures must be done before this one.",
                         "items": items})
    except Exception as e:
        print(f"[rel] doc requires failed: {e!r}")
    # similar -> related docs (co-citation aggregated to doc level, trigram fallback)
    try:
        sim: set[int] = set()
        rows = conn.execute(
            "SELECT array_agg(DISTINCT pg.doc_id) AS dids "
            "FROM messages m "
            "JOIN jsonb_array_elements(m.pages) e ON true "
            "JOIN pages pg ON pg.id = (e->>'page_id')::int "
            "WHERE m.role = 'bot' "
            "GROUP BY m.id HAVING count(DISTINCT pg.doc_id) > 1",
        ).fetchall()
        for r in rows:
            dids = r["dids"] or []
            if doc_id in dids:
                for d in dids:
                    if d != doc_id:
                        sim.add(d)
        if len(sim) < 6:
            try:
                trows = conn.execute(
                    "SELECT CASE WHEN a.doc_id = %s THEN b.doc_id ELSE a.doc_id END AS other "
                    "FROM doc_wiki a JOIN doc_wiki b ON a.doc_id <> b.doc_id "
                    "WHERE (a.doc_id = %s OR b.doc_id = %s) "
                    "  AND similarity(coalesce(a.summary,''), coalesce(b.summary,'')) > 0.15 "
                    "ORDER BY similarity(coalesce(a.summary,''), coalesce(b.summary,'')) DESC "
                    "LIMIT 8",
                    (doc_id, doc_id, doc_id),
                ).fetchall()
                for tr in trows:
                    sim.add(tr["other"])
            except Exception as e:
                print(f"[rel] doc similar trigram failed: {e!r}")
        sim.discard(doc_id)
        if sim:
            drows = conn.execute(
                "SELECT id, name FROM docs WHERE id = ANY(%s)",
                (list(sim)[:8],),
            ).fetchall()
            items = [{"id": f"d{r['id']}", "type": "doc",
                      "label": _doc_label(r["name"])} for r in drows]
            if items:
                rels.append({"kind": "similar", "title": "Related documents",
                             "why": "These documents cover related topics or the same system.",
                             "items": items})
    except Exception as e:
        print(f"[rel] doc similar failed: {e!r}")
    return rels


def _rel_page(page_id: int, doc_id: int, page_no, doc_name: str, conn) -> list[dict]:
    """Grouped+explained relations for a page node. Each group fail-soft."""
    rels: list[dict] = []
    friendly = _friendly_doc(doc_name)
    # contains -> parent doc
    rels.append({"kind": "contains", "title": "Part of",
                 "why": f"This page belongs to the document **{friendly}**.",
                 "items": [{"id": f"d{doc_id}", "type": "doc", "label": friendly}]})
    # sequence -> previous + next page in the same doc
    if page_no is not None:
        try:
            rows = conn.execute(
                "SELECT id, page_no FROM pages "
                "WHERE doc_id = %s AND page_no IN (%s, %s) "
                "ORDER BY page_no",
                (doc_id, page_no - 1, page_no + 1),
            ).fetchall()
            items = [{"id": f"p{r['id']}", "type": "page",
                      "label": f"p.{r['page_no']}"} for r in rows]
            if items:
                rels.append({"kind": "sequence", "title": "Reading order",
                             "why": "Comes right before/after these pages in the document.",
                             "items": items})
        except Exception as e:
            print(f"[rel] page sequence failed: {e!r}")
    # cocite -> pages cited in the same answers as this page (cap 8)
    try:
        rows = conn.execute(
            "SELECT DISTINCT (e->>'page_id')::int AS pid "
            "FROM messages m "
            "JOIN jsonb_array_elements(m.pages) e ON true "
            "WHERE m.role = 'bot' AND m.id IN ("
            "  SELECT m2.id FROM messages m2 "
            "  JOIN jsonb_array_elements(m2.pages) e2 ON true "
            "  WHERE m2.role = 'bot' AND (e2->>'page_id')::int = %s"
            ") "
            "AND (e->>'page_id')::int <> %s LIMIT 8",
            (page_id, page_id),
        ).fetchall()
        pids = [r["pid"] for r in rows if r["pid"] is not None]
        if pids:
            prows = conn.execute(
                "SELECT p.id, p.page_no, d.name AS doc_name "
                "FROM pages p JOIN docs d ON d.id = p.doc_id "
                "WHERE p.id = ANY(%s)",
                (pids,),
            ).fetchall()
            items = [{"id": f"p{r['id']}", "type": "page",
                      "label": _page_label(_friendly_doc(r["doc_name"]), r["page_no"])}
                     for r in prows]
            if items:
                rels.append({"kind": "cocite", "title": "Answered together",
                             "why": "Aria cites these pages in the **same answers** as this one.",
                             "items": items})
    except Exception as e:
        print(f"[rel] page cocite failed: {e!r}")
    # cite -> facts referencing this page (cap 6)
    try:
        rows = conn.execute(
            "SELECT DISTINCT mf.learned_memory_id AS mid "
            "FROM message_feedback mf "
            "JOIN jsonb_array_elements_text(mf.page_ids) e ON true "
            "WHERE mf.learned_memory_id IS NOT NULL AND (e)::int = %s LIMIT 6",
            (page_id,),
        ).fetchall()
        mids = [r["mid"] for r in rows if r["mid"] is not None]
        if mids:
            mrows = conn.execute(
                "SELECT id, key, value FROM memory WHERE id = ANY(%s)",
                (mids,),
            ).fetchall()
            items = [{"id": f"f{r['id']}", "type": "fact",
                      "label": ((r["value"] or r["key"] or "")[:40])} for r in mrows]
            if items:
                rels.append({"kind": "cite", "title": "Cited by facts",
                             "why": "Taught fact(s) reference this page.", "items": items})
    except Exception as e:
        print(f"[rel] page cite failed: {e!r}")
    return rels


def _rel_fact(mem_id: int, conn) -> list[dict]:
    """Grouped+explained relations for a fact node. Fail-soft; [] if no cites."""
    rels: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT DISTINCT (e)::int AS page_id "
            "FROM message_feedback mf "
            "JOIN jsonb_array_elements_text(mf.page_ids) e ON true "
            "WHERE mf.learned_memory_id = %s LIMIT 8",
            (mem_id,),
        ).fetchall()
        pids = [r["page_id"] for r in rows if r["page_id"] is not None]
        if not pids:
            return []
        prows = conn.execute(
            "SELECT p.id, p.doc_id, p.page_no, d.name AS doc_name "
            "FROM pages p JOIN docs d ON d.id = p.doc_id "
            "WHERE p.id = ANY(%s)",
            (pids,),
        ).fetchall()
        items = []
        seen_docs: set[int] = set()
        for pr in prows:
            friendly = _friendly_doc(pr["doc_name"])
            items.append({"id": f"p{pr['id']}", "type": "page",
                          "label": _page_label(friendly, pr["page_no"])})
            if pr["doc_id"] not in seen_docs:
                seen_docs.add(pr["doc_id"])
                items.append({"id": f"d{pr['doc_id']}", "type": "doc",
                              "label": friendly})
        if items:
            rels.append({"kind": "cite", "title": "Based on",
                         "why": "This fact was taught from / points to these pages.",
                         "items": items})
    except Exception as e:
        print(f"[rel] fact cite failed: {e!r}")
    return rels


def _node_doc(doc_id: int) -> dict | None:
    """Rich detail for a doc node ('d{id}'). Fail-soft on each sub-query."""
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT d.id, d.name, d.lang, d.status, d.created_at, "
            "coalesce(d.page_count, (SELECT count(*) FROM pages p WHERE p.doc_id = d.id)) AS page_count, "
            "(SELECT p.id FROM pages p WHERE p.doc_id = d.id ORDER BY p.page_no, p.id LIMIT 1) AS cover_page_id "
            "FROM docs d WHERE d.id = %s",
            (doc_id,),
        ).fetchone()
        if not doc:
            return None
        summary = ""
        try:
            w = conn.execute(
                "SELECT summary FROM doc_wiki WHERE doc_id = %s", (doc_id,)
            ).fetchone()
            summary = (w or {}).get("summary") or ""
        except Exception as e:
            print(f"[node] doc summary failed: {e!r}")
        used = None
        try:
            used = conn.execute(
                "SELECT count(DISTINCT m.id) AS used_count, max(m.created_at) AS last_used_at "
                "FROM messages m "
                "JOIN jsonb_array_elements(m.pages) e ON true "
                "JOIN pages p ON p.id = (e->>'page_id')::int "
                "WHERE m.role = 'bot' AND p.doc_id = %s",
                (doc_id,),
            ).fetchone()
        except Exception as e:
            print(f"[node] doc usage failed: {e!r}")
        nbr_pages = []
        try:
            nbr_pages = conn.execute(
                "SELECT id, page_no FROM pages WHERE doc_id = %s "
                "ORDER BY page_no, id LIMIT 8",
                (doc_id,),
            ).fetchall()
        except Exception as e:
            print(f"[node] doc pages failed: {e!r}")
        try:
            relations = _rel_doc(doc_id, conn)
        except Exception as e:
            print(f"[node] doc relations failed: {e!r}")
            relations = []

    used_count = (used or {}).get("used_count") or 0
    stats = [
        {"label": "Pages", "value": str(doc["page_count"] or 0)},
        {"label": "Language", "value": (doc["lang"] or "?").upper()},
        {"label": "Status", "value": doc["status"] or "?"},
        {"label": "Used", "value": f"{used_count}×"},
    ]
    neighbors = [
        {"id": f"p{p['id']}", "type": "page",
         "label": f"p.{p['page_no']}" if p["page_no"] is not None else "page"}
        for p in nbr_pages
    ]
    lang = (doc["lang"] or "?").upper()
    return {
        "type": "doc",
        "id": f"d{doc['id']}",
        "title": _friendly_doc(doc["name"]),
        "subtitle": f"Document · {doc['page_count'] or 0} pages · {lang}",
        "summary": summary,
        "image_url": (f"/api/pages/{doc['cover_page_id']}"
                      if doc["cover_page_id"] else None),
        "doc_id": doc["id"],
        "page_no": None,
        "stats": stats,
        "neighbors": neighbors,
        "relations": relations,
    }


def _node_page(page_id: int) -> dict | None:
    """Rich detail for a page node ('p{id}'). Fail-soft on each sub-query."""
    with get_conn() as conn:
        pg = conn.execute(
            "SELECT p.id, p.doc_id, p.page_no, p.text, d.name AS doc_name "
            "FROM pages p JOIN docs d ON d.id = p.doc_id WHERE p.id = %s",
            (page_id,),
        ).fetchone()
        if not pg:
            return None
        nsummary = ""
        try:
            n = conn.execute(
                "SELECT summary FROM nodes WHERE doc_id = %s AND page_no = %s "
                "ORDER BY id LIMIT 1",
                (pg["doc_id"], pg["page_no"]),
            ).fetchone()
            nsummary = (n or {}).get("summary") or ""
        except Exception as e:
            print(f"[node] page section failed: {e!r}")
        siblings = []
        try:
            siblings = conn.execute(
                "SELECT id, page_no FROM pages "
                "WHERE doc_id = %s AND id <> %s "
                "ORDER BY abs(coalesce(page_no,0) - %s), page_no, id LIMIT 6",
                (pg["doc_id"], page_id, pg["page_no"] or 0),
            ).fetchall()
        except Exception as e:
            print(f"[node] page siblings failed: {e!r}")
        try:
            relations = _rel_page(page_id, pg["doc_id"], pg["page_no"],
                                  pg["doc_name"], conn)
        except Exception as e:
            print(f"[node] page relations failed: {e!r}")
            relations = []

    friendly = _friendly_doc(pg["doc_name"])
    body = (pg["text"] or "").strip()
    summary = (body[:400] or nsummary)
    chars = len(body)
    stats = [
        {"label": "Page", "value": (str(pg["page_no"]) if pg["page_no"] is not None else "?")},
        {"label": "Characters", "value": str(chars)},
        {"label": "Document", "value": friendly},
    ]
    neighbors = [{"id": f"d{pg['doc_id']}", "type": "doc", "label": friendly}]
    neighbors += [
        {"id": f"p{s['id']}", "type": "page",
         "label": f"p.{s['page_no']}" if s["page_no"] is not None else "page"}
        for s in siblings
    ]
    pno = pg["page_no"] if pg["page_no"] is not None else "?"
    return {
        "type": "page",
        "id": f"p{pg['id']}",
        "title": f"{friendly} · p.{pno}",
        "subtitle": f"Page {pno} of {friendly}",
        "summary": summary,
        "image_url": f"/api/pages/{pg['id']}",
        "doc_id": pg["doc_id"],
        "page_no": pg["page_no"],
        "stats": stats,
        "neighbors": neighbors,
        "relations": relations,
    }


def _node_fact(mem_id: int) -> dict | None:
    """Rich detail for a fact node ('f{id}'). Fail-soft on each sub-query."""
    with get_conn() as conn:
        f = conn.execute(
            "SELECT id, key, value, source, created_by, status, "
            "cited_count, last_cited_at FROM memory WHERE id = %s",
            (mem_id,),
        ).fetchone()
        if not f:
            return None
        cited_pages = []
        try:
            cited_pages = conn.execute(
                "SELECT DISTINCT (e)::int AS page_id "
                "FROM message_feedback mf "
                "JOIN jsonb_array_elements_text(mf.page_ids) e ON true "
                "WHERE mf.learned_memory_id = %s LIMIT 8",
                (mem_id,),
            ).fetchall()
        except Exception as e:
            print(f"[node] fact cites failed: {e!r}")
        # resolve those pages -> friendly labels + their docs
        neighbors = []
        seen_docs: set[int] = set()
        if cited_pages:
            try:
                pids = [r["page_id"] for r in cited_pages]
                prows = conn.execute(
                    "SELECT p.id, p.doc_id, p.page_no, d.name AS doc_name "
                    "FROM pages p JOIN docs d ON d.id = p.doc_id "
                    "WHERE p.id = ANY(%s)",
                    (pids,),
                ).fetchall()
                for pr in prows:
                    friendly = _friendly_doc(pr["doc_name"])
                    neighbors.append({
                        "id": f"p{pr['id']}", "type": "page",
                        "label": (f"{friendly} · p.{pr['page_no']}"
                                  if pr["page_no"] is not None else friendly),
                    })
                    if pr["doc_id"] not in seen_docs:
                        seen_docs.add(pr["doc_id"])
                        neighbors.append({
                            "id": f"d{pr['doc_id']}", "type": "doc",
                            "label": friendly,
                        })
            except Exception as e:
                print(f"[node] fact page resolve failed: {e!r}")
        try:
            relations = _rel_fact(mem_id, conn)
        except Exception as e:
            print(f"[node] fact relations failed: {e!r}")
            relations = []

    value = f["value"] or ""
    key = f["key"] or ""
    title = (key or value)[:60]
    src = f["source"] or "human"
    status = f["status"] or "active"
    src_label = "Taught fact" if src != "chat" else "Learned fact"
    stats = [
        {"label": "Source", "value": src},
        {"label": "Status", "value": status},
        {"label": "Cited", "value": f"{f['cited_count'] or 0}×"},
        {"label": "Last cited", "value": _rel_time(f["last_cited_at"])},
    ]
    return {
        "type": "fact",
        "id": f"f{f['id']}",
        "title": title,
        "subtitle": f"{src_label} · {status}",
        "summary": (f"{key}: {value}" if key else value),
        "image_url": None,
        "doc_id": None,
        "page_no": None,
        "stats": stats,
        "neighbors": neighbors,
        "relations": relations,
    }


@router.get("/brain/node", dependencies=[Depends(require_key)])
def brain_node(id: str = ""):
    """Rich detail for a single namespaced graph node (left side-panel).

    `id` is namespaced: 'd{doc_id}', 'p{page_id}', 'f{memory_id}'. Returns
    {type,id,title,subtitle,summary,image_url,doc_id,page_no,stats[],neighbors[],
     relations[]} (relations = grouped+explained edges: kind/title/why/items).
    Unknown / missing → {"type": null}. Each sub-query is fail-soft."""
    nsid = (id or "").strip()
    if len(nsid) < 2 or nsid[0] not in ("d", "p", "f") or not nsid[1:].isdigit():
        return {"type": None}
    kind, num = nsid[0], int(nsid[1:])
    try:
        if kind == "d":
            res = _node_doc(num)
        elif kind == "p":
            res = _node_page(num)
        else:
            res = _node_fact(num)
    except Exception as e:
        print(f"[node] {nsid} failed: {e!r}")
        return {"type": None}
    if res is None:
        return {"type": None}
    return res


@router.get("/pages/{page_id}/md", dependencies=[Depends(current_principal)])
def get_page_md(page_id: int):
    """Compiled clean markdown for a page (Text view in the source drawer).
    Falls back to raw vision/text if the page hasn't been compiled yet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.doc_id, p.page_no, d.name AS doc_name, "
            "m.md AS md, p.text AS raw "
            "FROM pages p JOIN docs d ON d.id = p.doc_id "
            "LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no "
            "WHERE p.id = %s",
            (page_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    return {
        "page_no": row["page_no"],
        "doc_name": row["doc_name"],
        "md": row["md"] or "",
        "compiled": bool(row["md"]),
        "raw": (row["raw"] or "")[:16000] if not row["md"] else "",
    }


@router.get("/pages/{page_id}/cues", dependencies=[Depends(current_principal)])
def get_page_cues(page_id: int):
    """Visual cues captured from the screenshot at ingest (Phase 0/6.4): which
    button/field is highlighted, what to click, etc. Parsed from the page's
    'VISUAL CUES:' block. Returns {cues:[...], caption}. Empty if none captured."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT vision_text FROM pages WHERE id = %s", (page_id,)
        ).fetchone()
    txt = (row or {}).get("vision_text") or "" if row else ""
    cues: list[str] = []
    caption = ""
    if "VISUAL CUES:" in txt:
        after = txt.split("VISUAL CUES:", 1)[1]
        block = after.split("CAPTION:", 1)[0]
        for line in block.splitlines():
            s = line.strip().lstrip("-*•").strip()
            if not s or s.lower() == "none":
                continue
            cues.append(s)
    if "CAPTION:" in txt:
        caption = txt.split("CAPTION:", 1)[1].strip().splitlines()[0].strip() if txt.split("CAPTION:", 1)[1].strip() else ""
    return {"cues": cues, "caption": caption}


@router.get("/facts/{fact_id}", dependencies=[Depends(require_key)])
def fact_detail(fact_id: int):
    """Design B1 rich fact detail: provenance + citation log + related facts/docs.

    Returns:
      id, key, value, source, status, created_by, created_at, cited_count,
      last_cited_at, origin (where it came from), cited_in (recent answers that
      used this fact), related (linked pages/docs/facts from _rel_fact + cited
      page neighbours). All sub-queries are fail-soft."""
    with get_conn() as conn:
        f = conn.execute(
            "SELECT id, key, value, source, status, created_by, created_at, "
            "cited_count, last_cited_at, origin_question "
            "FROM memory WHERE id = %s",
            (fact_id,),
        ).fetchone()
        if not f:
            raise HTTPException(status_code=404, detail={"error": "not found"})

        # ---- origin: where the fact came from ----
        origin = None
        try:
            oq = f.get("origin_question") or None
            src = f.get("source") or "human"
            if oq:
                origin = {
                    "kind": src,
                    "question": oq,
                    "at": f["created_at"].isoformat() if f.get("created_at") else None,
                    "by": f.get("created_by"),
                }
            elif src == "feedback":
                # look up the originating message_feedback row via learned_memory_id
                fb = conn.execute(
                    "SELECT question, created_at FROM message_feedback "
                    "WHERE learned_memory_id = %s ORDER BY id DESC LIMIT 1",
                    (fact_id,),
                ).fetchone()
                if fb:
                    origin = {
                        "kind": "feedback",
                        "question": fb.get("question"),
                        "at": fb["created_at"].isoformat() if fb.get("created_at") else None,
                        "by": f.get("created_by"),
                    }
            # hand-taught facts with no origin_question → origin stays None
        except Exception as e:
            print(f"[fact_detail] origin failed: {e!r}")

        # ---- cited_in: recent fact_citation rows, newest first, limit 8 ----
        cited_in: list[dict] = []
        try:
            crows = conn.execute(
                "SELECT question, at FROM fact_citation "
                "WHERE fact_id = %s ORDER BY at DESC LIMIT 8",
                (fact_id,),
            ).fetchall()
            cited_in = [
                {
                    "question": r.get("question"),
                    "at": r["at"].isoformat() if r.get("at") else None,
                }
                for r in crows
            ]
        except Exception as e:
            print(f"[fact_detail] cited_in failed: {e!r}")

        # ---- related: _rel_fact neighbours + cited-page docs (deduped, cap 12) ----
        related: list[dict] = []
        seen_ids: set[str] = set()
        try:
            rel_groups = _rel_fact(fact_id, conn)
            for grp in rel_groups:
                for item in grp.get("items") or []:
                    nsid = item.get("id") or ""
                    if nsid and nsid not in seen_ids:
                        seen_ids.add(nsid)
                        related.append({
                            "id": nsid,
                            "type": item.get("type"),
                            "label": item.get("label"),
                        })
                        if len(related) >= 12:
                            break
                if len(related) >= 12:
                    break
        except Exception as e:
            print(f"[fact_detail] related failed: {e!r}")
        # supplement with pages that co-cited (from message_feedback page_ids) if short
        if len(related) < 12:
            try:
                prows = conn.execute(
                    "SELECT DISTINCT (e)::int AS page_id "
                    "FROM message_feedback mf "
                    "JOIN jsonb_array_elements_text(mf.page_ids) e ON true "
                    "WHERE mf.learned_memory_id = %s LIMIT 8",
                    (fact_id,),
                ).fetchall()
                pids = [r["page_id"] for r in prows if r["page_id"] is not None]
                if pids:
                    pr_rows = conn.execute(
                        "SELECT p.id, p.doc_id, p.page_no, d.name AS doc_name "
                        "FROM pages p JOIN docs d ON d.id = p.doc_id "
                        "WHERE p.id = ANY(%s)",
                        (pids,),
                    ).fetchall()
                    for pr in pr_rows:
                        pid_key = f"p{pr['id']}"
                        did_key = f"d{pr['doc_id']}"
                        friendly = _friendly_doc(pr["doc_name"])
                        if pid_key not in seen_ids and len(related) < 12:
                            seen_ids.add(pid_key)
                            related.append({
                                "id": pid_key, "type": "page",
                                "label": _page_label(friendly, pr["page_no"]),
                            })
                        if did_key not in seen_ids and len(related) < 12:
                            seen_ids.add(did_key)
                            related.append({
                                "id": did_key, "type": "doc",
                                "label": friendly,
                            })
            except Exception as e:
                print(f"[fact_detail] related page-supplement failed: {e!r}")

    return {
        "id": f["id"],
        "key": f.get("key"),
        "value": f.get("value") or "",
        "source": f.get("source") or "human",
        "status": f.get("status") or "active",
        "created_by": f.get("created_by"),
        "created_at": f["created_at"].isoformat() if f.get("created_at") else None,
        "cited_count": int(f.get("cited_count") or 0),
        "last_cited_at": f["last_cited_at"].isoformat() if f.get("last_cited_at") else None,
        "origin": origin,
        "cited_in": cited_in,
        "related": related,
    }


# ======================================================================
# Embeddable widget — public session / secret token / admin key CRUD
# ======================================================================

class EmbedSessionReq(BaseModel):
    public_key: str
    visitor_id: str | None = None


class EmbedTokenReq(BaseModel):
    visitor_id: str
    name: str | None = None


class EmbedKeyReq(BaseModel):
    name: str
    allowed_origins: list[str] = []
    rate_per_min: int = 30
    greeting: str | None = None
    accent: str = "#c2683f"
    position: str = "right"
    subtitle: str | None = None
    logo_url: str | None = None
    launcher: str = "robot"
    daily_msg_cap: int = 0          # 0 = unlimited
    daily_cost_cap: float = 0       # USD/day, 0 = unlimited


class EmbedKeyPatch(BaseModel):
    name: str | None = None
    allowed_origins: list[str] | None = None
    rate_per_min: int | None = None
    greeting: str | None = None
    accent: str | None = None
    active: bool | None = None
    position: str | None = None
    subtitle: str | None = None
    logo_url: str | None = None
    launcher: str | None = None
    daily_msg_cap: int | None = None
    daily_cost_cap: float | None = None


class EmbedBrandReq(BaseModel):
    accent: str | None = None
    position: str | None = None
    greeting: str | None = None
    subtitle: str | None = None
    logo_url: str | None = None
    launcher: str | None = None
    title: str | None = None


@router.post("/embed/session")
def embed_session(req: EmbedSessionReq, request: Request):
    """PUBLIC: widget exchanges its pk_ for a short-lived visitor token.
    Gated by the request Origin against the key's allow-list."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    return embed_mod.start_session_public(req.public_key, origin, req.visitor_id)


@router.post("/embed/token")
def embed_token(req: EmbedTokenReq, authorization: str | None = Header(default=None)):
    """SERVER-TO-SERVER: customer backend exchanges sk_ for a visitor token
    bound to its own user identity (SSO passthrough)."""
    parts = (authorization or "").split(" ", 1)
    secret = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization
    if not secret:
        raise HTTPException(status_code=401, detail="missing secret key")
    return embed_mod.mint_token_secret(secret, req.visitor_id, req.name)


@router.get("/embed/keys", dependencies=[Depends(require_admin)])
def embed_keys_list():
    return {"keys": embed_mod.list_keys()}


@router.post("/embed/keys")
def embed_keys_create(req: EmbedKeyReq, user: dict = Depends(require_admin)):
    key = embed_mod.create_key(
        req.name, req.allowed_origins, user["id"],
        req.rate_per_min, req.greeting, req.accent,
        req.position, req.subtitle, req.logo_url, req.launcher,
        req.daily_msg_cap, req.daily_cost_cap,
    )
    audit_mod.log(user, "embed_key.create", "embed_key", str(key["id"]),
                  {"name": req.name})
    # secret returned ONCE — UI must show + tell admin to copy now
    return {"public_key": key["public_key"], "secret": key["secret"],
            "id": key["id"], "secret_last4": key["secret_last4"]}


@router.patch("/embed/keys/{key_id}")
def embed_keys_update(key_id: int, req: EmbedKeyPatch,
                      user: dict = Depends(require_admin)):
    row = embed_mod.update_key(key_id, **req.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(status_code=404, detail="key not found")
    audit_mod.log(user, "embed_key.update", "embed_key", str(key_id), {})
    return {"ok": True}


@router.post("/embed/keys/{key_id}/rotate")
def embed_keys_rotate(key_id: int, user: dict = Depends(require_admin)):
    secret = embed_mod.rotate_secret(key_id)
    audit_mod.log(user, "embed_key.rotate", "embed_key", str(key_id), {})
    return {"secret": secret}


@router.delete("/embed/keys/{key_id}")
def embed_keys_delete(key_id: int, user: dict = Depends(require_admin)):
    embed_mod.delete_key(key_id)
    audit_mod.log(user, "embed_key.delete", "embed_key", str(key_id), {})
    return {"ok": True}


@router.get("/embed/brand", dependencies=[Depends(require_admin)])
def embed_brand_get():
    return embed_mod.get_brand()


@router.put("/embed/brand")
def embed_brand_put(req: EmbedBrandReq, user: dict = Depends(require_admin)):
    brand = embed_mod.set_brand(req.model_dump(exclude_none=True))
    audit_mod.log(user, "embed_brand.update", "embed_brand", "1", {})
    return brand


@router.get("/embed/stats", dependencies=[Depends(require_admin)])
def embed_stats(days: int = 30):
    return embed_mod.stats(days)


@router.post("/embed/keys/{key_id}/sandbox-token", dependencies=[Depends(require_admin)])
def embed_sandbox_token(key_id: int):
    """Mint a live visitor token for in-dashboard testing (skips Origin gate)."""
    return embed_mod.sandbox_token(key_id)


# ---------------- OKF export (Open Knowledge Format bundle) ----------------
@router.get("/okf/preview", dependencies=[Depends(require_admin)])
def okf_preview():
    """File listing + sizes for the export bundle (no download)."""
    return okf_mod.preview()


@router.get("/okf/export", dependencies=[Depends(require_admin)])
def okf_export(images: bool = False):
    """Download the whole brain as a conformant OKF v0.1 gzip tarball.
    images=true also bundles page images (bigger, but round-trip keeps
    answer-with-page)."""
    data = okf_mod.build_tarball(include_images=images)
    return StreamingResponse(
        iter([data]), media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=aria-knowledge-okf.tar.gz"})


@router.post("/okf/import")
def okf_import(file: UploadFile | None = File(None), url: str | None = Form(None),
               dry_run: bool = False, user: dict = Depends(require_admin)):
    """Import an OKF bundle (.tar.gz/.tar/.zip) from an upload OR an https URL
    (a GitHub repo URL works directly). dry_run=true previews counts without
    writing. Imported docs are searchable immediately (no vision); imported facts
    land 'pending' for review. Reads the markdown body straight in — no LLM cost."""
    try:
        if file is not None:
            raw = file.file.read()
        elif url:
            raw = okf_mod.fetch_url(url)
        else:
            raise HTTPException(status_code=400, detail="provide a file or a url")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read failed: {e}")
    try:
        if dry_run:
            p = okf_mod.parse_bundle(raw)
            return {"dry_run": True, **p["counts"],
                    "documents": [d["title"] for d in p["documents"]][:50],
                    "facts": [f["title"] for f in p["facts"]][:50]}
        result = okf_mod.import_bundle(raw, created_by=user.get("email"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_mod.log(user, "okf.import", "okf", "-", result)
    notify.emit("ingest", "OKF bundle imported",
                f"{result['imported_documents']} docs · "
                f"{result['imported_facts_pending']} facts pending", "info")
    return result
