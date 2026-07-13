import threading

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL, DB_POOL_MIN, DB_POOL_MAX, DB_POOL_TIMEOUT

# ---- shared connection pool ----
# One pool per process, lazily built on first use (thread-safe). Replaces the
# old "new psycopg.connect() per call" which exhausted PG max_connections at
# ~15 concurrent requests. Pooled conns are autocommit + dict_row, same as before.
# Callers keep using `with get_conn() as conn:` unchanged — pool.connection() is a
# context manager that RETURNS the conn to the pool on exit (does not close it).
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    DATABASE_URL,
                    min_size=DB_POOL_MIN,
                    max_size=DB_POOL_MAX,
                    timeout=DB_POOL_TIMEOUT,
                    kwargs={"row_factory": dict_row, "autocommit": True},
                    open=True,
                )
    return _pool


def get_conn():
    """Borrow an autocommit dict-row connection from the shared pool.

    Returns a context manager — use `with get_conn() as conn:`. On exit the
    connection is returned to the pool (not closed). Drop-in for the old API.
    """
    return _get_pool().connection()


def close_pool() -> None:
    """Close the pool on shutdown (lifespan). Safe if never opened."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS docs (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    lang        TEXT,
    tree_json   JSONB,
    page_count  INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE docs ADD COLUMN IF NOT EXISTS status      TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE docs ADD COLUMN IF NOT EXISTS progress    INT  NOT NULL DEFAULT 0;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS pages_done  INT  NOT NULL DEFAULT 0;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS error       TEXT;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS ready_at    TIMESTAMPTZ;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS category    TEXT;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS uploaded_by TEXT;
ALTER TABLE docs ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ DEFAULT now();
ALTER TABLE docs ADD COLUMN IF NOT EXISTS doc_type    TEXT;  -- sop|runbook|policy|reference|other
CREATE INDEX IF NOT EXISTS idx_docs_status ON docs(status);
-- one-time safety backfill (idempotent no-op once columns exist): keep legacy/pre-existing rows visible
UPDATE docs SET status = 'ready' WHERE status IS NULL;

CREATE TABLE IF NOT EXISTS pages (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    image_path  TEXT NOT NULL,
    text        TEXT,
    text_layer  TEXT,
    vision_text TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE pages ADD COLUMN IF NOT EXISTS text_layer  TEXT;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS vision_text TEXT;
-- salience: usage-derived importance score (citation count + co-citation degree
-- + recency), recomputed nightly by the dream cycle. Pure graph signal, no ML.
-- Added as a small boost term in retrieve.search_pages ORDER BY.
ALTER TABLE pages ADD COLUMN IF NOT EXISTS salience REAL NOT NULL DEFAULT 0;
-- Contextual Retrieval (Anthropic-style, vectorless): a 1-2 sentence blurb
-- generated at ingest situating this page within its document. Folded into the
-- FTS source (tsv below) so isolated pages are not ambiguous.
ALTER TABLE pages ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT '';
-- tsv is a GENERATED column. Postgres can't ALTER a generation expression in
-- place, so to make `context` searchable we drop+recreate the column. This runs
-- idempotently every boot; the DROP only matters once (the first deploy that
-- introduced `context`) — after that the column already covers text+context and
-- recreating it is harmless (same definition). Keep the 'simple' config.
DO $context_tsv$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'pages'::regclass
          AND attname = 'tsv'
          AND pg_get_expr(
                (SELECT adbin FROM pg_attrdef
                 WHERE adrelid = 'pages'::regclass
                   AND adnum = pg_attribute.attnum),
                'pages'::regclass
              ) ILIKE '%context%'
    ) THEN
        ALTER TABLE pages DROP COLUMN IF EXISTS tsv;
        ALTER TABLE pages ADD COLUMN tsv tsvector
            GENERATED ALWAYS AS (
                to_tsvector(
                    'simple',
                    coalesce(text, '') || ' ' || coalesce(context, '')
                )
            ) STORED;
    END IF;
END
$context_tsv$;
CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(doc_id);
CREATE INDEX IF NOT EXISTS idx_pages_tsv ON pages USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_pages_trgm ON pages USING GIN (text gin_trgm_ops);

-- flattened PageIndex tree nodes (precomputed at ingest, no per-query parse)
CREATE TABLE IF NOT EXISTS nodes (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_no     INT,
    title       TEXT,
    summary     TEXT,
    tsv tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(summary, ''))) STORED
);
CREATE INDEX IF NOT EXISTS idx_nodes_doc ON nodes(doc_id);
CREATE INDEX IF NOT EXISTS idx_nodes_tsv ON nodes USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_nodes_trgm ON nodes
    USING GIN ((coalesce(title,'') || ' ' || coalesce(summary,'')) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS memory (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT,
    value       TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
-- self-learning: where a fact came from (human-taught vs agent learned in chat)
ALTER TABLE memory ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'human';
ALTER TABLE memory ADD COLUMN IF NOT EXISTS created_by TEXT;
-- review gate (Intern Rule): chat-learned facts land 'pending' and don't
-- influence answers until approved; hand-taught facts are 'active' immediately.
ALTER TABLE memory ADD COLUMN IF NOT EXISTS status     TEXT NOT NULL DEFAULT 'active';
-- freshness: how often / when a fact actually shaped an answer (staleness signal)
ALTER TABLE memory ADD COLUMN IF NOT EXISTS cited_count      INT NOT NULL DEFAULT 0;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS last_cited_at    TIMESTAMPTZ;
-- provenance: the chat/feedback message that originally produced this fact
ALTER TABLE memory ADD COLUMN IF NOT EXISTS origin_question  TEXT;
-- auto-learning (Layer B): confidence of the extraction + whether it was auto-mined
ALTER TABLE memory ADD COLUMN IF NOT EXISTS confidence  REAL;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS auto        BOOLEAN NOT NULL DEFAULT false;
-- normalized dedupe key (lowercased, punctuation/stopwords stripped) for near-dup detection
ALTER TABLE memory ADD COLUMN IF NOT EXISTS dedupe_key  TEXT;
CREATE INDEX IF NOT EXISTS idx_memory_status ON memory(status);
CREATE INDEX IF NOT EXISTS idx_memory_dedupe ON memory(dedupe_key);

-- per-citation log: one row each time a fact's wording appears in an answer
CREATE TABLE IF NOT EXISTS fact_citation (
    id              BIGSERIAL PRIMARY KEY,
    fact_id         INT NOT NULL,
    conversation_id TEXT,
    question        TEXT,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_citation_fact ON fact_citation(fact_id, at DESC);

-- auto-Q&A bank: grounded question/answer pairs mined from docs (Phase 1) and,
-- later, harvested from real sourced+upvoted chat turns. Same review gate as
-- facts: source='qa' lands 'active' if confidence high enough else 'pending'.
-- page_ids = the doc pages the answer is grounded in (citation, best-effort).
CREATE TABLE IF NOT EXISTS qa_pairs (
    id              BIGSERIAL PRIMARY KEY,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'qa',     -- 'qa' (doc-mined) | 'chat' (harvested)
    status          TEXT NOT NULL DEFAULT 'pending',-- 'active' | 'pending' | 'rejected'
    confidence      REAL,
    doc_id          INT,                            -- origin document (NULL for chat-harvested)
    page_ids        JSONB NOT NULL DEFAULT '[]',    -- grounding pages [page_id,...]
    created_by      TEXT,                           -- "ingest:{doc_id}" or user email
    origin          TEXT,                           -- human-readable provenance
    cited_count     INT NOT NULL DEFAULT 0,         -- times this pair served/shaped an answer
    last_cited_at   TIMESTAMPTZ,
    dedupe_key      TEXT,                           -- normalized question for near-dup detection
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_qa_status ON qa_pairs(status);
CREATE INDEX IF NOT EXISTS idx_qa_dedupe ON qa_pairs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_qa_doc ON qa_pairs(doc_id);
CREATE INDEX IF NOT EXISTS idx_qa_q_trgm ON qa_pairs USING GIN (question gin_trgm_ops);

-- LLM-curated bilingual home starter chips (rebuilt by a refresh job, served zero-LLM).
-- One row per chip; rank orders them. q_en always set; q_my = Burmese translation.
CREATE TABLE IF NOT EXISTS starter_chips (
    id          BIGSERIAL PRIMARY KEY,
    rank        INT NOT NULL DEFAULT 0,         -- display order (0 = first)
    q_en        TEXT NOT NULL,                  -- English question
    q_my        TEXT,                           -- Burmese translation (nullable → fall back to EN)
    cat         TEXT,                           -- category label (eyebrow)
    doc_id      INT,                            -- source document (for the "from <doc>" subtitle)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_starter_rank ON starter_chips(rank);

-- behavioral signal for the chip bandit: one row per impression / click.
CREATE TABLE IF NOT EXISTS chip_events (
    id          BIGSERIAL PRIMARY KEY,
    chip_id     BIGINT NOT NULL,                -- references starter_chips.id (soft)
    event       TEXT NOT NULL,                  -- 'shown' | 'clicked'
    lang        TEXT,                           -- 'en' | 'my'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chipev_chip ON chip_events(chip_id);
CREATE INDEX IF NOT EXISTS idx_chipev_time ON chip_events(created_at);

-- generic non-secret config for integrations (SharePoint, etc.). Secrets stay in env.
CREATE TABLE IF NOT EXISTS integration_config (
    key         TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_log (
    id          BIGSERIAL PRIMARY KEY,
    q           TEXT,
    answer      TEXT,
    doc_refs    JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ---- auth / user management (local + ldap + sso, merged by email) ----
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT,
    role          TEXT NOT NULL DEFAULT 'user',     -- admin | user | pending
    password_hash TEXT,                              -- null for ldap/oidc-only
    auth_source   TEXT NOT NULL DEFAULT 'local',     -- local | ldap | oidc
    oauth_sub     TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    last_login    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));

-- ---- sector-based row-level access (multi-tenant, flag RBAC_ENABLED) ----
-- a sector = one company/domain (e.g. cityholdings.com). Only super-admin creates.
CREATE TABLE IF NOT EXISTS sectors (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,            -- email domain key, e.g. cityholdings.com
    label       TEXT,                            -- display name
    created_at  TIMESTAMPTZ DEFAULT now()
);
-- folders live inside a sector; docs live inside a folder.
CREATE TABLE IF NOT EXISTS folders (
    id          BIGSERIAL PRIMARY KEY,
    sector_id   BIGINT REFERENCES sectors(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    access_mode TEXT NOT NULL DEFAULT 'sector',  -- sector | specific | org
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_folders_sector ON folders(sector_id);
ALTER TABLE folders ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'sector';
-- nesting: a folder can live inside another (self-reference). parent_id NULL = top
-- level. ON DELETE SET NULL is a safety net — the delete route reparents children to
-- the grandparent explicitly; this just prevents orphaned rows if a folder is ever
-- removed directly. is_expanded persists the tree open/closed state per folder.
ALTER TABLE folders ADD COLUMN IF NOT EXISTS parent_id BIGINT REFERENCES folders(id) ON DELETE SET NULL;
ALTER TABLE folders ADD COLUMN IF NOT EXISTS is_expanded BOOLEAN NOT NULL DEFAULT false;
-- optional per-folder presentation: color = hex like '#c2683f', icon = short emoji like '📊'
ALTER TABLE folders ADD COLUMN IF NOT EXISTS color TEXT;
ALTER TABLE folders ADD COLUMN IF NOT EXISTS icon TEXT;
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
-- for access_mode='specific': who (a user or a group) may access this folder.
CREATE TABLE IF NOT EXISTS folder_access (
    folder_id      BIGINT REFERENCES folders(id) ON DELETE CASCADE,
    principal_type TEXT NOT NULL,                -- 'user' | 'group'
    principal_id   BIGINT NOT NULL,
    PRIMARY KEY (folder_id, principal_type, principal_id)
);
-- the All-Access group: members read every sector (read-only).
CREATE TABLE IF NOT EXISTS groups (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    all_sectors BOOLEAN NOT NULL DEFAULT FALSE,  -- true = read every sector
    created_at  TIMESTAMPTZ DEFAULT now()
);
-- per-group feature access: which app features (tabs) members may use.
-- NULL = no group-level feature restriction. Keys: chat|sources|workspace|eval|wiki.
ALTER TABLE groups ADD COLUMN IF NOT EXISTS features TEXT[];
-- group-granted content permissions: a plain 'user' in an empowered group may
-- manage content (upload/folders/move/retag) and/or teach knowledge (facts/Q&A),
-- while Settings stays super-admin-only. Both default false (secure).
ALTER TABLE groups ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS manage_content BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS teach_knowledge BOOLEAN NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS user_groups (
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    group_id    BIGINT REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);
-- every sign-in method this email has authenticated with (local|ldap|oidc).
-- one users row per email (merge-by-email); this array shows ALL its logins.
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_methods TEXT[];
-- a user's home sector (set from email domain at signup); role gains 'sector_admin'.
ALTER TABLE users ADD COLUMN IF NOT EXISTS sector_id BIGINT REFERENCES sectors(id);
-- every doc is tagged with the sector (+ folder) it belongs to.
ALTER TABLE docs  ADD COLUMN IF NOT EXISTS sector_id BIGINT REFERENCES sectors(id);
ALTER TABLE docs  ADD COLUMN IF NOT EXISTS folder_id BIGINT REFERENCES folders(id);
CREATE INDEX IF NOT EXISTS idx_docs_sector ON docs(sector_id);

-- single-row config blob, editable from the admin Authentication page
CREATE TABLE IF NOT EXISTS auth_config (
    id     INT PRIMARY KEY DEFAULT 1,
    data   JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT one_row CHECK (id = 1)
);
INSERT INTO auth_config (id, data) VALUES (1, '{}'::jsonb)
    ON CONFLICT (id) DO NOTHING;

-- ---- OIDC/SSO CSRF state (cross-worker) ----
-- one short-lived row per in-flight login. Was an in-process dict, which broke
-- with multiple uvicorn workers (login on worker A, callback on worker B ->
-- "invalid state"). Consumed one-shot on callback; expired rows pruned lazily.
CREATE TABLE IF NOT EXISTS oidc_state (
    state       TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- which SSO provider this auth attempt is for (multi-provider). NULL = legacy single.
ALTER TABLE oidc_state ADD COLUMN IF NOT EXISTS pid TEXT;

-- ---- per-user chat conversations + messages ----
CREATE TABLE IF NOT EXISTS conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- user | bot
    text            TEXT,
    pages           JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
-- persisted thinking trace ({steps:[{label,detail}], thought_ms}) so reopened chats show it
ALTER TABLE messages ADD COLUMN IF NOT EXISTS meta JSONB DEFAULT '{}'::jsonb;

-- ---- shareable answer links (read-only permalink to one bot answer) ----
CREATE TABLE IF NOT EXISTS shares (
    token       TEXT PRIMARY KEY,
    message_id  BIGINT REFERENCES messages(id) ON DELETE CASCADE,
    created_by  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    revoked     BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_shares_msg ON shares(message_id);

-- ---- per-answer telemetry (latency / cost / retrieval / grounding) ----
-- ONE row per bot answer, written at the answer chokepoint. Powers the
-- admin "Performance" view: p50/p95 latency, real token spend, retrieval
-- hit-rate, wider-retry rate. Privacy-safe — counts + timings, no chat text.
CREATE TABLE IF NOT EXISTS answer_metrics (
    id              BIGSERIAL PRIMARY KEY,
    message_id      BIGINT,
    conversation_id BIGINT,
    user_id         BIGINT,
    model           TEXT,
    mode            TEXT,
    ms_first_token  INT,                 -- time to first streamed token
    ms_total        INT,                 -- full answer wall-clock
    tok_in          INT DEFAULT 0,
    tok_out         INT DEFAULT 0,
    cost_usd        NUMERIC(10,5) DEFAULT 0,
    seed_n          INT DEFAULT 0,       -- pages from first retrieval
    wide_n          INT DEFAULT 0,       -- extra pages pulled on wider retry
    wider_fired     BOOLEAN DEFAULT FALSE,
    cited_n         INT DEFAULT 0,       -- pages actually cited in the answer
    blind           BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ametrics_created ON answer_metrics(created_at DESC);
-- citation-accuracy verify pass (batch #2): % of cited pages that actually
-- support the answer, set by the verify daemon. NULL = not yet verified.
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS verify_score INT;
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS verify_at TIMESTAMPTZ;
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN DEFAULT FALSE;
-- retrieval funnel (ops cockpit): candidates the SQL pool scanned, the pool size
-- requested, and how many survived rerank. NULL on cache-served / legacy rows.
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS scanned   INTEGER;
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS pool      INTEGER;
ALTER TABLE answer_metrics ADD COLUMN IF NOT EXISTS reranked  INTEGER;

-- ---- per-document ingest event timeline (ops cockpit doc log) ----
-- append-only, one row per meaningful ingest stage transition / per-page event.
-- read oldest-first as a timeline by GET /api/documents/{id}/log. Fail-soft writes.
CREATE TABLE IF NOT EXISTS ingest_events (
    id      SERIAL PRIMARY KEY,
    doc_id  BIGINT,
    ts      TIMESTAMPTZ DEFAULT now(),
    stage   TEXT,
    msg     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_events_doc ON ingest_events(doc_id, id);

-- ---- per-cited-page verdict: does the page back the answer's claims? ----
-- written by app/verify.py (LLM judge). One row per (answer, cited page).
CREATE TABLE IF NOT EXISTS answer_verify (
    id          BIGSERIAL PRIMARY KEY,
    message_id  BIGINT,
    page_id     BIGINT,
    doc_id      BIGINT,
    page_no     INT,
    supports    BOOLEAN,
    confidence  NUMERIC(4,3),
    reason      TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_averify_msg ON answer_verify(message_id);

-- ---- auth / security events (logins, lockouts, role changes) ----
-- append-only feed for the security panel: failed logins, who changed whose role.
CREATE TABLE IF NOT EXISTS auth_event (
    id          BIGSERIAL PRIMARY KEY,
    event       TEXT NOT NULL,          -- login_ok | login_fail | role_change | user_create | user_delete | user_deactivate
    email       TEXT,                   -- subject (the account acted on / attempted)
    actor_email TEXT,                   -- admin who performed it (null for self-login)
    meta        JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_event_created ON auth_event(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_event_evt ON auth_event(event, created_at DESC);

-- ---- admin audit log (governance: who did what) ----
-- one row per privileged mutation (approve/reject/edit/delete a fact, delete or
-- retry a doc, change ROI config). Append-only, admin-readable. Compliance trail.
CREATE TABLE IF NOT EXISTS admin_audit (
    id           BIGSERIAL PRIMARY KEY,
    actor_id     BIGINT,
    actor_email  TEXT,
    actor_role   TEXT,
    action       TEXT NOT NULL,          -- e.g. fact.approve, doc.delete, config.save
    target_type  TEXT,                   -- fact | doc | config | user
    target_id    TEXT,
    meta         JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at DESC);

-- ---- activity feed / notifications ----
CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    level       TEXT NOT NULL DEFAULT 'info',   -- info | success | warn | alert
    read        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- dream cycle run log: one row per self-improvement pass. stats = the step
-- counts (merged/promoted/…); touched = ids changed this run (fact ids, doc ids,
-- entity-link edges) so the UI can highlight "what changed tonight".
CREATE TABLE IF NOT EXISTS dream_runs (
    id          BIGSERIAL PRIMARY KEY,
    stats       JSONB NOT NULL DEFAULT '{}'::jsonb,
    touched     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dream_runs_at ON dream_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC);

-- ---- per-doc answer-accuracy self-eval (UAT) runs — powers the Accuracy page ----
CREATE TABLE IF NOT EXISTS doc_eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    overall     JSONB NOT NULL DEFAULT '{}'::jsonb,
    docs        JSONB NOT NULL DEFAULT '[]'::jsonb,
    gaps        JSONB NOT NULL DEFAULT '[]'::jsonb,
    questions   INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_eval_at ON doc_eval_runs(created_at DESC);

-- ---- Self-Heal agent: per-doc eval-gated self-improvement (parallel worker) ----
-- one row per doc per heal run; discovery = ready docs with no status='done' row.
CREATE TABLE IF NOT EXISTS selfheal_runs (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed
    rounds      INT DEFAULT 0,
    before_acc  INT DEFAULT 0,                      -- grounded% before
    after_acc   INT DEFAULT 0,                      -- grounded% after
    banked      INT DEFAULT 0,                      -- Q&A pairs locked into the bank
    review      JSONB NOT NULL DEFAULT '[]'::jsonb, -- couldn't-self-verify questions
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_selfheal_doc ON selfheal_runs(doc_id, status);
CREATE INDEX IF NOT EXISTS idx_selfheal_at ON selfheal_runs(started_at DESC);
-- streamed activity log (mine/eval/heal/judge/bank) powering the live UI panel.
CREATE TABLE IF NOT EXISTS selfheal_log (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT,
    run_id      BIGINT,
    tag         TEXT,                               -- mine|eval|heal|judge|bank|warn|info
    msg         TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_selfheal_log ON selfheal_log(id DESC);

-- ---- compiled "wiki" layer (Karpathy second-brain): vision-once -> clean markdown ----
-- per-page compiled markdown (tables/steps/codes preserved, nothing omitted).
-- chat reads this instead of raw page text, so a small model still answers in full.
CREATE TABLE IF NOT EXISTS doc_pages_md (
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_no     INT NOT NULL,
    md          TEXT NOT NULL,
    chars       INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (doc_id, page_no)
);
-- per-doc compiled summary + concatenated markdown + extracted key facts
CREATE TABLE IF NOT EXISTS doc_wiki (
    doc_id      BIGINT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    title       TEXT,
    summary     TEXT,
    md          TEXT,
    key_facts   JSONB DEFAULT '[]'::jsonb,
    updated_at  TIMESTAMPTZ DEFAULT now()
);
-- per-doc user-facing PLAYBOOK (goal/who/prereqs/steps/verify/if-fails/escalate)
CREATE TABLE IF NOT EXISTS doc_playbook (
    doc_id        BIGINT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    goal          TEXT,
    who           TEXT,
    prerequisites JSONB DEFAULT '[]'::jsonb,
    steps         JSONB DEFAULT '[]'::jsonb,
    verify        JSONB DEFAULT '[]'::jsonb,
    if_fails      JSONB DEFAULT '[]'::jsonb,
    escalate      TEXT,
    chars         INT  NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT now()
);
-- reference-doc lookup rows: term -> value (exact "what is code 3001") + source page
CREATE TABLE IF NOT EXISTS doc_lookup (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    term        TEXT NOT NULL,
    value       TEXT,
    page_id     BIGINT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_lookup_doc  ON doc_lookup(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_lookup_term ON doc_lookup USING gin (to_tsvector('simple', term));

-- ---- cross-document entity index (Phase 3) ----
-- canonical entities (codes, menu paths, systems, screens, fields, terms) that
-- recur across documents. norm_key = normalized dedupe key.
CREATE TABLE IF NOT EXISTS entities (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT,                 -- code|menu_path|system|screen|field|term
    norm_key    TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS entity_mention (
    id          BIGSERIAL PRIMARY KEY,
    entity_id   BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_id     BIGINT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (entity_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_mention_ent ON entity_mention(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_mention_doc ON entity_mention(doc_id);

-- ---- GraphRAG-A: typed entity->entity relationships (LLM-extracted per doc) ----
-- e.g. "CAR Mass Input —depends_on→ Night Batch Job". Powers multi-hop graph-walk
-- retrieval (recursive CTE over this + doc_dependency + doc_links + entity_mention).
CREATE TABLE IF NOT EXISTS entity_edge (
    id          BIGSERIAL PRIMARY KEY,
    src_entity  BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    dst_entity  BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    rel         TEXT NOT NULL,            -- depends_on|part_of|accessed_via|relates_to|...
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    weight      REAL DEFAULT 1.0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (src_entity, dst_entity, rel, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_edge_src ON entity_edge(src_entity);
CREATE INDEX IF NOT EXISTS idx_entity_edge_dst ON entity_edge(dst_entity);
-- GraphRAG-B: communities (connected clusters) + per-cluster LLM summary for
-- whole-corpus / "global" questions.
CREATE TABLE IF NOT EXISTS entity_community (
    entity_id   BIGINT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    cluster_id  INT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_community_cl ON entity_community(cluster_id);
-- per-embedded-image detailed explanation (SOP screenshots → which screen / element /
-- action). Hybrid extraction: text+tables from the layer, vision spent per screenshot.
CREATE TABLE IF NOT EXISTS doc_image (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_no     INT,
    idx         INT,                 -- image index within the page
    image_path  TEXT,                -- storage key / path to the extracted PNG
    screen      TEXT,                -- which screen/window
    shows       TEXT,                -- what is shown (form/list/dialog/...)
    element     TEXT,                -- highlighted/circled element + its label
    action      TEXT,                -- what to do ("click Search", "enter End Date")
    caption     TEXT,                -- nearby caption text if any
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_image_doc ON doc_image(doc_id, page_no);

CREATE TABLE IF NOT EXISTS community_summary (
    cluster_id  INT PRIMARY KEY,
    label       TEXT,
    summary     TEXT,
    entity_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    doc_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ---- knowledge-base conflicts (Phase 4): same term, divergent value across docs ----
CREATE TABLE IF NOT EXISTS kb_conflict (
    id          BIGSERIAL PRIMARY KEY,
    term        TEXT NOT NULL,
    value_a     TEXT,
    doc_a       BIGINT,
    value_b     TEXT,
    doc_b       BIGINT,
    source      TEXT,                 -- lookup|fact
    detail      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',   -- open|dismissed
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_conflict_status ON kb_conflict(status);

-- ---- Phase 1 (Karpathy wiki): atomic claims extracted per doc ----
-- One row per asserted fact: <entity> <attribute> = <value> at a page. The
-- contradiction pass matches new claims against existing ones on (entity_key,
-- attribute) and LLM-confirms real value conflicts.
CREATE TABLE IF NOT EXISTS doc_claims (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    page_no     INT,
    page_id     BIGINT,
    entity      TEXT NOT NULL,        -- display form, e.g. "Access"
    entity_key  TEXT NOT NULL,        -- normalized (lower, ws-collapsed) for matching
    attribute   TEXT NOT NULL,        -- value | threshold | setting | code | path | ...
    value       TEXT NOT NULL,        -- the asserted value, e.g. "2"
    raw         TEXT,                 -- source sentence/snippet for provenance
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_claims_match ON doc_claims(entity_key, attribute);
CREATE INDEX IF NOT EXISTS idx_doc_claims_doc ON doc_claims(doc_id);

-- Phase 2 (temporal): bi-temporal supersession. A claim is "live" until a conflict
-- is resolved against it — then superseded_at is stamped (invalidate, don't delete),
-- so history stays queryable ("what was the rule before the change").
ALTER TABLE doc_claims ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ DEFAULT now();
ALTER TABLE doc_claims ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
ALTER TABLE doc_claims ADD COLUMN IF NOT EXISTS superseded_by_doc BIGINT;
ALTER TABLE doc_claims ADD COLUMN IF NOT EXISTS supersede_reason TEXT;

-- ---- Phase 1: semantic contradictions between a NEW doc and EXISTING docs ----
CREATE TABLE IF NOT EXISTS claim_conflict (
    id          BIGSERIAL PRIMARY KEY,
    entity      TEXT NOT NULL,
    entity_key  TEXT NOT NULL,
    attribute   TEXT NOT NULL,
    doc_a       BIGINT,               -- the NEW doc
    page_a      INT,
    value_a     TEXT,
    claim_a     BIGINT,               -- doc_claims id (new)
    doc_b       BIGINT,               -- the EXISTING doc
    page_b      INT,
    value_b     TEXT,
    claim_b     BIGINT,               -- doc_claims id (existing)
    severity    TEXT,                 -- value_mismatch | ...
    detail      TEXT,                 -- LLM one-line explanation
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|kept_new|kept_old|both|dismissed
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claim_conflict_status ON claim_conflict(status);

-- ---- procedure chaining (Phase 5): doc A must be done before doc B ----
CREATE TABLE IF NOT EXISTS doc_dependency (
    id          BIGSERIAL PRIMARY KEY,
    from_doc    BIGINT REFERENCES docs(id) ON DELETE CASCADE,  -- the dependent doc
    to_doc      BIGINT REFERENCES docs(id) ON DELETE CASCADE,  -- the prerequisite doc
    reason      TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (from_doc, to_doc)
);
CREATE INDEX IF NOT EXISTS idx_doc_dependency_from ON doc_dependency(from_doc);
CREATE INDEX IF NOT EXISTS idx_doc_dependency_to   ON doc_dependency(to_doc);

-- ---- troubleshooting decision tree (Phase 6.2): for runbook/issue docs ----
CREATE TABLE IF NOT EXISTS doc_tree (
    doc_id      BIGINT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    tree        JSONB NOT NULL DEFAULT '{}'::jsonb,
    chars       INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ---- per-message thumbs up/down feedback ----
CREATE TABLE IF NOT EXISTS message_feedback (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER,
    conversation_id INTEGER,
    vote            TEXT NOT NULL,        -- 'up' | 'down'
    note            TEXT,
    answer          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
-- richer feedback (2026-06-12): the question, cited page ids, a free-text note,
-- an optional correction, and a review status. On a downvote-with-correction the
-- correction is also written as a PENDING memory fact (admin-approve in Brain).
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS question   TEXT;
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS page_ids   JSONB;
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS note       TEXT;
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS correction TEXT;
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS status     TEXT DEFAULT 'new';
-- explicit link to the pending memory fact auto-created from a downvote+correction
-- (lets the Audit downvotes_recent[] expose memory_id + whether it's active yet).
ALTER TABLE message_feedback ADD COLUMN IF NOT EXISTS learned_memory_id INT;

-- ---- live per-step ingest / activity log (streamed to the floating "robot" UI) ----
-- one row per pipeline step (queue/tree/page/compile/ready/failed) + learn/digest.
-- append-only, read by GET /api/ingest/log with an id cursor.
CREATE TABLE IF NOT EXISTS ingest_log (
    id     BIGSERIAL PRIMARY KEY,
    doc_id INT,
    ts     TIMESTAMPTZ DEFAULT now(),
    step   TEXT,
    msg    TEXT,
    level  TEXT DEFAULT 'info'
);

-- ============================================================================
-- Eval Agent (app/eval_agent.py) — offline answer-quality scoring.
-- Golden questions per doc, LLM-judged, rolled up into per-doc health.
-- ============================================================================

-- golden questions for a doc. kind: positive (answer is in doc) | negative
-- (NOT in doc → agent must refuse) | adversarial (false premise → must correct).
-- source: auto (LLM-generated at first eval) | human (curated).
CREATE TABLE IF NOT EXISTS eval_question (
    id           BIGSERIAL PRIMARY KEY,
    doc_id       BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    question     TEXT NOT NULL,
    expected     TEXT,                       -- expected answer (positive) / expected behaviour (neg/adv)
    kind         TEXT NOT NULL DEFAULT 'positive',
    source       TEXT NOT NULL DEFAULT 'auto',
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evalq_doc ON eval_question(doc_id) WHERE active;

-- one row per eval pass (nightly or forced). Scopes a run's results together.
CREATE TABLE IF NOT EXISTS eval_run (
    id           BIGSERIAL PRIMARY KEY,
    trigger      TEXT NOT NULL DEFAULT 'nightly',   -- nightly | manual
    judge_model  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    n_docs       INT DEFAULT 0,
    n_total      INT DEFAULT 0,
    n_pass       INT DEFAULT 0,
    n_fail       INT DEFAULT 0,
    n_partial    INT DEFAULT 0,
    score_pct    REAL,
    started_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

-- one row per (run, question): what the agent answered + how the judge scored it.
CREATE TABLE IF NOT EXISTS eval_result (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT REFERENCES eval_run(id) ON DELETE CASCADE,
    doc_id        BIGINT,
    question_id   BIGINT,
    question      TEXT,
    kind          TEXT,
    expected      TEXT,
    got           TEXT,                       -- agent's actual answer
    verdict       TEXT,                       -- pass | partial | fail
    judge_score   REAL,                       -- 0..1
    judge_reason  TEXT,
    grounded      BOOLEAN,                    -- answer claims found in retrieved pages
    refused       BOOLEAN,                    -- agent said "not in doc"
    retrieved_n   INT,                        -- # pages retrieved for this Q
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evalr_run ON eval_result(run_id);
CREATE INDEX IF NOT EXISTS idx_evalr_doc ON eval_result(doc_id);

-- rolling per-doc health, upserted after each doc is scored. Dashboard reads THIS.
CREATE TABLE IF NOT EXISTS doc_health (
    doc_id        BIGINT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
    last_run_id   BIGINT,
    score_pct     REAL,
    prev_score    REAL,                       -- previous run's score (for delta)
    n_total       INT DEFAULT 0,
    n_pass        INT DEFAULT 0,
    n_fail        INT DEFAULT 0,
    n_partial     INT DEFAULT 0,
    grounded_pct  REAL,
    halluc_count  INT DEFAULT 0,              -- negatives the agent answered (should've refused)
    neg_total     INT DEFAULT 0,
    neg_caught    INT DEFAULT 0,
    needs_review  BOOLEAN DEFAULT FALSE,
    evaluated_at  TIMESTAMPTZ
);

-- ---- usage analytics rollup (per user, per day) — scale to 10k+ users ----
-- nightly/interval daemon (or ensure-on-read) aggregates messages+feedback into
-- ONE row per (user, day). Dashboards read THIS, never scan raw messages live.
-- Counts only — privacy-safe, no chat text anywhere.
CREATE TABLE IF NOT EXISTS usage_daily (
    user_id    BIGINT NOT NULL,
    day        DATE   NOT NULL,
    questions  INT NOT NULL DEFAULT 0,   -- user messages
    convos     INT NOT NULL DEFAULT 0,   -- distinct conversations touched
    sourced    INT NOT NULL DEFAULT 0,   -- bot answers WITH a cited page
    blind      INT NOT NULL DEFAULT 0,   -- bot answers with NO source (blind spot)
    up         INT NOT NULL DEFAULT 0,   -- 👍 given
    down       INT NOT NULL DEFAULT 0,   -- 👎 given
    PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_usage_daily_day ON usage_daily(day);

-- ---- single-row app config blob (ROI knobs etc, admin-editable) ----
CREATE TABLE IF NOT EXISTS app_config (
    id    INT PRIMARY KEY DEFAULT 1,
    data  JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT one_app_config CHECK (id = 1)
);
INSERT INTO app_config (id, data) VALUES (1, '{}'::jsonb) ON CONFLICT (id) DO NOTHING;

-- ---- embeddable widget: public/secret key pairs per customer site ----
CREATE TABLE IF NOT EXISTS embed_keys (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    public_key      TEXT UNIQUE NOT NULL,        -- pk_live_… (safe to expose)
    secret_hash     TEXT NOT NULL,               -- bcrypt of sk_live_… (never stored raw)
    secret_last4    TEXT,                         -- display only: sk_live_…abcd
    allowed_origins TEXT[] NOT NULL DEFAULT '{}', -- [] = allow any origin (dev only)
    rate_per_min    INT  NOT NULL DEFAULT 30,     -- per-visitor message cap
    anon_user_id    BIGINT REFERENCES users(id),  -- shared identity for anonymous visitors
    greeting        TEXT,                         -- widget welcome line
    accent          TEXT DEFAULT '#c2683f',       -- widget bubble color
    active          BOOLEAN NOT NULL DEFAULT true,
    created_by      BIGINT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    last_used_at    TIMESTAMPTZ
);
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS position  TEXT DEFAULT 'right';
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS subtitle  TEXT;
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS logo_url  TEXT;
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS launcher  TEXT DEFAULT 'robot';
-- per-key abuse / spend ceilings (0 = unlimited)
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS daily_msg_cap  INT DEFAULT 0;
ALTER TABLE embed_keys ADD COLUMN IF NOT EXISTS daily_cost_cap NUMERIC(10,2) DEFAULT 0;

-- denied requests (origin / rate) so Monitoring can show blocked counts
CREATE TABLE IF NOT EXISTS embed_events (
    id      BIGSERIAL PRIMARY KEY,
    key_id  BIGINT,
    kind    TEXT NOT NULL,            -- origin_block | rate_block
    at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_embed_events_key ON embed_events(key_id, at);

-- one anonymous-or-identified end user per (key, visitor_id)
CREATE TABLE IF NOT EXISTS embed_visitors (
    id          BIGSERIAL PRIMARY KEY,
    key_id      BIGINT REFERENCES embed_keys(id) ON DELETE CASCADE,
    visitor_id  TEXT NOT NULL,
    name        TEXT,
    user_id     BIGINT REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now(),
    last_seen   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (key_id, visitor_id)
);

-- explicit doc->doc relationships (from OKF import cross-links; markdown
-- [x](/documents/y.md) links resolved to real doc ids). Powers graph edges.
CREATE TABLE IF NOT EXISTS doc_links (
    src_doc  BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    dst_doc  BIGINT REFERENCES docs(id) ON DELETE CASCADE,
    source   TEXT DEFAULT 'okf',
    PRIMARY KEY (src_doc, dst_doc)
);

-- cross-process rate limit: per-(key,visitor) minute bucket. Replaces the old
-- in-process token bucket so multiple workers/replicas share one counter.
CREATE TABLE IF NOT EXISTS embed_rate (
    key_id     BIGINT NOT NULL,
    visitor_id TEXT   NOT NULL,
    minute     BIGINT NOT NULL,            -- floor(epoch / 60)
    n          INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, visitor_id, minute)
);

-- per-key daily roll-up: hard message + dollar ceilings (runaway-cost guard)
CREATE TABLE IF NOT EXISTS embed_usage (
    key_id    BIGINT NOT NULL,
    day       DATE   NOT NULL,
    messages  INT    NOT NULL DEFAULT 0,
    tokens    BIGINT NOT NULL DEFAULT 0,
    cost_usd  NUMERIC(12,5) NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, day)
);
"""


_DDL_KEY = 0x0D0C5E02  # advisory key: serialize CREATE/ALTER across workers


def init_db() -> None:
    # Multiple uvicorn workers may run init_db() at once; concurrent identical
    # DDL can race ("tuple concurrently updated"). Serialize with a session
    # advisory lock so only one worker runs the schema at a time (others no-op).
    with get_conn() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_DDL_KEY,))
        try:
            conn.execute(SCHEMA)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_DDL_KEY,))


def log_ingest(doc_id, step: str, msg: str, level: str = "info") -> None:
    """Append one line to the live ingest/activity log. Fail-soft: never raises
    into the pipeline (own short-lived autocommit connection; swallows errors)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ingest_log (doc_id, step, msg, level) "
                "VALUES (%s, %s, %s, %s)",
                (doc_id, step, (msg or "")[:500], level),
            )
    except Exception as e:  # logging must never crash the worker
        print(f"[ingest_log] write skipped: {e!r}")
