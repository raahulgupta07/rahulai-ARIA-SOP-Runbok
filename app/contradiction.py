"""Cross-document CONTRADICTION detection — Karpathy "LLM wiki" Phase 1.

The corpus is a living wiki: the same thing (a code, a threshold, a setting, a
menu path) is asserted in many SOPs/runbooks. When two docs assert DIFFERENT
values for the SAME entity+attribute, that is a real knowledge-base conflict an
editor must reconcile — not a fact to silently average or last-write-win.

This module runs at ingest of a NEW doc. For each atomic claim the new doc makes
(rows in `doc_claims`, mined by a sibling module), it pulls the EXISTING claims
about the same shared entity+attribute (via `claims.claims_for_entity`, which
excludes the new doc itself), keeps only those whose value actually DIFFERS after
light normalization, then asks the LLM — given the entity, attribute and BOTH
source sentences — whether this is a GENUINE contradiction (same thing asserted
with conflicting values) or a false match (different scope/context). Every
LLM-confirmed conflict is written to the `claim_conflict` review queue
(doc_a = the new doc, doc_b = the existing doc), deduped, and optionally
auto-resolved to the newer doc per the per-project wiki schema.

Design: new-vs-existing only (the existing corpus is the source of truth being
challenged), LLM-confirmed (cheap normalization pre-filter, model gates false
positives), review-queue output (humans reconcile). Fail-soft throughout — it
runs INSIDE the ingest pipeline and must NEVER raise. Mirrors the LLM-client +
tolerant-JSON-parse + print-and-return-0 style of entities.py.
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
    CONTRADICTION_MODEL,
)
from . import claims
from . import appcfg

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _model() -> str:
    return CONTRADICTION_MODEL or COMPILE_MODEL or CHAT_MODEL


_SYS = (
    "You audit a company knowledge base for CONTRADICTIONS. You are given an "
    "ENTITY and an ATTRIBUTE, plus a NEW claim and one or more EXISTING claims "
    "about that same entity/attribute, each with the source sentence it came "
    "from. For EACH existing candidate decide: is this a REAL contradiction "
    "(the same thing is asserted with genuinely conflicting values) or a FALSE "
    "match (the values differ because they describe a different context, scope, "
    "subset, time, or condition — not a true disagreement)? Reply ONLY as strict "
    "JSON: a list aligned by candidate index, each item "
    '{"conflict": true|false, "severity": "value_mismatch", '
    '"detail": "<one short sentence explaining the conflict or why it is not one>"}.'
)


def _norm(v) -> str:
    """Light normalization for the differ-pre-filter: strip, lowercase, collapse
    internal whitespace. '2 ' == '2' (same), '2' != '3' (differ)."""
    return re.sub(r"\s+", " ", str(v or "").strip().lower()).strip()


def _parse_json(raw: str):
    """Robustly parse the model's JSON list: strip ```json fences, tolerate
    surrounding prose, fall back to the first [...] block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _judge(entity: str, attribute: str, new_claim: dict, candidates: list) -> list:
    """ONE LLM call: classify each differing candidate vs the new claim. Returns a
    list of dicts aligned to `candidates` (conflict/severity/detail); on any parse
    failure returns [] (treated as 'no confirmed conflicts' — fail-soft)."""
    lines = [
        f"ENTITY: {entity}",
        f"ATTRIBUTE: {attribute}",
        "",
        "NEW claim:",
        f"  value: {new_claim.get('value')}",
        f"  source sentence: {(new_claim.get('raw') or '').strip()[:600]}",
        "",
        "EXISTING candidates:",
    ]
    for i, c in enumerate(candidates):
        lines.append(f"  [{i}] value: {c.get('value')}")
        lines.append(f"      source sentence: {(c.get('raw') or '').strip()[:600]}")
    user = "\n".join(lines)

    resp = _client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _SYS},
            {"role": "user", "content": user},
        ],
        max_tokens=1500,
        temperature=0,
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = _parse_json(raw)
    if not isinstance(data, list):
        return []
    return data


def detect_for_doc(doc_id: int) -> int:
    """Compare a freshly-ingested doc's atomic claims against existing shared-entity
    claims; LLM-confirm real value conflicts into the `claim_conflict` review queue.
    Returns the number of conflict rows written (0 on skip/parse-fail/error).
    Runs inside the ingest pipeline — NEVER raises."""
    try:
        if not _client:
            return 0

        schema = appcfg.get_wiki_schema()
        contra = schema.get("contradiction") or {}
        compare_attrs = {
            str(a).strip().lower()
            for a in (contra.get("compare_attributes") or [])
            if str(a).strip()
        }
        if not compare_attrs:
            return 0
        auto_newer = bool(contra.get("auto_resolve_newer"))
        status = "kept_new" if auto_newer else "pending"

        with get_conn() as conn:
            new_claims = conn.execute(
                "SELECT id, page_no, page_id, entity, entity_key, attribute, value, raw "
                "FROM doc_claims WHERE doc_id = %s",
                (doc_id,),
            ).fetchall()

        # only compare claims on the project's configured attributes (cost control)
        new_claims = [
            c for c in new_claims
            if (c.get("attribute") or "").strip().lower() in compare_attrs
        ]
        if not new_claims:
            return 0

        written = 0
        with get_conn() as conn:
            for new in new_claims:
                ekey = new.get("entity_key")
                attr = new.get("attribute")
                if not ekey or not attr:
                    continue

                existing = claims.claims_for_entity(
                    ekey, attr, exclude_doc_id=doc_id
                ) or []
                nval = _norm(new.get("value"))
                # keep only existing claims whose value genuinely differs
                candidates = [
                    e for e in existing if _norm(e.get("value")) != nval
                ]
                if not candidates:
                    continue

                try:
                    verdicts = _judge(new.get("entity") or ekey, attr, new, candidates)
                except Exception as e:
                    print(f"[contradiction] {doc_id}: judge failed for "
                          f"{ekey}/{attr}: {e!r}")
                    continue

                for i, cand in enumerate(candidates):
                    verdict = verdicts[i] if i < len(verdicts) else None
                    if not isinstance(verdict, dict) or not verdict.get("conflict"):
                        continue
                    severity = (verdict.get("severity") or "").strip() or "value_mismatch"
                    detail = (verdict.get("detail") or "").strip()[:500]

                    # dedupe: same entity/attr + same new→existing doc + same values
                    dup = conn.execute(
                        "SELECT 1 FROM claim_conflict WHERE entity_key = %s AND "
                        "attribute = %s AND doc_a = %s AND doc_b = %s AND "
                        "value_a = %s AND value_b = %s LIMIT 1",
                        (ekey, attr, doc_id, cand.get("doc_id"),
                         new.get("value"), cand.get("value")),
                    ).fetchone()
                    if dup:
                        continue

                    conn.execute(
                        "INSERT INTO claim_conflict "
                        "(entity, entity_key, attribute, "
                        " doc_a, page_a, value_a, claim_a, "
                        " doc_b, page_b, value_b, claim_b, "
                        " severity, detail, status) "
                        "VALUES (%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s)",
                        (
                            new.get("entity") or ekey, ekey, attr,
                            doc_id, new.get("page_no"), new.get("value"), new.get("id"),
                            cand.get("doc_id"), cand.get("page_no"),
                            cand.get("value"), cand.get("id"),
                            severity, detail, status,
                        ),
                    )
                    written += 1

                    # best-effort activity-feed notify (never breaks ingest)
                    try:
                        from . import notify
                        notify.emit(
                            "audit",
                            "Contradiction found",
                            f"{new.get('entity') or ekey} {attr}: "
                            f"{new.get('value')} vs {cand.get('value')}",
                            "warn",
                            dedupe_hours=6,
                        )
                    except Exception:
                        pass

        print(f"[contradiction] {doc_id}: {written} conflict(s) written")
        return written
    except Exception as e:
        print(f"[contradiction] {doc_id} failed: {e!r}")
        return 0
