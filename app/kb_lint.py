"""KB lint — deterministic (no LLM) knowledge-base health checks.

Two pure-data signals, in the spirit of audit.py (plain reads/writes, fail-soft,
return empty/0 on any error, never raise into the caller):

  1. conflicts  : the SAME term carrying DIFFERENT values across documents
                  (and, when the memory table has a clean key/value shape, the
                  same fact key with different active values). Recomputed into the
                  `kb_conflict` table by run_lint().
  2. freshness  : ready documents whose newest timestamp is older than a stale
                  floor (DOC_STALE_DAYS).

Normalization (consistent everywhere): casefold + strip + collapse internal
whitespace to a single space. Terms/keys are compared on this normalized form;
values are compared case-insensitively + trimmed. The ORIGINAL (un-normalized)
term/value text is what we store + display.
"""
import datetime
import re

from .db import get_conn

try:
    from .config import DOC_STALE_DAYS
except Exception:  # config import must never break linting
    DOC_STALE_DAYS = 365


_WS = re.compile(r"\s+")


def _norm(s) -> str:
    """Casefold + trim + collapse internal whitespace. '' for None/blank."""
    if not s:
        return ""
    return _WS.sub(" ", str(s).strip()).casefold()


def _norm_val(s) -> str:
    """Value comparison key: trimmed + casefolded (whitespace NOT collapsed —
    a value can legitimately contain meaningful spacing; trimming is enough)."""
    if s is None:
        return ""
    return str(s).strip().casefold()


# --------------------------------------------------------------------------- #
# 1. recompute conflicts                                                       #
# --------------------------------------------------------------------------- #

def _lookup_conflicts(conn) -> int:
    """Same normalized term, ≥2 different docs, ≥2 different values → one row per
    clashing term. Pulls rows and compares in Python (clearer than a self-join
    for picking two representative differing rows)."""
    try:
        rows = conn.execute(
            "SELECT term, value, doc_id FROM doc_lookup "
            "WHERE term IS NOT NULL AND btrim(term) <> '' "
            "  AND value IS NOT NULL AND btrim(value) <> ''"
        ).fetchall()
    except Exception as e:
        print(f"[kb_lint] lookup read skipped: {e!r}")
        return 0

    # normalized term -> list of (orig_term, orig_value, value_key, doc_id)
    by_term: dict[str, list] = {}
    for r in rows:
        nt = _norm(r["term"])
        if not nt:
            continue
        by_term.setdefault(nt, []).append(
            (r["term"], r["value"], _norm_val(r["value"]), r["doc_id"])
        )

    written = 0
    for entries in by_term.values():
        # need ≥2 distinct docs AND ≥2 distinct values to be a real conflict
        docs = {e[3] for e in entries}
        vals = {e[2] for e in entries}
        if len(docs) < 2 or len(vals) < 2:
            continue

        # pick two representative rows whose values differ AND that sit in
        # different docs where possible (a divergence across documents).
        a = entries[0]
        b = None
        for e in entries[1:]:
            if e[2] != a[2] and e[3] != a[3]:
                b = e
                break
        if b is None:  # fall back to any differing value (same doc edge case)
            for e in entries[1:]:
                if e[2] != a[2]:
                    b = e
                    break
        if b is None:
            continue

        term = a[0]
        va, doc_a = a[1], a[3]
        vb, doc_b = b[1], b[3]
        detail = (
            f"'{term}' defined as '{va}' in doc {doc_a} "
            f"but '{vb}' in doc {doc_b}"
        )
        try:
            conn.execute(
                "INSERT INTO kb_conflict "
                "(term, value_a, doc_a, value_b, doc_b, source, detail, status) "
                "VALUES (%s, %s, %s, %s, %s, 'lookup', %s, 'open')",
                (term, va, doc_a, vb, doc_b, detail),
            )
            written += 1
        except Exception as e:
            print(f"[kb_lint] lookup conflict insert skipped: {e!r}")
    return written


def _memory_has_keyvalue(conn) -> bool:
    """True only if the memory table genuinely exposes a key + value pair we can
    compare on. Guards the fact-conflict block so we never guess columns."""
    try:
        cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'memory'"
            ).fetchall()
        }
    except Exception:
        return False
    return "key" in cols and "value" in cols


def _fact_conflicts(conn) -> int:
    """Same normalized fact KEY with different ACTIVE values → source='fact'.
    Only runs when memory has a real key/value shape (checked by caller)."""
    try:
        has_status = any(
            r["column_name"] == "status"
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'memory' AND column_name = 'status'"
            ).fetchall()
        )
        where = "WHERE key IS NOT NULL AND btrim(key) <> ''"
        if has_status:
            where += " AND status = 'active'"
        rows = conn.execute(
            f"SELECT id, key, value FROM memory {where}"
        ).fetchall()
    except Exception as e:
        print(f"[kb_lint] fact read skipped: {e!r}")
        return 0

    by_key: dict[str, list] = {}
    for r in rows:
        nk = _norm(r["key"])
        if not nk:
            continue
        by_key.setdefault(nk, []).append(
            (r["key"], r["value"], _norm_val(r["value"]), r["id"])
        )

    written = 0
    for entries in by_key.values():
        if len({e[2] for e in entries}) < 2:
            continue
        a = entries[0]
        b = next((e for e in entries[1:] if e[2] != a[2]), None)
        if b is None:
            continue
        key = a[0]
        detail = (
            f"fact '{key}' = '{a[1]}' (memory {a[3]}) "
            f"conflicts with '{b[1]}' (memory {b[3]})"
        )
        try:
            conn.execute(
                "INSERT INTO kb_conflict "
                "(term, value_a, doc_a, value_b, doc_b, source, detail, status) "
                "VALUES (%s, %s, NULL, %s, NULL, 'fact', %s, 'open')",
                (key, a[1], b[1], detail),
            )
            written += 1
        except Exception as e:
            print(f"[kb_lint] fact conflict insert skipped: {e!r}")
    return written


def run_lint() -> int:
    """Recompute open conflicts. Drops existing OPEN rows (keeps dismissed ones),
    then re-detects lookup (and, where possible, fact) conflicts. Returns the
    number of conflict rows written. Fail-soft → 0 on any error."""
    try:
        with get_conn() as conn:
            try:
                conn.execute("DELETE FROM kb_conflict WHERE status = 'open'")
            except Exception as e:
                print(f"[kb_lint] clear open conflicts skipped: {e!r}")
                return 0

            n = _lookup_conflicts(conn)
            if _memory_has_keyvalue(conn):
                n += _fact_conflicts(conn)
            return n
    except Exception as e:
        print(f"[kb_lint] run_lint skipped: {e!r}")
        return 0


# --------------------------------------------------------------------------- #
# 2. list / dismiss conflicts                                                  #
# --------------------------------------------------------------------------- #

def list_conflicts(status: str = "open") -> list[dict]:
    """Conflict rows of a given status, newest first, with doc names joined where
    available (doc_a_name / doc_b_name; NULL for fact conflicts). Plain dicts."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.term, c.value_a, c.doc_a, c.value_b, c.doc_b,
                       c.source, c.detail, c.status, c.created_at,
                       da.name AS doc_a_name, db.name AS doc_b_name
                FROM kb_conflict c
                LEFT JOIN docs da ON da.id = c.doc_a
                LEFT JOIN docs db ON db.id = c.doc_b
                WHERE c.status = %s
                ORDER BY c.created_at DESC, c.id DESC
                """,
                (status,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[kb_lint] list_conflicts skipped: {e!r}")
        return []


def dismiss_conflict(conflict_id: int) -> bool:
    """Mark one conflict dismissed (kept out of the next run_lint recompute).
    Returns True if a row changed, else False."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE kb_conflict SET status = 'dismissed' WHERE id = %s",
                (conflict_id,),
            )
            return (cur.rowcount or 0) > 0
    except Exception as e:
        print(f"[kb_lint] dismiss_conflict skipped: {e!r}")
        return False


# --------------------------------------------------------------------------- #
# 3. stale documents                                                           #
# --------------------------------------------------------------------------- #

def stale_docs(days: int | None = None) -> list[dict]:
    """Ready docs whose freshest timestamp (newer of updated_at / created_at) is
    older than now() - `days` (default DOC_STALE_DAYS). Oldest first.
    Each dict: {id, name, created_at, age_days}. Fail-soft → []."""
    try:
        d = int(days) if days is not None else int(DOC_STALE_DAYS)
    except Exception:
        d = int(DOC_STALE_DAYS)
    if d < 0:
        d = 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, name,
                       GREATEST(coalesce(updated_at, created_at), created_at) AS ts
                FROM docs
                WHERE status = 'ready'
                  AND GREATEST(coalesce(updated_at, created_at), created_at)
                      < now() - make_interval(days => %s)
                ORDER BY ts ASC
                """,
                (d,),
            ).fetchall()
    except Exception as e:
        print(f"[kb_lint] stale_docs skipped: {e!r}")
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    out: list[dict] = []
    for r in rows:
        ts = r["ts"]
        age = None
        try:
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
                age = (now - ts).days
        except Exception:
            age = None
        out.append(
            {"id": r["id"], "name": r["name"], "created_at": ts, "age_days": age}
        )
    return out
