import os
from pathlib import Path
from dotenv import load_dotenv

# project root = parent of app/
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
INGEST_MODEL = os.getenv("INGEST_MODEL", "openrouter/google/gemini-2.5-flash")
CHAT_MODEL = os.getenv("CHAT_MODEL", "google/gemini-2.5-flash")
DEEP_MODEL = os.getenv("DEEP_MODEL", CHAT_MODEL)  # deep mode (images+tools)

API_KEY = os.getenv("API_KEY", "")  # if set, required via X-API-Key header

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://docsensei:docsensei@localhost:5436/docsensei",
)

# ---- DB connection pool (psycopg_pool) ----
# Previously get_conn() opened a brand-new connection per call (no pool) -> PG
# max_connections exhausts under ~15 concurrent requests. Pool reuses conns.
# Size for: pool fits inside PG max_connections across all uvicorn workers
# (DB_POOL_MAX * WEB_CONCURRENCY < pg max_connections).
DB_POOL_MIN     = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX     = int(os.getenv("DB_POOL_MAX", "20"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30"))  # secs to wait for a free conn

DATA_DIR = ROOT / os.getenv("DATA_DIR", "./data").lstrip("./")
PAGES_DIR = ROOT / os.getenv("PAGES_DIR", "./data/pages").lstrip("./")
UPLOADS_DIR = ROOT / os.getenv("UPLOADS_DIR", "./data/uploads").lstrip("./")
INBOX_DIR     = ROOT / os.getenv("INBOX_DIR", "./data/inbox").lstrip("./")
PROCESSED_DIR = ROOT / os.getenv("PROCESSED_DIR", "./data/processed").lstrip("./")
FAILED_DIR    = ROOT / os.getenv("FAILED_DIR", "./data/failed").lstrip("./")
# server-side bulk-import folder: "Import from server" scans this for new docs to queue
IMPORT_DIR    = ROOT / os.getenv("IMPORT_DIR", "./documents").lstrip("./")
STORAGE       = os.getenv("STORAGE", "local")  # local | s3 (durable object-store backend)
# Local working cache for files pulled from S3 during processing (pymupdf needs a real path)
S3_CACHE_DIR  = ROOT / os.getenv("S3_CACHE_DIR", "./data/s3cache").lstrip("./")

# ---- S3 / MinIO object storage (durable backend + bulk import source) ----
S3_BUCKET            = os.getenv("S3_BUCKET", "")            # required when STORAGE=s3 or for import
S3_REGION            = os.getenv("S3_REGION", "us-east-1")
S3_ENDPOINT_URL      = os.getenv("S3_ENDPOINT_URL", "")      # set for MinIO / non-AWS (e.g. http://minio:9000)
S3_ACCESS_KEY_ID     = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")
S3_PREFIX            = os.getenv("S3_PREFIX", "docsensei/").strip()   # key namespace for OUR managed files
S3_FORCE_PATH_STYLE  = os.getenv("S3_FORCE_PATH_STYLE", "1") == "1"   # MinIO needs path-style
S3_PRESIGN           = os.getenv("S3_PRESIGN", "0") == "1"            # serve page images via presigned redirect (browser pulls direct from S3)
# bulk import: where source documents to ingest live in the bucket (can differ from S3_PREFIX)
S3_IMPORT_PREFIX     = os.getenv("S3_IMPORT_PREFIX", "inbox-drop/").strip()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ---- compiled "wiki" layer (Karpathy second-brain): vision-once -> markdown ----
# COMPILE_ON_INGEST: at upload, reformat each page's raw vision text into clean
#   markdown (tables/steps/codes preserved) -> doc_pages_md. One-time cost.
# CHAT_USE_WIKI: chat reads the compiled markdown instead of raw page text.
# COMPILE_MODEL: model for the one-time reformat pass (defaults to CHAT_MODEL).
COMPILE_ON_INGEST = os.getenv("COMPILE_ON_INGEST", "0") == "1"
CHAT_USE_WIKI = os.getenv("CHAT_USE_WIKI", "0") == "1"
COMPILE_MODEL = os.getenv("COMPILE_MODEL") or CHAT_MODEL
# AUTO_CATEGORIZE: at ingest, LLM-tag each doc with one topic category (docs.category).
AUTO_CATEGORIZE = os.getenv("AUTO_CATEGORIZE", "1") == "1"

# ---- cadence: weekly self-audit digest (proactive, no human prompt) ----
DIGEST_ENABLED = os.getenv("DIGEST_ENABLED", "1") not in ("0", "false", "False", "")
DIGEST_INTERVAL_DAYS = int(os.getenv("DIGEST_INTERVAL_DAYS", "7"))

# ---- auto-learning (Layer B): always-extract facts from chat (default OFF) ----
# Layer A (reactive, intent-gated) always runs. Layer B mines EVERY answer.
AUTO_LEARN_ENABLED = os.getenv("AUTO_LEARN_ENABLED", "1") == "1"   # ON by default
AUTO_LEARN_MODE = os.getenv("AUTO_LEARN_MODE", "per_answer")  # per_answer | nightly
AUTO_LEARN_MIN_CONF = float(os.getenv("AUTO_LEARN_MIN_CONF", "0.7"))   # propose ≥ this
AUTO_APPROVE_MIN_CONF = float(os.getenv("AUTO_APPROVE_MIN_CONF", "0.95"))  # auto-activate ≥ this (admin only)
AUTO_LEARN_MAX_PER_CONV = int(os.getenv("AUTO_LEARN_MAX_PER_CONV", "5"))   # flood cap per conversation

# ---- auto-facts from documents: extract durable facts at ingest (pending review) ----
AUTO_FACTS_ENABLED = os.getenv("AUTO_FACTS_ENABLED", "1") == "1"   # ON by default (1 LLM call/doc)
AUTO_FACTS_MAX = int(os.getenv("AUTO_FACTS_MAX", "6"))             # max facts proposed per doc

# ---- auto-Q&A from documents: extract grounded question/answer pairs at ingest (pending review) ----
AUTO_QA_ENABLED = os.getenv("AUTO_QA_ENABLED", "1") == "1"   # ON by default (1 LLM call/doc)
AUTO_QA_MAX = int(os.getenv("AUTO_QA_MAX", "8"))            # max Q&A pairs proposed per doc
# Phase 2: harvest Q&A FREE from real sourced+upvoted chat turns (zero LLM, reuses the answer)
AUTO_QA_HARVEST = os.getenv("AUTO_QA_HARVEST", "1") == "1"   # ON by default
# Phase 3: daemon that turns demand/coverage GAPS (keywords.py) into grounded Q&A, rate-capped.
# COSTS LLM per gap (synth question + answer pipeline) → default OFF; enable when you want it.
AUTO_QA_GAP_ENABLED = os.getenv("AUTO_QA_GAP_ENABLED", "0") == "1"     # default OFF (costs LLM)
AUTO_QA_GAP_PER_DAY = int(os.getenv("AUTO_QA_GAP_PER_DAY", "5"))       # cap new gap pairs/day
AUTO_QA_GAP_INTERVAL_H = int(os.getenv("AUTO_QA_GAP_INTERVAL_H", "24"))  # how often to run the fill
# Phase 4: SERVE a cached answer from the bank when an incoming question near-matches an
# APPROVED pair → instant, zero-agent, zero-cost. Conservative similarity floor.
AUTO_QA_SERVE_ENABLED = os.getenv("AUTO_QA_SERVE_ENABLED", "1") == "1"   # ON by default
AUTO_QA_SERVE_MIN_SIM = float(os.getenv("AUTO_QA_SERVE_MIN_SIM", "0.72"))  # trigram floor to serve

# ---- auto complexity routing (quick vs deep, picked per question) ----
AUTO_ROUTE_ENABLED = os.getenv("AUTO_ROUTE_ENABLED", "1") == "1"   # ON by default
AUTO_ROUTE_LLM = os.getenv("AUTO_ROUTE_LLM", "1") == "1"           # LLM tie-breaker when heuristic unsure

# ---- AI admin/ops agent (natural-language ops Q&A over live metrics) ----
OPS_AGENT_ENABLED = os.getenv("OPS_AGENT_ENABLED", "1") == "1"

# ---- shareable answer links ----
SHARE_ENABLED = os.getenv("SHARE_ENABLED", "1") == "1"
SHARE_PUBLIC_ENABLED = os.getenv("SHARE_PUBLIC_ENABLED", "0") == "1"  # 0 = members only, 1 = anyone

# ---- runtime env: "dev" | "production". production enables boot guards + HSTS ----
APP_ENV = os.getenv("APP_ENV", "dev").lower()

# ---- login brute-force lockout ----
LOGIN_MAX_FAIL = int(os.getenv("LOGIN_MAX_FAIL", "5"))     # fails allowed per window
LOGIN_LOCK_MIN = int(os.getenv("LOGIN_LOCK_MIN", "15"))    # window length (minutes)

# ---- auth ----
JWT_SECRET = os.getenv("JWT_SECRET", "dev-docsensei-change-me")
JWT_TTL_DAYS = int(os.getenv("JWT_TTL_DAYS", "7"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "")  # used to build the OIDC redirect URI

# CORS: cross-origin browser callers. Empty (default) = same-origin only — the
# embed widget runs in an iframe served from THIS origin so it never needs CORS.
# Only set this if a customer calls the API directly from their own page's JS.
CORS_ALLOW_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]

# ---- embeddable widget ----
EMBED_TOKEN_TTL_MIN = int(os.getenv("EMBED_TOKEN_TTL_MIN", "30"))  # widget JWT lifetime
# retention: prune idle anonymous visitors + trim append-only embed logs so the
# users table doesn't grow unbounded at widget scale. Daemon default OFF.
EMBED_PRUNE_ENABLED = os.getenv("EMBED_PRUNE_ENABLED", "0") == "1"
EMBED_VISITOR_TTL_DAYS = int(os.getenv("EMBED_VISITOR_TTL_DAYS", "90"))
EMBED_EVENT_TTL_DAYS = int(os.getenv("EMBED_EVENT_TTL_DAYS", "60"))
EMBED_PRUNE_INTERVAL_H = int(os.getenv("EMBED_PRUNE_INTERVAL_H", "24"))

_DEFAULT_SECRET = "dev-docsensei-change-me"


def check_prod_config() -> None:
    """Fail fast in production if security-critical secrets are left at defaults."""
    if APP_ENV != "production":
        if JWT_SECRET == _DEFAULT_SECRET:
            print("[config] WARNING: JWT_SECRET is the dev default — set a strong "
                  "JWT_SECRET before going to production.")
        return
    problems = []
    if JWT_SECRET in ("", _DEFAULT_SECRET) or len(JWT_SECRET) < 32:
        problems.append("JWT_SECRET must be set to a strong value (>=32 chars)")
    if not OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY is required")
    if problems:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


def ensure_dirs() -> None:
    for d in (DATA_DIR, PAGES_DIR, UPLOADS_DIR, INBOX_DIR, PROCESSED_DIR, FAILED_DIR, IMPORT_DIR, S3_CACHE_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)
