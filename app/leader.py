"""Single-leader election across uvicorn workers (PG session advisory lock).

When the app runs with multiple uvicorn workers (WEB_CONCURRENCY > 1), every
worker is a separate process that runs the lifespan startup. Background work —
the ingest worker, the inbox watcher, and all the daemons — must run in EXACTLY
ONE process, otherwise N workers = N watchers racing on the same inbox files and
N copies of every daemon.

Election uses a PG SESSION-level advisory lock held on a dedicated long-lived
connection (NOT a pooled conn — a pooled conn gets recycled and would drop the
lock). The first worker to grab it is the leader for its process lifetime; the
lock auto-releases if that process dies, so a surviving worker can take over on
the next election attempt. Fail-open: on any DB error we assume leadership, so a
single-worker / dev setup always runs its daemons.
"""
import psycopg

from .config import DATABASE_URL

_LEADER_KEY = 0x0D0C5E01  # arbitrary, app-specific advisory-lock key
_lock_conn = None
_is_leader = False


def try_acquire() -> bool:
    """Attempt to become the background leader. Idempotent. Returns leadership."""
    global _lock_conn, _is_leader
    if _is_leader:
        return True
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        got = conn.execute("SELECT pg_try_advisory_lock(%s)", (_LEADER_KEY,)).fetchone()[0]
        if got:
            _lock_conn = conn  # keep open for process life -> hold the lock
            _is_leader = True
            return True
        conn.close()
        return False
    except Exception as e:
        # DB hiccup during election: fail open so daemons still run (single worker).
        print(f"[leader] election error, assuming leader: {e!r}")
        _is_leader = True
        return True


def is_leader() -> bool:
    return _is_leader
