from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ensure_dirs, ROOT, CORS_ALLOW_ORIGINS, APP_ENV, check_prod_config
from .db import init_db
from .routes import router
from .auth.router import router as auth_router
from . import worker
from . import watcher
from . import digest
from . import usage_rollup
from . import verify


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_prod_config()   # fail fast on weak prod secrets
    ensure_dirs()
    init_db()
    from .auth import store as _astore
    _astore.ensure_superadmin()   # env-bootstrapped admin (signup disabled → only admin source)
    # Background work runs in ONE worker only (leader). With WEB_CONCURRENCY>1
    # every worker runs this lifespan; without the gate we'd get N ingest
    # workers + N watchers racing the same inbox + N copies of every daemon.
    from . import leader
    if leader.try_acquire():
        print("[leader] this worker is the background leader — starting daemons")
        worker.start()   # async ingest: requeue stuck docs + run worker thread(s)
        watcher.start()  # pick up files dropped straight into the storage inbox
        digest.start()   # cadence: weekly self-audit digest into the activity feed
        from . import embed as _embed
        _embed.start_prune_daemon()  # embed retention (flag EMBED_PRUNE_ENABLED)
        from . import maintenance as _maint
        _maint.start_daemon()        # DB vacuum/retention (flag DB_MAINTENANCE_ENABLED)
        usage_rollup.start_daemon()  # usage analytics rollup (flag USAGE_ROLLUP_ENABLED)
        verify.start_daemon()  # citation-accuracy verify pass (flag VERIFY_ENABLED)
        from . import learn as _learn
        _learn.start_auto_learn_daemon()  # nightly fact mining (AUTO_LEARN_ENABLED + MODE=nightly)
        from . import auto_qa_gaps as _qagaps
        _qagaps.start()  # gap-driven Q&A daemon (flag AUTO_QA_GAP_ENABLED, default OFF)
        from . import sharepoint as _sp
        _sp.start()  # SharePoint auto-sync (flag SHAREPOINT_SYNC_ENABLED, default OFF)
        try:
            from . import s3client
            if s3client.enabled():
                s3client.ensure_bucket()  # create bucket if missing (S3/MinIO)
        except Exception as e:
            print(f"[s3] init skipped: {e!r}")
    else:
        print("[leader] another worker holds leadership — serving HTTP only")
    yield
    from .db import close_pool
    close_pool()  # drain DB connection pool on shutdown


app = FastAPI(title="DocSensei", version="0.1.0", lifespan=lifespan)

# Same-origin by default (CORS_ALLOW_ORIGINS empty). The embed widget loads in an
# iframe served from THIS origin, so it talks same-origin and needs no CORS grant.
# allow_credentials stays False so a wildcard, if ever set, can't leak cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if APP_ENV == "production":
        # behind TLS termination — tell browsers to never downgrade to http
        resp.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "DocSensei"}


# all backend endpoints live under /api so the SPA can own every other path
app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api")


# ---- serve the built SvelteKit SPA (OpenWebUI-style single deployable) ----
FRONTEND = ROOT / "frontend" / "build"
if FRONTEND.exists():
    class _ImmutableStatic(StaticFiles):
        # /_app filenames are content-hashed → cache forever, safe across deploys
        def file_response(self, *a, **k):
            resp = super().file_response(*a, **k)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    app.mount("/_app", _ImmutableStatic(directory=FRONTEND / "_app"), name="spa-assets")

    # the SPA shell must NEVER be cached, else a browser keeps an old index.html
    # that references hashed chunks deleted by the next deploy → stale/blank UI.
    # Hashed /_app assets are content-addressed, so they're safe to cache forever.
    _NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # serve a real static file if it exists, else fall back to index.html
        candidate = FRONTEND / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers=_NOCACHE)
        return FileResponse(FRONTEND / "index.html", headers=_NOCACHE)
else:
    @app.get("/")
    def root():
        return {"service": "DocSensei", "note": "frontend not built; API under /api"}
