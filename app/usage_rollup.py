"""Usage rollup — aggregate messages + feedback into usage_daily (one row per
user per day). Dashboards read the rollup instead of scanning raw messages, so
analytics stay fast at 10k+ users / millions of messages.

Counts only — no chat text touched. rebuild_window() is idempotent (upsert), so
it's safe to re-run; today/yesterday are refreshed on every ensure() call while
older days are immutable and only backfilled once. A daemon (flag-gated) keeps
recent days warm.
"""
import os
import threading
import time

from .db import get_conn

_REBUILD_SQL = """
WITH base AS (
  SELECT c.user_id, m.created_at::date AS day,
         count(*) FILTER (WHERE m.role='user') AS questions,
         count(DISTINCT m.conversation_id) FILTER (WHERE m.role='user') AS convos
  FROM messages m JOIN conversations c ON c.id = m.conversation_id
  WHERE m.created_at >= %(cut)s AND c.user_id IS NOT NULL
  GROUP BY c.user_id, m.created_at::date
),
bot AS (
  SELECT c.user_id, m.created_at::date AS day,
         count(*) FILTER (WHERE m.pages IS NOT NULL AND m.pages::text <> '[]') AS sourced,
         count(*) FILTER (WHERE m.pages IS NULL OR m.pages::text = '[]') AS blind
  FROM messages m JOIN conversations c ON c.id = m.conversation_id
  WHERE m.role='bot' AND m.created_at >= %(cut)s AND c.user_id IS NOT NULL
  GROUP BY c.user_id, m.created_at::date
),
fb AS (
  SELECT user_id, created_at::date AS day,
         count(*) FILTER (WHERE vote='up') AS up,
         count(*) FILTER (WHERE vote='down') AS down
  FROM message_feedback
  WHERE user_id IS NOT NULL AND created_at >= %(cut)s
  GROUP BY user_id, created_at::date
),
keys AS (
  SELECT user_id, day FROM base
  UNION SELECT user_id, day FROM bot
  UNION SELECT user_id, day FROM fb
)
INSERT INTO usage_daily (user_id, day, questions, convos, sourced, blind, up, down)
SELECT k.user_id, k.day,
       COALESCE(b.questions,0), COALESCE(b.convos,0),
       COALESCE(bo.sourced,0),  COALESCE(bo.blind,0),
       COALESCE(f.up,0),        COALESCE(f.down,0)
FROM keys k
LEFT JOIN base b  ON b.user_id=k.user_id  AND b.day=k.day
LEFT JOIN bot  bo ON bo.user_id=k.user_id AND bo.day=k.day
LEFT JOIN fb   f  ON f.user_id=k.user_id  AND f.day=k.day
ON CONFLICT (user_id, day) DO UPDATE SET
  questions=EXCLUDED.questions, convos=EXCLUDED.convos,
  sourced=EXCLUDED.sourced, blind=EXCLUDED.blind,
  up=EXCLUDED.up, down=EXCLUDED.down
"""


def rebuild_window(days: int) -> int:
    """Recompute usage_daily for the last `days` days (today inclusive). Idempotent."""
    days = max(1, min(int(days), 400))
    with get_conn() as conn:
        conn.execute(_REBUILD_SQL, {"cut": _cutoff_expr(days)})
        n = conn.execute("SELECT count(*) AS n FROM usage_daily").fetchone()
    return n["n"]


def _cutoff_expr(days: int):
    # compute the date cutoff in python-free way via SQL would need a literal;
    # we pass an actual date string so the planner can use the day index.
    with get_conn() as conn:
        row = conn.execute(
            "SELECT (now()::date - make_interval(days => %s))::date AS d", (days - 1,)
        ).fetchone()
    return row["d"]


def ensure(days: int = 30) -> None:
    """Make the rollup correct for a `days` window before a dashboard reads it.
    Always refreshes today+yesterday (cheap); backfills the full window once."""
    try:
        rebuild_window(2)  # today + yesterday grow, keep fresh
        with get_conn() as conn:
            row = conn.execute("SELECT min(day) AS mn FROM usage_daily").fetchone()
            need = conn.execute(
                "SELECT (now()::date - make_interval(days => %s))::date AS d", (days - 1,)
            ).fetchone()["d"]
        mn = row["mn"]
        if mn is None or mn > need:
            rebuild_window(days)
    except Exception as e:
        print(f"[usage_rollup] ensure failed: {e!r}")


# ---- background daemon (flag-gated) -------------------------------------------
_started = False


def start_daemon() -> None:
    global _started
    if _started or os.getenv("USAGE_ROLLUP_ENABLED", "0") != "1":
        return
    _started = True

    def _loop():
        # initial backfill of the last 90 days, then keep recent days warm hourly
        try:
            rebuild_window(90)
        except Exception as e:
            print(f"[usage_rollup] backfill failed: {e!r}")
        while True:
            time.sleep(3600)
            try:
                rebuild_window(2)
            except Exception as e:
                print(f"[usage_rollup] hourly refresh failed: {e!r}")

    threading.Thread(target=_loop, daemon=True, name="usage-rollup").start()
    print("[usage_rollup] daemon started")
