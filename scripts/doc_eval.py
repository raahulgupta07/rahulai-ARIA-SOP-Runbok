#!/usr/bin/env python3
"""Per-document answer-accuracy self-eval (UAT) — CLI wrapper.

Thin wrapper over app.doc_eval.run_eval (the dashboard Accuracy page uses the
same module). Runs each doc's mined golden Q&A through the real answer pipeline
and prints + persists a grounded / right-doc / page-hit / faithful scorecard.

Run INSIDE the container:
  docker exec -w /app docsensei-app sh -c 'PYTHONPATH=/app python scripts/doc_eval.py'
Optional: EVAL_MAX_Q (default 6) caps questions per doc.
"""
import json
import os

from app.doc_eval import run_eval


def main():
    rep = run_eval(int(os.getenv("EVAL_MAX_Q", "6")))
    for d in rep["docs"]:
        print(f"\n[doc {d['doc_id']}] {d['name'][:46]}")
        for r in d["rows"]:
            flag = "OK " if (r["grounded"] and r["right_doc"]) else "XX "
            print(f"   {flag} grnd={int(r['grounded'])} doc={int(r['right_doc'])} "
                  f"pg={int(r['page_hit'])} faith={int(r['faithful'])} | {r['q'][:54]}")
        p = d["pct"]
        print(f"   -> grounded {p['grounded']}%  right-doc {p['right_doc']}%  "
              f"page-hit {p['page_hit']}%  faithful {p['faithful']}%")
    for g in rep["coverage_gaps"]:
        print(f"\n[doc {g['doc_id']}] {g['name'][:46]}  — NO golden Q&A (gap)")
    o = rep["overall"]
    print("\n" + "=" * 56)
    print(f"OVERALL ({rep['questions']} Q) grounded {o['grounded']}%  "
          f"right-doc {o['right_doc']}%  page-hit {o['page_hit']}%  faithful {o['faithful']}%")
    print(json.dumps(rep["overall"]))


if __name__ == "__main__":
    main()
