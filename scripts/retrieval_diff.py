#!/usr/bin/env python3
"""Diff two retrieval_<label>.json reports (e.g. baseline vs phase1).

    python scripts/retrieval_diff.py scripts/retrieval_baseline.json scripts/retrieval_phase1.json
    # or by label (resolved against scripts/):
    python scripts/retrieval_diff.py baseline phase1

Prints the delta in recall@6 (overall / literal / paraphrased) and the change
in mean first-correct rank. Read-only; touches no DB.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(arg: str) -> dict:
    p = Path(arg)
    if not p.exists():
        cand = _HERE / f"retrieval_{arg}.json"
        if cand.exists():
            p = cand
    if not p.exists():
        print(f"ERROR: report not found: {arg}")
        sys.exit(2)
    return json.loads(p.read_text())


def _fmt_pct(x: float | None) -> str:
    return f"{x*100:5.1f}%" if x is not None else "   - "


def _fmt_rank(x: float | None) -> str:
    return f"{x:5.2f}" if x is not None else "   - "


def _delta_pct(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "   - "
    d = (b - a) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}pp"


def _delta_rank(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "   - "
    d = b - a
    sign = "+" if d >= 0 else ""
    # lower rank = better, so flag direction
    arrow = "(better)" if d < 0 else ("(worse)" if d > 0 else "(same)")
    return f"{sign}{d:.2f} {arrow}"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: retrieval_diff.py <old.json|label> <new.json|label>")
        return 2
    a = _load(sys.argv[1])
    b = _load(sys.argv[2])

    la, lb = a.get("label", "old"), b.get("label", "new")
    ma, mb = a["metrics"], b["metrics"]

    print()
    print(f"Retrieval diff:  {la}  ->  {lb}")
    print(f"  {la}: {a.get('n_questions','?')} qs / {a.get('n_docs','?')} docs "
          f"@ {a.get('generated_at','?')}")
    print(f"  {lb}: {b.get('n_questions','?')} qs / {b.get('n_docs','?')} docs "
          f"@ {b.get('generated_at','?')}")
    print()
    print(f"{'subset':<14} {'recall ' + la[:6]:>12} {'recall ' + lb[:6]:>12} "
          f"{'delta':>9}")
    print("-" * 52)
    for key in ("overall", "literal", "paraphrased"):
        ra = ma[key]["recall_at_6"]
        rb = mb[key]["recall_at_6"]
        print(f"{key:<14} {_fmt_pct(ra):>12} {_fmt_pct(rb):>12} "
              f"{_delta_pct(ra, rb):>9}")
    print()
    print(f"{'subset':<14} {'rank ' + la[:6]:>12} {'rank ' + lb[:6]:>12} "
          f"{'delta':>16}")
    print("-" * 60)
    for key in ("overall", "literal", "paraphrased"):
        ka = ma[key]["mean_first_correct_rank"]
        kb = mb[key]["mean_first_correct_rank"]
        print(f"{key:<14} {_fmt_rank(ka):>12} {_fmt_rank(kb):>12} "
              f"{_delta_rank(ka, kb):>16}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
