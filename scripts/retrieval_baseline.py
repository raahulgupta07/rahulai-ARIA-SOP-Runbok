#!/usr/bin/env python3
"""Retrieval-quality baseline measurement harness (NEW, read-only).

WHAT IT DOES
------------
Builds a question set DYNAMICALLY from the loaded corpus (no hardcoded
questions — the corpus content is unknown), runs each question through the
app's own ``retrieve.search_pages`` and scores whether the EXPECTED document
appears in the top-6. Computes recall@6 (overall / literal / paraphrased) and
the mean rank of the first correct document, then writes a JSON report and
prints a human summary.

This is a MEASUREMENT tool only — it never writes to the DB. It reuses the
app's DB connection + OpenRouter env, so it is meant to run INSIDE the
container:

    docker exec docsensei-app python scripts/retrieval_baseline.py --label baseline

(or, via the rtk docker shim:)

    rtk proxy docker exec docsensei-app python scripts/retrieval_baseline.py --label baseline

It can also run from the repo root locally if the DB is reachable:

    python scripts/retrieval_baseline.py --label baseline

RESULT-DICT KEYS from search_pages (as of this writing):
    page_id, doc_id, doc_name, page_no, image_path, snippet, text_full
There is NO per-result score field in the public output, so this harness scores
purely on (doc_id, page_no) membership and rank — which is exactly what
recall@k / first-correct-rank need.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- make the app package importable whether run from /app, repo root, or scripts/ ---
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parent.parent
for _p in (str(_REPO_ROOT), "/app"):
    if _p not in sys.path and Path(_p).exists():
        sys.path.insert(0, _p)

try:
    from app.retrieve import search_pages
    from app.db import get_conn
except Exception as e:  # pragma: no cover - import guidance
    print(
        "ERROR: could not import app modules (app.retrieve / app.db).\n"
        "Run this from the repo root or inside the container (cwd /app):\n"
        "    docker exec docsensei-app python scripts/retrieval_baseline.py\n"
        f"Import error: {e!r}"
    )
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Question generation                                                          #
# --------------------------------------------------------------------------- #
# Generic SOP-template section titles that repeat in EVERY document and carry
# no document-specific topic — using them as questions produces ambiguous,
# non-discriminating queries. (This corpus = one shared SOP template; the only
# distinguishing topic lives in the filename, e.g. "New Site Creation".)
_STOP_TITLES = re.compile(
    r"^(table of contents|contents|overview|introduction|index|appendix|"
    r"purpose|scope|references?|revision history|document control|page \d+|"
    r"preface|roles?(\s+and\s+responsibilit(y|ies))?|responsibilit(y|ies)|"
    r"pre[\s\-]?requisites?|procedure(/?\s*steps)?|steps|dependenc(y|ies)|"
    r"do.?s|don.?.?ts?|escalations?|signatures?|definitions?|abbreviations?|"
    r"approvals?)\s*$",
    re.IGNORECASE,
)
_NOISE = re.compile(r"^[\W\d_]+$")


def _humanize_doc(name: str) -> str:
    """Turn a raw filename into a human topic phrase (mirrors docname intent).

    Strips the structured SOP code prefix so only the real topic remains, e.g.
    'SOP_IT_CMHL_AMS_GLDCENTRAL_001_New Site Creation.pdf' -> 'New Site Creation'.
    Falls back to a lighter clean if the structured pattern is absent.
    """
    base = re.sub(r"\.(pdf|png|jpe?g|docx?|md)$", "", name or "", flags=re.IGNORECASE)
    base = re.sub(r"^[0-9a-f]{6,}__", "", base)     # leading content-hash prefix
    # structured code: SOP_IT_<...>_NNN_<topic> -> keep only <topic>
    m = re.search(r"_(\d{2,4})_(.+)$", base)
    if m:
        base = m.group(2)
    else:
        base = re.sub(r"^_?\d+_", "", base)         # leading _NNN_ index
        base = re.sub(r"\bSOP\b", "", base, flags=re.IGNORECASE)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    t = t.strip(" .-—:·")
    return t


def _lc_first(p: str) -> str:
    """Lowercase only the first char unless the word is an ALL-CAPS acronym."""
    p = p.strip()
    if not p:
        return p
    first = p.split()[0]
    if first.isupper() and len(first) > 1:
        return p  # keep acronym casing (e.g. 'CAR procedure')
    return p[0].lower() + p[1:]


def _action_question(phrase: str) -> str:
    """Natural literal question from a topic phrase."""
    p = phrase.strip().rstrip(".")
    if not p:
        return ""
    low = p.lower()
    # process/issue topics read better as 'how do I do <X>' / 'where is <X>'
    if re.search(r"\b(process|issue|monitoring|job)\b", low):
        return f"How do I handle {_lc_first(p)}?"
    return f"How do I {_lc_first(p)}?"


# synonym map to build PARAPHRASED / vague variants that drop keyword overlap.
# Tuned for this ITSM/ERP SOP corpus (Gold Central: sites, users, mass price,
# batch jobs, mass input). The goal is to strip the verbatim keywords the
# document uses so a literal keyword search would struggle — exposing whether
# query expansion / rerank bridge the gap.
_PARAPHRASE_RULES = [
    (r"\bnew site creation\b", "open up a brand-new branch location"),
    (r"\bsite creation\b", "open up a branch location"),
    (r"\buser creation\b", "set up a new staff login"),
    (r"\bcreate\b|\badd\b|\bset up\b|\bsetup\b|\bregister\b", "get going"),
    (r"\bdisable\b|\bdeactivate\b|\bremove\b|\bdelete\b", "switch off"),
    (r"\bmass selling price change\b", "update lots of shelf prices at once"),
    (r"\bselling price\b", "shelf price"),
    (r"\bmass input\b", "bulk entry"),
    (r"\bnight batch job\b", "overnight scheduled task"),
    (r"\bbatch job\b", "scheduled task"),
    (r"\bmonitoring\b", "keeping an eye on"),
    (r"\border\b", "purchase request"),
    (r"\bpromotion\b", "discount campaign"),
    (r"\bpurchase price\b", "cost we pay suppliers"),
    (r"\bissue\b", "problem"),
    (r"\bconfigure\b|\bset\b", "change the settings for"),
    (r"\bsite\b", "location"),
    (r"\buser\b|\baccount\b", "person's login"),
    (r"\bpassword\b", "sign-in code"),
    (r"\bcode\b", "reference number"),
]


def _paraphrase(phrase: str) -> str | None:
    """Rewrite a topic phrase with synonyms / vaguer wording, dropping keyword
    overlap so a keyword-only search would struggle. Returns None if nothing
    changed (so we don't emit a fake 'paraphrase' identical to the literal)."""
    p = " " + phrase.lower() + " "
    changed = False
    for pat, repl in _PARAPHRASE_RULES:
        new = re.sub(pat, repl, p)
        if new != p:
            p = new
            changed = True
    p = re.sub(r"\s+", " ", p).strip()
    if not changed or not p:
        return None
    return f"What's the process to {p}?"


def build_questions(max_per_doc: int = 3, target_total: int = 20) -> list[dict]:
    """Derive a tagged question set from the corpus.

    Strategy (corpus-aware): the per-document topic is usually carried by the
    FILENAME (e.g. "New Site Creation"), while PageIndex node titles in this
    corpus are a shared generic SOP template (Preface/Scope/Procedure/...). So:
      • literal Q + a paraphrased sibling come from the humanized DOC NAME
        (specific, discriminating);
      • any node title that survives the generic-template stoplist (i.e. a
        document-specific section) adds an extra literal Q.

    Each item: {q, expected_doc_id, expected_doc_name, kind: literal|paraphrase,
                source: docname|title}.
    """
    with get_conn() as conn:
        docs = conn.execute(
            "SELECT id, name, COALESCE(category,'') AS category "
            "FROM docs WHERE status = 'ready' ORDER BY id"
        ).fetchall()
        specific_titles: dict[int, list[str]] = {}
        for d in docs:
            rows = conn.execute(
                "SELECT title FROM nodes WHERE doc_id = %s AND title IS NOT NULL "
                "ORDER BY page_no NULLS LAST, id",
                (d["id"],),
            ).fetchall()
            titles = []
            for r in rows:
                t = _clean_title(r["title"])
                if not t or len(t) < 4 or _STOP_TITLES.match(t) or _NOISE.match(t):
                    continue  # drop generic SOP-template sections
                if t.lower() not in [x.lower() for x in titles]:
                    titles.append(t)
            specific_titles[d["id"]] = titles

    questions: list[dict] = []
    para_quota = max(4, target_total // 4)  # aim ~25% paraphrased
    para_count = 0

    for d in docs:
        doc_id, name = d["id"], d["name"]
        topic = _humanize_doc(name)
        made = 0

        # 1) PRIMARY literal Q from the document's filename topic (discriminating)
        if topic:
            questions.append({
                "q": _action_question(topic) or f"How do I {topic.lower()}?",
                "expected_doc_id": doc_id,
                "expected_doc_name": name,
                "kind": "literal",
                "source": "docname",
            })
            made += 1

            # 2) PARAPHRASED sibling — synonym-swapped, low keyword overlap
            if para_count < para_quota:
                pq = _paraphrase(topic)
                if pq:
                    questions.append({
                        "q": pq,
                        "expected_doc_id": doc_id,
                        "expected_doc_name": name,
                        "kind": "paraphrase",
                        "source": "docname",
                    })
                    para_count += 1
                    made += 1

        # 3) EXTRA literal Qs from any document-specific node titles
        for t in specific_titles.get(doc_id, []):
            if made >= max_per_doc:
                break
            q = _action_question(t)
            if not q:
                continue
            questions.append({
                "q": q,
                "expected_doc_id": doc_id,
                "expected_doc_name": name,
                "kind": "literal",
                "source": "title",
            })
            made += 1

    return questions


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #
def run_question(item: dict, k: int = 6) -> dict:
    """Run one question, capture top-k and score the hit + first-correct rank."""
    q = item["q"]
    expected = item["expected_doc_id"]
    try:
        results = search_pages(q, k=k) or []
    except Exception as e:
        return {
            **item,
            "error": repr(e),
            "top6": [],
            "hit": False,
            "first_correct_rank": None,
        }

    top = []
    first_rank = None
    for i, r in enumerate(results[:k]):
        rid = r.get("doc_id")
        top.append({
            "rank": i + 1,
            "doc_id": rid,
            "doc_name": r.get("doc_name"),
            "page_no": r.get("page_no"),
            "page_id": r.get("page_id"),
        })
        if first_rank is None and rid == expected:
            first_rank = i + 1

    return {
        **item,
        "top6": top,
        "hit": first_rank is not None,
        "first_correct_rank": first_rank,
    }


def aggregate(results: list[dict]) -> dict:
    def _metrics(subset: list[dict]) -> dict:
        n = len(subset)
        hits = [r for r in subset if r["hit"]]
        recall = (len(hits) / n) if n else 0.0
        ranks = [r["first_correct_rank"] for r in hits if r["first_correct_rank"]]
        mean_rank = (sum(ranks) / len(ranks)) if ranks else None
        return {
            "n": n,
            "hits": len(hits),
            "recall_at_6": round(recall, 4),
            "mean_first_correct_rank": round(mean_rank, 3) if mean_rank else None,
        }

    literal = [r for r in results if r["kind"] == "literal"]
    para = [r for r in results if r["kind"] == "paraphrase"]
    return {
        "overall": _metrics(results),
        "literal": _metrics(literal),
        "paraphrased": _metrics(para),
    }


def print_summary(label: str, agg: dict, n_docs: int) -> None:
    def fmt(m):
        mr = m["mean_first_correct_rank"]
        mr = f"{mr:.2f}" if mr is not None else "  -"
        return f"{m['recall_at_6']*100:5.1f}%  ({m['hits']:>2}/{m['n']:<2})   mean_rank={mr}"

    print()
    print(f"=== Retrieval baseline [{label}] — {n_docs} ready docs ===")
    print(f"{'subset':<14} {'recall@6':>8}              mean first-correct rank")
    print("-" * 64)
    print(f"{'overall':<14} {fmt(agg['overall'])}")
    print(f"{'literal':<14} {fmt(agg['literal'])}")
    print(f"{'paraphrased':<14} {fmt(agg['paraphrased'])}")
    print("-" * 64)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Retrieval-quality baseline harness")
    ap.add_argument("--label", default="baseline",
                    help="output label, e.g. baseline|phase1|phase2 "
                         "(writes scripts/retrieval_<label>.json)")
    ap.add_argument("--k", type=int, default=6, help="top-k to retrieve/score")
    ap.add_argument("--max-per-doc", type=int, default=3,
                    help="max questions derived per document")
    ap.add_argument("--target-total", type=int, default=20,
                    help="approx total questions to aim for")
    args = ap.parse_args()

    questions = build_questions(max_per_doc=args.max_per_doc,
                                target_total=args.target_total)
    if not questions:
        print("No questions could be derived — is any document loaded with "
              "status='ready' and PageIndex nodes? Aborting.")
        return 1

    n_docs = len({q["expected_doc_id"] for q in questions})
    print(f"Derived {len(questions)} questions across {n_docs} docs. Running...")

    results = []
    for i, item in enumerate(questions, 1):
        r = run_question(item, k=args.k)
        flag = "HIT " if r["hit"] else "miss"
        rank = r["first_correct_rank"] or "-"
        print(f"  [{i:>2}/{len(questions)}] {flag} rank={rank:<2} "
              f"({r['kind'][:4]}) {r['q'][:70]}")
        results.append(r)

    agg = aggregate(results)

    out = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "k": args.k,
        "n_docs": n_docs,
        "n_questions": len(questions),
        "metrics": agg,
        "questions": results,
    }

    out_path = _HERE.parent / f"retrieval_{args.label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path}")

    print_summary(args.label, agg, n_docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
