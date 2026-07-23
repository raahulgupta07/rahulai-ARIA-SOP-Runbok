"""Seed CASUAL / broken-English question variants for existing Q&A pairs.

Real users write "promo price not show error", not "What should I do when the
Promotion Mass Input fails?". The Q&A bank serves by trigram similarity
(>= AUTO_QA_SERVE_MIN_SIM) against the stored QUESTION text, so formal mined
questions never match casual asks. This script rewrites each doc's active
questions into casual variants (ONE LLM call per doc) and banks them against
the SAME answer/pages — the instant-serve path then covers casual phrasing.

Idempotent: add_qa dedupes by normalised question + trigram. Variants use
source='qa' so a corpus re-mine (DELETE WHERE source='qa') clears them too.

Run inside the app container:
    python scripts/seed_casual_qa.py [--dry-run]
"""
import json
import re
import sys

sys.path.insert(0, ".")

from app.db import get_conn          # noqa: E402
from app import qa                   # noqa: E402
from app.retrieve import _client     # noqa: E402
from app.config import CHAT_MODEL    # noqa: E402

DRY = "--dry-run" in sys.argv
PER_Q = 1  # casual variants per question

_SYS = (
    "You rewrite formal IT-runbook FAQ questions into the CASUAL, broken-English "
    "way a busy Myanmar retail/IT staff member would actually type them: short, "
    "no articles, maybe a typo, symptom-first (e.g. 'What should I do when the "
    "Promotion Mass Input fails?' -> 'promo price not show error'). Keep the "
    "topic unmistakable; do not invent new topics. Reply ONLY JSON: "
    '{"variants": [{"i": <index>, "q": "<casual question>"}]}'
)


def main() -> None:
    if not _client:
        print("no LLM client configured"); return
    with get_conn() as conn:
        docs = conn.execute(
            "SELECT id, name FROM docs WHERE status IN ('ready','ready_lite') ORDER BY id"
        ).fetchall()
    total = 0
    for d in docs:
        with get_conn() as conn:
            pairs = conn.execute(
                "SELECT id, question, answer, page_ids FROM qa_pairs "
                "WHERE doc_id=%s AND status='active' AND question NOT LIKE '%%[casual]%%' "
                "ORDER BY id", (d["id"],)).fetchall()
        if not pairs:
            continue
        listing = "\n".join(f"[{i}] {p['question']}" for i, p in enumerate(pairs))
        try:
            r = _client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[{"role": "system", "content": _SYS},
                          {"role": "user", "content":
                           f"Runbook: {d['name']}\nQuestions:\n{listing}\n\n"
                           f"Give {PER_Q} casual variant per question."}],
                max_tokens=900, temperature=0.4,
            )
            raw = (r.choices[0].message.content or "").strip()
            raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
            variants = json.loads(raw).get("variants") or []
        except Exception as e:
            print(f"doc {d['id']}: LLM failed {e!r}"); continue
        added = 0
        for v in variants:
            try:
                i = int(v.get("i")); vq = (v.get("q") or "").strip()
            except Exception:
                continue
            if not vq or not (0 <= i < len(pairs)):
                continue
            p = pairs[i]
            if DRY:
                print(f"  would add: {vq!r} -> pair {p['id']}"); added += 1; continue
            nid = qa.add_qa(vq, p["answer"], source="qa", status="active",
                            confidence=0.8, doc_id=d["id"],
                            page_ids=p["page_ids"], origin="casual-variant")
            if nid:
                added += 1
        total += added
        print(f"doc {d['id']} {d['name'][:50]}: +{added} casual variants")
    print(f"TOTAL: {total}")


if __name__ == "__main__":
    main()
