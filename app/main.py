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
    from . import admin_rbac as _arbac
    _arbac.ensure_default_groups()   # seed starter feature-based access groups (idempotent)
    _astore.backfill_auth_and_groups()   # seed auth_methods[] + add every member to 'Users' (idempotent)
    # Background work (ingest worker + daemons) runs in ONE process only (leader).
    # When INGEST_IN_API=0 a dedicated `worker` container runs them instead, so the
    # API never shares its GIL with heavy vision/PageIndex work.
    from .config import INGEST_IN_API
    if INGEST_IN_API:
        from . import background
        background.start_all()
    else:
        print("[api] INGEST_IN_API=0 — ingest handled by the dedicated worker container")
    yield
    from .db import close_pool
    close_pool()  # drain DB connection pool on shutdown


app = FastAPI(title="DocSensei", version="2.20.2", lifespan=lifespan)

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
