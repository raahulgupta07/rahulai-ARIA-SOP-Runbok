"""Storage abstraction for async document ingest.

A thin storage driver so the rest of the app never cares whether files live on
local disk or (Phase 8) S3. Every uploaded document moves through a 3-stage
lifecycle, addressed by an opaque ``storage_key``:

    INBOX      uploaded file lands here (``save_to_inbox``); the Phase-7 watcher
               polls ``list_inbox()`` and picks files up for ingestion.
        |
        v
    PROCESSED  ingest succeeded → ``move_out(key, "processed")``
        or
    FAILED     ingest raised → ``move_out(key, "failed")``

A ``storage_key`` is ``"<uuid8>__<safe_filename>"``. The uuid prefix guarantees
uniqueness (two uploads of the same name never collide); the original human
filename is recoverable via ``original_filename(key)``.

Backend selection is driven by ``config.STORAGE`` ("local" | "s3"). Only the
local backend is implemented; the s3 backend is a stub that raises
``NotImplementedError`` until Phase 8.

Public API (other phases depend on these exact signatures):
    save_to_inbox(fileobj, filename) -> str    # storage_key
    inbox_path(key) -> Path
    move_out(key, outcome) -> str              # new key
    list_inbox() -> list[str]
    original_filename(key) -> str
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from .config import (
    FAILED_DIR,
    INBOX_DIR,
    PROCESSED_DIR,
    S3_CACHE_DIR,
    STORAGE,
)

_KEY_SEP = "__"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")

# S3 object layout under our prefix: inbox/<key>, processed/<key>, failed/<key>
_S3_INBOX = "inbox/"
_S3_OUT = {"processed": "processed/", "failed": "failed/"}


def _is_s3() -> bool:
    # effective backend = DB (super-admin UI) over env; bucket must be set
    from . import s3client
    return s3client.backend() == "s3" and s3client.enabled()


def _s3_key(stage: str, key: str) -> str:
    """Bucket-relative object key for a storage_key at a lifecycle stage."""
    from . import s3client
    return s3client.prefixed(f"{stage}{key}")


# ---- s3 backend ------------------------------------------------------------


def _s3_save_to_inbox(fileobj, filename: str) -> str:
    from . import s3client
    key = f"{uuid.uuid4().hex[:8]}{_KEY_SEP}{_safe_filename(filename)}"
    s3client.put_fileobj(fileobj, _s3_key(_S3_INBOX, key))
    return key


def _s3_inbox_path(key: str) -> Path:
    """Download the inbox object to a local cache path (pymupdf needs a real file)."""
    from . import s3client
    Path(S3_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    dest = Path(S3_CACHE_DIR) / key
    if not dest.exists():
        s3client.download_to(_s3_key(_S3_INBOX, key), dest)
    return dest


def _s3_move_out(key: str, outcome: str) -> str:
    from . import s3client
    stage = _S3_OUT.get(outcome)
    if not stage:
        raise ValueError(f"outcome must be 'processed' or 'failed', got {outcome!r}")
    src = _s3_key(_S3_INBOX, key)
    if s3client.exists(src):
        s3client.copy(src, _s3_key(stage, key))
        s3client.delete(src)
    # drop the local processing cache copy
    try:
        (Path(S3_CACHE_DIR) / key).unlink(missing_ok=True)
    except Exception:
        pass
    return key


def _s3_list_inbox() -> list[str]:
    from . import s3client
    base = _s3_key(_S3_INBOX, "")
    keys = []
    for full in s3client.list_keys(base):
        name = full[len(base):]
        if not name or "/" in name or name.startswith("."):
            continue
        if name.endswith(".partial") or name.endswith(".tmp"):
            continue
        keys.append(name)
    return sorted(keys)


def _safe_filename(filename: str) -> str:
    """Keep alnum/dot/dash/underscore; replace everything else with '_'.

    Strips any directory components so a malicious name can't escape the dir.
    Preserves the extension (it's just more allowed chars). Falls back to
    'file' if the name reduces to nothing.
    """
    name = Path(filename or "").name  # drop path components
    safe = _SAFE_RE.sub("_", name).strip("._") or "file"
    return safe


def _outcome_dir(outcome: str) -> Path:
    if outcome == "processed":
        return PROCESSED_DIR
    if outcome == "failed":
        return FAILED_DIR
    raise ValueError(f"outcome must be 'processed' or 'failed', got {outcome!r}")


# ---- local backend ---------------------------------------------------------


def _local_save_to_inbox(fileobj, filename: str) -> str:
    key = f"{uuid.uuid4().hex[:8]}{_KEY_SEP}{_safe_filename(filename)}"
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    dest = INBOX_DIR / key
    with open(dest, "wb") as out:
        shutil.copyfileobj(fileobj, out)
    return key


def _local_inbox_path(key: str) -> Path:
    return INBOX_DIR / key


def _local_move_out(key: str, outcome: str) -> str:
    dest_dir = _outcome_dir(outcome)
    src = INBOX_DIR / key
    dest = dest_dir / key
    # No-op safe: if it's already moved/missing from inbox, just return the key.
    if not src.exists():
        return key
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return key


def _local_list_inbox() -> list[str]:
    if not INBOX_DIR.exists():
        return []
    keys = []
    for p in INBOX_DIR.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if name.startswith("."):  # dotfiles
            continue
        if name.endswith(".partial") or name.endswith(".tmp"):  # partials
            continue
        keys.append(name)
    return sorted(keys)


# ---- public API (backend dispatch) -----------------------------------------


def save_to_inbox(fileobj, filename: str) -> str:
    """Save an uploaded file-like object into the inbox. Returns an opaque storage_key.

    Local: key = "<uuid8>__<safe_filename>", file written to INBOX_DIR/key.
    """
    if _is_s3():
        return _s3_save_to_inbox(fileobj, filename)
    return _local_save_to_inbox(fileobj, filename)


def inbox_path(key: str) -> Path:
    """Absolute local path for a key currently in the inbox (INBOX_DIR/key)."""
    if _is_s3():
        return _s3_inbox_path(key)
    return _local_inbox_path(key)


def move_out(key: str, outcome: str) -> str:
    """Move file from inbox to processed (outcome="processed") or failed (outcome="failed").

    Returns the new key. No-op safe if file already moved/missing (return key).
    """
    if _is_s3():
        return _s3_move_out(key, outcome)
    return _local_move_out(key, outcome)


def list_inbox() -> list[str]:
    """List storage_keys of files currently sitting in the inbox (for the Phase-7 watcher).

    Skip dotfiles/partials.
    """
    if _is_s3():
        return _s3_list_inbox()
    return _local_list_inbox()


def original_filename(key: str) -> str:
    """Recover the human filename from a key ("<uuid>__name.pdf" -> "name.pdf")."""
    return key.split(_KEY_SEP, 1)[1] if _KEY_SEP in key else key
