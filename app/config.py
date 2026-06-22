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

# INGEST_IN_API: run the ingest worker + background daemons inside the API process.
# Set to 0 when a dedicated `worker` container runs them (so heavy vision/PageIndex
# never share the API's GIL → uploads + HTTP stay snappy). Default 1 = single-container.
INGEST_IN_API = os.getenv("INGEST_IN_API", "1") == "1"

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
# White-label brand assets (uploaded logo + derived mark/favicon/icons)
BRAND_DIR     = ROOT / os.getenv("BRAND_DIR", "./data/brand").lstrip("./")

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
# COMPILE_PLAYBOOK: at ingest, reformat the compiled doc into a user-facing playbook
#   (goal/who/prerequisites/steps/verify/if-fails/escalate) -> doc_playbook. One-time.
# PLAYBOOK_MODEL: model for that pass (defaults to CHAT_MODEL).
COMPILE_PLAYBOOK = os.getenv("COMPILE_PLAYBOOK", "0") == "1"
PLAYBOOK_MODEL = os.getenv("PLAYBOOK_MODEL") or CHAT_MODEL
# USE_PLAYBOOK_IN_CHAT: inject the dominant doc's compiled playbook into the answer
#   context so procedural answers follow the pre-made step shape. Fail-soft.
USE_PLAYBOOK_IN_CHAT = os.getenv("USE_PLAYBOOK_IN_CHAT", "1") == "1"
# TYPE_AWARE_COMPILE: at ingest, classify doc_type (sop/runbook/policy/reference)
#   and route to the matching compiler (reference -> lookup table; others -> playbook
#   with a type-tuned prompt). Phase 2.
TYPE_AWARE_COMPILE = os.getenv("TYPE_AWARE_COMPILE", "1") == "1"
# ENTITY_INDEX: at ingest, extract recurring entities (codes/menus/systems/screens)
#   into the cross-doc entity index. Phase 3.
ENTITY_INDEX = os.getenv("ENTITY_INDEX", "1") == "1"
# ENTITY_RETRIEVE: expand retrieval across docs that share an entity named in the
#   question (cross-doc recall). ENTITY_FILL_MAX caps the extra pages pulled in.
ENTITY_RETRIEVE = os.getenv("ENTITY_RETRIEVE", "1") == "1"
ENTITY_FILL_MAX = int(os.getenv("ENTITY_FILL_MAX", "3"))
# GRAPHWALK: GraphRAG-lite — from the FTS seed docs, follow doc_dependency +
#   doc_links edges ONE hop and pull each connected doc's best page (prerequisites,
#   co-cited docs). Pure SQL on existing edge tables. GRAPHWALK_MAX caps added pages.
GRAPHWALK_ENABLED = os.getenv("GRAPHWALK_ENABLED", "1") == "1"
GRAPHWALK_MAX = int(os.getenv("GRAPHWALK_MAX", "3"))
# KB_LINT: detect same-term/divergent-value conflicts; DOC_STALE_DAYS = freshness floor.
KB_LINT = os.getenv("KB_LINT", "1") == "1"
DOC_STALE_DAYS = int(os.getenv("DOC_STALE_DAYS", "365"))
# PROC_CHAINING: detect cross-doc prerequisites ("do SOP 2 before SOP 5") and surface
#   them in the playbook + chat. Phase 5.
PROC_CHAINING = os.getenv("PROC_CHAINING", "1") == "1"
# ROLE_DEPTH: tune answer verbosity by user role — non-admins get a simplified
#   end-user answer, admins/IT get the full procedure. Phase 6.
ROLE_DEPTH = os.getenv("ROLE_DEPTH", "1") == "1"
# MULTIDOC_SYNTH: when a question spans several docs, merge their playbooks into one
#   procedure; MULTIDOC_MAX caps how many playbooks are injected. Phase 7.
MULTIDOC_SYNTH = os.getenv("MULTIDOC_SYNTH", "1") == "1"
MULTIDOC_MAX = int(os.getenv("MULTIDOC_MAX", "2"))
# TROUBLESHOOT_TREE: at ingest, compile a decision tree from docs with branching
#   ("if X do Y else Z") for an interactive troubleshooter. Phase 6.2.
TROUBLESHOOT_TREE = os.getenv("TROUBLESHOOT_TREE", "1") == "1"
# AUTO_CATEGORIZE: at ingest, LLM-tag each doc with one topic category (docs.category).
AUTO_CATEGORIZE = os.getenv("AUTO_CATEGORIZE", "1") == "1"

# ---- cadence: weekly self-audit digest (proactive, no human prompt) ----
DIGEST_ENABLED = os.getenv("DIGEST_ENABLED", "1") not in ("0", "false", "False", "")
DIGEST_INTERVAL_DAYS = int(os.getenv("DIGEST_INTERVAL_DAYS", "7"))

# ---- dream cycle: nightly self-improvement pass (gbrain-style) ----
# One leader-gated daemon that consolidates the brain while idle: dedup facts,
# promote/retire, resolve conflicts, score salience, auto-link entities (zero LLM)
# and fill coverage gaps. Each step is fail-soft; the whole pass emits a digest.
DREAM_ENABLED = os.getenv("DREAM_ENABLED", "1") == "1"
DREAM_INTERVAL_H = int(os.getenv("DREAM_INTERVAL_H", "24"))      # how often the cycle runs
DREAM_STALE_DAYS = int(os.getenv("DREAM_STALE_DAYS", "60"))      # active+uncited+older → retire
DREAM_PROMOTE_AGE_H = int(os.getenv("DREAM_PROMOTE_AGE_H", "24"))  # pending must be ≥ this old to auto-promote
DREAM_AUTO_RESOLVE = os.getenv("DREAM_AUTO_RESOLVE", "0") == "1"  # auto-pick conflict winner (else flag for review)
DREAM_GAP_FILL = os.getenv("DREAM_GAP_FILL", "0") == "1"         # run gap→Q&A fill in the cycle (costs LLM)
DREAM_AUTOLINK = os.getenv("DREAM_AUTOLINK", "1") == "1"          # zero-LLM entity auto-linking pass

# ---- salience: boost retrieval by how much a page actually gets cited ----
# Pure graph/usage signal (no embeddings). Computed nightly by the dream cycle
# into pages.salience; added as a small term to the search ORDER BY.
SALIENCE_BOOST = float(os.getenv("SALIENCE_BOOST", "0.3"))

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
# SharePoint auto-sync: periodically pull new files from the configured library.
# Default OFF. Reuses sharepoint.import_all (dedup by name → only new files ingest).
SHAREPOINT_SYNC_ENABLED = os.getenv("SHAREPOINT_SYNC_ENABLED", "0") == "1"
SHAREPOINT_SYNC_INTERVAL_H = int(os.getenv("SHAREPOINT_SYNC_INTERVAL_H", "6"))
# Phase 4: SERVE a cached answer from the bank when an incoming question near-matches an
# APPROVED pair → instant, zero-agent, zero-cost. Conservative similarity floor.
AUTO_QA_SERVE_ENABLED = os.getenv("AUTO_QA_SERVE_ENABLED", "1") == "1"   # ON by default
AUTO_QA_SERVE_MIN_SIM = float(os.getenv("AUTO_QA_SERVE_MIN_SIM", "0.72"))  # trigram floor to serve
# Quality floor: never serve a near-empty cached answer (chars). Low by design —
# a crisp factual answer ("IP is 10.x.x.x") is valuable; length is NOT the quality
# signal. The real culprit is vague-deflection stubs ("inform your superior, do not
# resolve alone") mined from DON'TS/ESCALATIONS sections — those are filtered
# separately in qa.serve_match and fall through to the live agent instead.
AUTO_QA_SERVE_MIN_LEN = int(os.getenv("AUTO_QA_SERVE_MIN_LEN", "24"))

# ---- LLM concurrency cap ----
# Global ceiling on simultaneous user-facing LLM calls (quick + deep). Under a
# burst (1000 users) this queues requests instead of firing 1000 concurrent
# OpenRouter calls -> avoids provider rate-limit storms. 0 = unlimited.
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "15"))

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
    for d in (DATA_DIR, PAGES_DIR, UPLOADS_DIR, INBOX_DIR, PROCESSED_DIR, FAILED_DIR, IMPORT_DIR, S3_CACHE_DIR, BRAND_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)
