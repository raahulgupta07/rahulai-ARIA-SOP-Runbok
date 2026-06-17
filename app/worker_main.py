"""Dedicated ingest worker — run in its own container, no HTTP server.

    python -m app.worker_main

Runs the async ingest worker, inbox watcher and all background daemons (leader-
gated like the API). Keeping this in a SEPARATE process means heavy per-page
vision + PageIndex work never competes with the API's request loop under the GIL,
so file upload and chat stay responsive while ingest churns. Set INGEST_IN_API=0
on the API container so only this process runs the daemons.
"""
import time

from .config import ensure_dirs
from .db import init_db, close_pool


def main() -> None:
    ensure_dirs()
    init_db()                 # idempotent; DDL is advisory-locked against races
    from . import background
    became_leader = background.start_all()
    if not became_leader:
        # another worker already leads — idle but stay alive so the container
        # doesn't crash-loop; it'll take over if the current leader dies.
        print("[worker] standing by (not leader)")
    print("[worker] ingest worker running — vision/PageIndex isolated from the API")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        close_pool()


if __name__ == "__main__":
    main()
