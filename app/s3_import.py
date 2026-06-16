"""Bulk import: pull documents from an S3 (or MinIO) bucket prefix and queue them.

Mirrors the server-folder "Import from server" scan, but the source is an S3
prefix (S3_IMPORT_PREFIX). Each supported object is downloaded and handed to the
normal ingest pipeline via storage.save_to_inbox (so whatever the storage backend
is — local or S3 — it lands in the right inbox and the async worker ingests it).
Dedup is by filename vs docs.name, same as the folder scan.
"""
import io
import os

from .db import get_conn
from . import s3client, storage

_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}


def _candidates() -> list[tuple[str, str]]:
    """(object_key, filename) for supported files under the import prefix."""
    out = []
    for key in s3client.list_keys(s3client.import_prefix()):
        fn = key.rsplit("/", 1)[-1]
        if not fn or fn.startswith("."):
            continue
        if os.path.splitext(fn)[1].lower() not in _EXTS:
            continue
        out.append((key, fn))
    return out


def preview() -> dict:
    """What an import WOULD queue (no writes)."""
    if not s3client.enabled():
        return {"ok": False, "configured": False, "found": 0, "new": 0, "skipped": 0}
    cands = _candidates()
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    new = sum(1 for _, fn in cands if fn not in existing)
    return {"ok": True, "configured": True, "bucket": s3client.bucket(),
            "prefix": s3client.import_prefix(), "found": len(cands),
            "new": new, "skipped": len(cands) - new}


def import_all(uploaded_by: str | None = None) -> dict:
    """Download every new object under the import prefix and queue it for ingest."""
    if not s3client.enabled():
        return {"ok": False, "configured": False, "found": 0, "queued": 0, "skipped": 0}
    cands = _candidates()
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM docs").fetchall()}
    found = len(cands)
    queued = skipped = 0
    for key, fn in cands:
        if fn in existing:
            skipped += 1
            continue
        try:
            data = s3client.get_bytes(key)
            skey = storage.save_to_inbox(io.BytesIO(data), fn)
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO docs (name, status, progress, storage_key, uploaded_by, updated_at) "
                    "VALUES (%s, 'queued', 0, %s, %s, now())",
                    (fn, skey, uploaded_by),
                )
            existing.add(fn)
            queued += 1
        except Exception as e:
            print(f"[s3_import] {key} failed: {e!r}")
    return {"ok": True, "configured": True, "bucket": s3client.bucket(),
            "prefix": s3client.import_prefix(), "found": found, "queued": queued, "skipped": skipped}
