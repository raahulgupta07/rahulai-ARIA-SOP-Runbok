"""App API keys for the SSO pass-through ("Access from other apps").

A key identifies the CALLING APP (e.g. one CityGPT server) instead of
allow-listing its Keycloak client id. The person is still identified by their
own SSO access token, verified by oidc.verify_bearer — a key alone never
grants access. Keys are `ak_live_…`, shown once; only a SHA-256 is stored
(high-entropy random secret, so a fast hash is fine and lookups are indexed).
"""
import hashlib
import secrets

from ..db import get_conn

PREFIX = "ak_live_"
HEADER = "X-Aria-App-Key"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create(name: str, created_by: str | None = None) -> dict:
    """Mint a key. Returns the row PLUS the plaintext `key` (the only time it exists)."""
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("name required")
    key = PREFIX + secrets.token_urlsafe(32)
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO app_keys (name, key_prefix, key_hash, created_by) VALUES (%s,%s,%s,%s) "
            "RETURNING id, name, key_prefix, active, created_by, created_at, last_used_at, uses",
            (name, key[: len(PREFIX) + 6], _hash(key), created_by),
        ).fetchone()
    return {**row, "key": key}


def list_keys() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, key_prefix, active, created_by, created_at, last_used_at, uses "
            "FROM app_keys ORDER BY id DESC"
        ).fetchall()


def set_active(key_id: int, active: bool) -> bool:
    with get_conn() as conn:
        r = conn.execute("UPDATE app_keys SET active=%s WHERE id=%s RETURNING id", (active, key_id)).fetchone()
    return bool(r)


def delete(key_id: int) -> bool:
    with get_conn() as conn:
        r = conn.execute("DELETE FROM app_keys WHERE id=%s RETURNING id", (key_id,)).fetchone()
    return bool(r)


def verify(key: str | None) -> dict | None:
    """Active key row for this plaintext key, else None. Never raises."""
    if not key or not key.startswith(PREFIX):
        return None
    try:
        with get_conn() as conn:
            return conn.execute(
                "SELECT id, name FROM app_keys WHERE key_hash=%s AND active", (_hash(key.strip()),)
            ).fetchone()
    except Exception as e:
        print(f"[app-keys] verify skipped: {e!r}", flush=True)
        return None


def touch(key_id: int) -> None:
    """Count one authenticated call. Fail-soft."""
    try:
        with get_conn() as conn:
            conn.execute("UPDATE app_keys SET uses = uses + 1, last_used_at = now() WHERE id=%s", (key_id,))
    except Exception as e:
        print(f"[app-keys] touch skipped: {e!r}", flush=True)
