"""Background work startup — shared by the API lifespan and the dedicated worker.

All ingest + daemon work is leader-gated (PG advisory lock) so that no matter how
many API workers or worker containers exist, exactly ONE runs the daemons. Set
INGEST_IN_API=0 on the API and run `python -m app.worker_main` in a separate
container to keep heavy vision / PageIndex off the API's GIL.
"""


def start_all() -> bool:
    """Acquire leadership and start the ingest worker, inbox watcher and all
    background daemons. Returns True if this process became the leader (and thus
    started them), False otherwise. Safe to call from API or the worker process."""
    from . import leader
    if not leader.try_acquire():
        print("[bg] another process holds leadership — not starting daemons")
        return False
    print("[bg] leader acquired — starting ingest worker + daemons")
    from . import worker, watcher, digest, usage_rollup, verify
    worker.start()    # async ingest: requeue stuck docs + run worker thread(s)
    watcher.start()   # pick up files dropped straight into the storage inbox
    digest.start()    # weekly self-audit digest into the activity feed
    from . import embed as _embed
    _embed.start_prune_daemon()       # embed retention (flag EMBED_PRUNE_ENABLED)
    from . import maintenance as _maint
    _maint.start_daemon()             # DB vacuum/retention (flag DB_MAINTENANCE_ENABLED)
    usage_rollup.start_daemon()       # usage analytics rollup (flag USAGE_ROLLUP_ENABLED)
    verify.start_daemon()             # citation-accuracy verify pass (flag VERIFY_ENABLED)
    from . import learn as _learn
    _learn.start_auto_learn_daemon()  # nightly fact mining (AUTO_LEARN_ENABLED nightly)
    from . import auto_qa_gaps as _qagaps
    _qagaps.start()                   # gap-driven Q&A daemon (AUTO_QA_GAP_ENABLED)
    from . import dream as _dream
    _dream.start()                    # nightly self-improvement cycle (DREAM_ENABLED)
    from . import sharepoint as _sp
    _sp.start()                       # SharePoint auto-sync (SHAREPOINT_SYNC_ENABLED)
    try:
        from . import s3client
        if s3client.enabled():
            s3client.ensure_bucket()  # create bucket if missing (S3/MinIO)
    except Exception as e:
        print(f"[s3] init skipped: {e!r}")
    return True
