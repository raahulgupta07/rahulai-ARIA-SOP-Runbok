"""Dream cycle — nightly self-improvement pass (gbrain-style "24/7 dream cycle").

While the system is idle, ONE leader-gated daemon consolidates the brain so the
next day's answers are better, with no human in the loop. Every step is a plain
read/write over data we already have (no embeddings, no ML) and is fail-soft —
a failing step is skipped, never crashes the pass. Each run is logged to
dream_runs (stats + touched ids) so the Brain-health UI can show what changed
and which docs got linked tonight, and a digest notification is emitted.

Steps (each returns count + the ids it touched):
  1. dedup facts        — fold exact normalized duplicates into one survivor
  2. promote pending    — high-confidence, aged pending facts → active
  3. retire stale       — active + never-cited + old auto-facts → 'stale'
  4. resolve conflicts  — refresh kb_lint, optionally auto-pick a winner
  5. score salience     — recompute pages.salience from citation usage
  6. auto-link entities — zero-LLM cross-doc edges (autolink.link_all)
  7. fill gaps          — demand/coverage gaps → grounded Q&A (optional, LLM)

Config is read live from appcfg.get_dream() (DB overrides env) so an admin can
flip behaviour without a redeploy.
"""
import datetime
import json
import threading
import time

from .db import get_conn, log_ingest
from . import notify
from . import appcfg

_started = False


def _cfg() -> dict:
    """Effective dream config (DB over env). Fail-soft to env via appcfg."""
    try:
        return appcfg.get_dream()
    except Exception:
        from . import config as c
        return {
            "enabled": c.DREAM_ENABLED, "interval_h": c.DREAM_INTERVAL_H,
            "stale_days": c.DREAM_STALE_DAYS, "promote_age_h": c.DREAM_PROMOTE_AGE_H,
            "promote_min_conf": c.AUTO_APPROVE_MIN_CONF,
            "auto_resolve": c.DREAM_AUTO_RESOLVE, "gap_fill": c.DREAM_GAP_FILL,
            "autolink": c.DREAM_AUTOLINK,
        }


# --------------------------------------------------------------------------- #
# step 1 — dedup facts                                                         #
# --------------------------------------------------------------------------- #
def _dedup_facts() -> tuple[int, list[int]]:
    """Fold facts sharing a normalized dedupe_key into one survivor (keeps the
    others' cited_count; losers → status='merged'). Returns (count, merged_ids)."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, status, cited_count, dedupe_key FROM memory "
                "WHERE dedupe_key IS NOT NULL AND btrim(dedupe_key) <> '' "
                "  AND status IN ('active', 'pending')"
            ).fetchall()
            groups: dict[str, list] = {}
            for r in rows:
                groups.setdefault(r["dedupe_key"], []).append(r)
            merged_ids: list[int] = []
            for entries in groups.values():
                if len(entries) < 2:
                    continue
                entries.sort(key=lambda r: (r["status"] != "active",
                                            -(r["cited_count"] or 0), r["id"]))
                keep, losers = entries[0], entries[1:]
                extra = sum((r["cited_count"] or 0) for r in losers)
                conn.execute(
                    "UPDATE memory SET cited_count = cited_count + %s WHERE id = %s",
                    (extra, keep["id"]),
                )
                lids = [r["id"] for r in losers]
                conn.execute("UPDATE memory SET status = 'merged' WHERE id = ANY(%s)", (lids,))
                merged_ids += lids
            return len(merged_ids), merged_ids
    except Exception as e:
        print(f"[dream] dedup_facts skipped: {e!r}")
        return 0, []


# --------------------------------------------------------------------------- #
# step 2 — promote aged, confident pending facts                              #
# --------------------------------------------------------------------------- #
def _promote_pending(min_conf: float, age_h: int) -> tuple[int, list[int]]:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "UPDATE memory SET status = 'active' "
                "WHERE status = 'pending' AND confidence IS NOT NULL "
                "  AND confidence >= %s "
                "  AND created_at < now() - make_interval(hours => %s) RETURNING id",
                (min_conf, age_h),
            ).fetchall()
            ids = [r["id"] for r in rows]
            return len(ids), ids
    except Exception as e:
        print(f"[dream] promote_pending skipped: {e!r}")
        return 0, []


# --------------------------------------------------------------------------- #
# step 3 — retire stale, never-used auto-facts                                #
# --------------------------------------------------------------------------- #
def _retire_stale(stale_days: int) -> tuple[int, list[int]]:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "UPDATE memory SET status = 'stale' "
                "WHERE status = 'active' AND cited_count = 0 AND auto = true "
                "  AND created_at < now() - make_interval(days => %s) RETURNING id",
                (stale_days,),
            ).fetchall()
            ids = [r["id"] for r in rows]
            return len(ids), ids
    except Exception as e:
        print(f"[dream] retire_stale skipped: {e!r}")
        return 0, []


# --------------------------------------------------------------------------- #
# step 4 — refresh + (optionally) resolve conflicts                           #
# --------------------------------------------------------------------------- #
def _resolve_conflicts(auto_resolve: bool) -> dict:
    detected = resolved = 0
    ids: list[int] = []
    try:
        from .kb_lint import run_lint
        detected = run_lint()
    except Exception as e:
        print(f"[dream] run_lint skipped: {e!r}")
    if not auto_resolve:
        return {"detected": detected, "resolved": 0, "ids": []}
    try:
        with get_conn() as conn:
            opens = conn.execute(
                "SELECT id, doc_a, doc_b, value_a, value_b FROM kb_conflict "
                "WHERE status = 'open' AND source = 'lookup' "
                "  AND doc_a IS NOT NULL AND doc_b IS NOT NULL"
            ).fetchall()
            for c in opens:
                winner = c["value_b"] if (c["doc_b"] or 0) > (c["doc_a"] or 0) else c["value_a"]
                conn.execute(
                    "UPDATE kb_conflict SET status = 'resolved', "
                    "detail = detail || ' — auto-resolved to newer doc value: ' || %s "
                    "WHERE id = %s",
                    (str(winner)[:200], c["id"]),
                )
                ids.append(c["id"]); resolved += 1
    except Exception as e:
        print(f"[dream] conflict resolve skipped: {e!r}")
    return {"detected": detected, "resolved": resolved, "ids": ids}


# --------------------------------------------------------------------------- #
# step 5 — recompute page salience from citation usage                        #
# --------------------------------------------------------------------------- #
def _score_salience() -> int:
    try:
        with get_conn() as conn:
            conn.execute("UPDATE pages SET salience = 0 WHERE salience <> 0")
            cur = conn.execute(
                """
                WITH cites AS (
                    SELECT DISTINCT (e->>'page_id')::bigint AS page_id,
                           m.id AS msg_id, m.created_at
                    FROM messages m
                    CROSS JOIN LATERAL jsonb_array_elements(m.pages) e
                    WHERE m.role = 'bot' AND jsonb_typeof(m.pages) = 'array'
                      AND (e->>'page_id') ~ '^[0-9]+$'
                ),
                agg AS (
                    SELECT page_id, count(DISTINCT msg_id) AS n, max(created_at) AS last_at
                    FROM cites GROUP BY page_id
                ),
                codeg AS (
                    SELECT c1.page_id, count(DISTINCT c2.page_id) AS deg
                    FROM cites c1 JOIN cites c2
                      ON c1.msg_id = c2.msg_id AND c2.page_id <> c1.page_id
                    GROUP BY c1.page_id
                )
                UPDATE pages p SET salience =
                    ln(1 + a.n)
                    + CASE WHEN a.last_at > now() - interval '30 days' THEN 0.5 ELSE 0 END
                    + 0.2 * COALESCE(cd.deg, 0)
                FROM agg a LEFT JOIN codeg cd ON cd.page_id = a.page_id
                WHERE p.id = a.page_id
                """
            )
            return cur.rowcount or 0
    except Exception as e:
        print(f"[dream] score_salience skipped: {e!r}")
        return 0


# --------------------------------------------------------------------------- #
# step 6 — zero-LLM auto-linking                                              #
# --------------------------------------------------------------------------- #
def _autolink(on: bool) -> dict:
    if not on:
        return {"docs": 0, "edges": 0, "doc_ids": []}
    try:
        from .autolink import link_all
        return link_all()
    except Exception as e:
        print(f"[dream] autolink skipped: {e!r}")
        return {"docs": 0, "edges": 0, "doc_ids": []}


# --------------------------------------------------------------------------- #
# step 7 — gap-driven Q&A fill (optional, costs LLM)                          #
# --------------------------------------------------------------------------- #
def _fill_gaps(on: bool) -> int:
    if not on:
        return 0
    try:
        from .auto_qa_gaps import run_gap_fill
        return run_gap_fill()
    except Exception as e:
        print(f"[dream] gap fill skipped: {e!r}")
        return 0


# --------------------------------------------------------------------------- #
# orchestrator                                                                 #
# --------------------------------------------------------------------------- #
def _last_run_at():
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT created_at FROM dream_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row["created_at"] if row else None
    except Exception:
        return None


def run_dream(force: bool = False) -> dict:
    """Run one full dream cycle. force=True ignores the interval gate. Persists a
    dream_runs row + emits a 'dream' digest. Returns a report dict."""
    cfg = _cfg()
    last = _last_run_at()
    if not force and last is not None:
        age = datetime.datetime.now(datetime.timezone.utc) - last
        if age < datetime.timedelta(hours=cfg["interval_h"]):
            return {"ran": False, "reason": "not due yet"}

    from .config import AUTO_APPROVE_MIN_CONF
    merged_n, merged_ids = _dedup_facts()
    promo_n, promo_ids = _promote_pending(cfg.get("promote_min_conf", AUTO_APPROVE_MIN_CONF), cfg["promote_age_h"])
    retire_n, retire_ids = _retire_stale(cfg["stale_days"])
    conflicts = _resolve_conflicts(cfg["auto_resolve"])
    scored = _score_salience()
    link = _autolink(cfg["autolink"])
    gap = _fill_gaps(cfg["gap_fill"])

    stats = {
        "merged_facts": merged_n,
        "promoted_facts": promo_n,
        "retired_facts": retire_n,
        "conflicts_detected": conflicts["detected"],
        "conflicts_resolved": conflicts["resolved"],
        "scored_pages": scored,
        "linked_edges": link["edges"],
        "gap_qa": gap,
    }
    touched = {
        "merged_facts": merged_ids,
        "promoted_facts": promo_ids,
        "retired_facts": retire_ids,
        "resolved_conflicts": conflicts["ids"],
        "linked_docs": link["doc_ids"],
    }

    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO dream_runs (stats, touched) VALUES (%s::jsonb, %s::jsonb)",
                (json.dumps(stats), json.dumps(touched)),
            )
    except Exception as e:
        print(f"[dream] run log insert skipped: {e!r}")

    body = (
        f"merged {merged_n} · promoted {promo_n} · retired {retire_n} · conflicts "
        f"{conflicts['detected']} (resolved {conflicts['resolved']}) · "
        f"scored {scored} pages · linked {link['edges']} edges · gap Q&A {gap}"
    )
    notify.emit("dream", "Dream cycle complete", body, "info")
    log_ingest(None, "dream", f"🌙 dream cycle · {body}")
    return {"ran": True, "stats": stats, "touched": touched, "body": body}


def recent_runs(limit: int = 10) -> list[dict]:
    """Recent dream-cycle runs, newest first, with stats + touched + timestamp."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, stats, touched, created_at FROM dream_runs "
                "ORDER BY created_at DESC LIMIT %s",
                (max(1, int(limit)),),
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "stats": r["stats"] or {},
                "touched": r["touched"] or {},
                "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            })
        return out
    except Exception as e:
        print(f"[dream] recent_runs skipped: {e!r}")
        return []


def latest() -> dict | None:
    """Most recent run (stats + touched + time) for the Brain-health UI."""
    runs = recent_runs(limit=1)
    return runs[0] if runs else None


def _loop():
    time.sleep(120)  # let boot settle
    while True:
        try:
            run_dream(force=False)
        except Exception as e:
            print(f"[dream] loop skipped: {e!r}")
        time.sleep(3600)  # check hourly; runs when the interval has elapsed


def start() -> None:
    """Launch the dream-cycle thread once (called from background.start_all)."""
    global _started
    if _started or not _cfg().get("enabled", True):
        return
    _started = True
    threading.Thread(target=_loop, name="dream-cycle", daemon=True).start()
    print(f"[dream] cycle on — every {_cfg().get('interval_h', 24)}h")
