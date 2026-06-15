"""Master kill switch — pause ingest, cancel the in-flight doc, abort LLM/chat.

Cooperative: nothing hard-kills an HTTP call mid-flight. Instead:
  - `paused` → the worker stops claiming new docs (instant).
  - `cancel_gen` bump + per-doc set → `process_doc` checks between pages/steps and
    aborts cleanly (the running LLM/vision call finishes or times out, seconds).
  - `chat_stop` → active /ask/stream loops break; new chat calls fail-fast.

`paused` persists in app_config (survives restart); cancel_gen / chat_stop / the
per-doc cancel set are ephemeral (only meaningful for live work).
"""
import threading

_lock = threading.Lock()
_state = {"paused": False, "cancel_gen": 0, "chat_stop": False}
_cancel_ids: set[int] = set()


class Cancelled(Exception):
    """Raised inside process_doc when a stop/cancel was requested."""


def _load() -> None:
    try:
        from .appcfg import _raw
        _state["paused"] = bool((_raw().get("ingest") or {}).get("paused", False))
    except Exception:
        pass


def _persist() -> None:
    try:
        from .appcfg import _raw, _write
        d = _raw()
        d["ingest"] = {**(d.get("ingest") or {}), "paused": _state["paused"]}
        _write(d)
    except Exception:
        pass


# ---- reads (hot path — no DB) ----
def is_paused() -> bool: return _state["paused"]
def cancel_gen() -> int: return _state["cancel_gen"]
def chat_stopped() -> bool: return _state["chat_stop"]


def should_cancel_doc(doc_id: int, gen0: int) -> bool:
    """True if this doc must abort: a stop_all happened after it started, or it was
    cancelled individually."""
    return _state["cancel_gen"] != gen0 or doc_id in _cancel_ids


def clear_doc(doc_id: int) -> None:
    _cancel_ids.discard(doc_id)


# ---- writes (admin actions) ----
def stop_all() -> dict:
    with _lock:
        _state["paused"] = True
        _state["cancel_gen"] += 1
        _state["chat_stop"] = True
    _persist()
    return get_state()


def resume() -> dict:
    with _lock:
        _state["paused"] = False
        _state["chat_stop"] = False
        _cancel_ids.clear()
    _persist()
    return get_state()


def cancel_doc(doc_id: int) -> None:
    with _lock:
        _cancel_ids.add(doc_id)


def get_state() -> dict:
    return {"paused": _state["paused"], "chat_stop": _state["chat_stop"], "cancel_gen": _state["cancel_gen"]}


_load()
