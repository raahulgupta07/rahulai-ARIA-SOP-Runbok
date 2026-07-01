# City Agent Aria — Complete Handbook

**One document: what it is, how it's built, how it works, how to install it, and how
it scales from 100 to 1000+ users without losing data.**

---

## Table of contents
1. [What Aria is](#1-what-aria-is)
2. [Architecture & design](#2-architecture--design)
3. [How it works (the pipelines)](#3-how-it-works-the-pipelines)
4. [Installation](#4-installation)
5. [Configuration](#5-configuration)
6. [Authentication & multi-tenant access (RBAC)](#6-authentication--multi-tenant-access-rbac)
7. [Scaling: 100 → 1000 users](#7-scaling-100--1000-users)
8. [Data safety — scale without losing data](#8-data-safety--scale-without-losing-data)
9. [Operations (day-2)](#9-operations-day-2)
10. [Troubleshooting](#10-troubleshooting)
11. [Reference](#11-reference)

---

## 1. What Aria is

A bilingual (English + Burmese) **single-agent assistant over your company's SOPs,
runbooks and policies** — for ITSM tasks (procedures, site provisioning, user admin,
batch ops). Upload documents (PDFs + images); ask questions in chat; get answers with
the exact steps, codes and **cited source pages**.

**Key design choices:**
- **Vectorless RAG** — no embeddings. Retrieval is Postgres full-text + trigram +
  a precomputed document tree. Cheap, Burmese-friendly, runs on CPU, no GPU.
- **Vision once at ingest** — page images are read by a vision model when the doc is
  ingested, then never re-scanned; chat reads the stored text.
- **Single deployable** — one FastAPI process serves the API **and** the SvelteKit web
  app on one port, plus Postgres. Packaged as one Docker image.
- **Self-improving** — learns facts from chat (with a review gate) and consolidates its
  knowledge nightly, with no human in the loop.

The only external dependency is **OpenRouter** (Gemini models) for the LLM.

---

## 2. Architecture & design

```
                          ┌──────────── one host (or k8s at scale) ───────────┐
   users ──HTTPS──▶ nginx │  app replica 1 ─┐                                  │
                    /ALB  │  app replica 2 ─┼─▶ reads/writes                   │
                    (TLS) │  app replica N ─┘        │                         │
                          │      ▲ stateless         ▼                         │
                          │      │ (JWT)      ┌────────────┐  ┌──────────┐     │
                          │  worker ×1 ──────▶│ Postgres   │  │ data/ or │     │
                          │  (leader-locked)  │ (ALL DATA) │  │   S3     │     │
                          │  ingest+daemons   └────────────┘  └──────────┘     │
                          └─────────────────────────┬──────────────────────────┘
                                                    ▼ remote
                                          OpenRouter (Gemini) — the LLM
```

| Component | Role |
|---|---|
| **app** (FastAPI + SvelteKit) | Stateless. Serves API `/api/*` + the SPA. Auth = JWT; rate-limits/OIDC-state in Postgres → run **N replicas**, rebuild anytime. |
| **worker** | Ingest + all background daemons. **Exactly one** runs them (Postgres advisory leader-lock) even with many replicas. |
| **Postgres 17** | Single source of truth: documents, page text, **chat (conversations + messages)**, facts, Q&A bank, users, sectors, analytics. |
| **data/ or S3** | Rendered page images (PNG). Local bind-mount on one host; **S3 when multi-host**. |
| **OpenRouter** | Remote LLM (chat, ingest vision, enrichers). The only heavy dependency; the real throughput/cost ceiling. |

**Backend modules (high level):** `retrieve` (hybrid search) · `agent` (2-speed
answerer) · `ingest`/`worker`/`vision` (pipeline) · `learn`/`qa`/`governance`/`dream`
(self-learning) · `auth/*` + `rbac` (access) · `analytics`/`dashboard`/`ops` (admin) ·
`embed` (widget) · `leader` (daemon election) · `db` (pooled Postgres).

---

## 3. How it works (the pipelines)

### 3.1 Ingest (document → knowledge) — two-phase
1. Upload → file saved to the inbox, a `docs` row is queued, returns instantly.
2. The worker claims it (`FOR UPDATE SKIP LOCKED`) and runs **Phase 1** (fast, no LLM):
   render pages to PNG, pull the text layer, index → status **`ready_lite`** in seconds
   → **the doc is answerable now**.
3. **Phase 2** (background "Enrichment Agent", throttled): vision-reads each page,
   builds the PageIndex tree, compiles a clean "wiki" markdown, then runs enrichers
   (playbook, entities, lookup, dependencies, troubleshoot tree, Q&A mining) →
   status **`ready`**.

> `ready_lite` = "answerable, still enriching" — not an error. Each enrichment stage is
> an LLM call, so deep enrichment takes minutes per doc, but users aren't blocked.

### 3.2 Chat (question → grounded answer)
`/api/ask/stream` (NDJSON: meta → tokens → done):
1. **Global/meta?** "summarize all docs", "what do we have" → a grounded corpus overview.
2. **Q&A cache?** A near-identical approved question → served instantly, **zero LLM**.
3. **Retrieve** (vectorless hybrid): FTS + trigram + section-tree match + salience,
   LLM query-expansion, relevance rerank, depth-fill (pull the right doc's body pages),
   entity- and graph-walk fill (pull linked/prerequisite docs).
4. **Off-topic gate** — clearly out-of-scope questions get a polite refusal *before* the LLM.
5. **Answer** — quick mode (streamed text) or deep mode (Agno agent + page images),
   with inline `[N]` citations mapped to real page ids.
6. **Blind-spot loop** — if the answer cites nothing, it searches wider once, then flags
   it honestly rather than guessing.
7. **Persist + learn** — saves the turn, records telemetry, and (gated) may learn a fact.

### 3.3 Self-learning
- **Layer A** (reactive): when a user message looks like teaching ("remember…"), extract
  a fact. **Layer B** (optional): mine every turn for durable facts.
- **Governance gate** — learned facts land **pending** and don't affect answers until
  approved (per-source policy: human=auto, doc≥0.90, chat=review, …).
- **Dream cycle** (nightly daemon): dedup facts, promote aged-confident pending facts,
  retire stale ones, resolve conflicts, recompute page salience, auto-link entities,
  optionally fill demand gaps — all fail-soft, no human.

---

## 4. Installation

### Prerequisites
- Docker + Docker Compose
- An **OpenRouter API key** (https://openrouter.ai)
- No GPU, ~2 GB disk

### Quick install (local / pilot)
```bash
git clone https://github.com/raahulgupta07/rahulai-ARIA-SOP-Runbok.git aria && cd aria
cp .env.example .env
#   set: OPENROUTER_API_KEY, JWT_SECRET ($(openssl rand -hex 32)),
#        SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD
docker compose up -d --build
# open http://localhost:8082  →  log in as your SUPERADMIN
```
Then: **Sources → + Add** (upload a SOP) → **Chat** (ask about it → cited answer).
Verify: `curl -s http://localhost:8082/api/health` → `{"status":"ok"}`.

### Production install
```bash
cp .env.prod.example .env.prod      # OPENROUTER_API_KEY, JWT_SECRET, APP_ENV=production,
                                    # PUBLIC_URL=https://…, SUPERADMIN_*, POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --scale app=2
```
`docker-compose.prod.yml` = nginx LB + scalable app replicas + a dedicated worker.
`APP_ENV=production` activates a **boot guard** that refuses to start with a weak
`JWT_SECRET`. Full smoke check: `python3 scripts/smoke.py`.

---

## 5. Configuration

All config is env vars in `.env` (gitignored). The essentials:

| Var | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | **required** — the LLM key |
| `JWT_SECRET` | **required in prod** — ≥32 random chars (boot guard) |
| `APP_ENV` | `dev` or `production` |
| `PUBLIC_URL` | external URL (OIDC redirects, share links) |
| `SUPERADMIN_EMAIL` / `_PASSWORD` | first admin, re-applied each boot |
| `WEB_CONCURRENCY` / `DB_POOL_MAX` / `PG_MAX_CONNECTIONS` | concurrency (see scaling) |
| `LLM_MAX_CONCURRENCY` | cap on simultaneous LLM calls (the throughput knob) |
| `AUTO_QA_SERVE_ENABLED` | zero-LLM answer cache (keep `1` for cost) |
| `STORAGE` | `local` (one host) or `s3` (multi-host) |
| `RBAC_ENABLED` | multi-tenant access (also a live UI toggle, §6) |

There are ~60 fine-grained flags (ingest enrichers, retrieval tuning, daemons) — all
have defaults; tune only when needed.

---

## 6. Authentication & multi-tenant access (RBAC)

**Auth:** local (bcrypt) + LDAP/AD + SSO/OIDC, all merged by email into one user, issued
a JWT (Bearer token). First user = admin; the rest default to `user`.

**Roles:**
| Role | Can do |
|---|---|
| `admin` (super-admin) | everything: Settings, upload, delete, configure access |
| `sector_admin` | manage docs in **their own sector** only |
| `user` | chat only |
| All-Access group | read **all** sectors (read-only) |

**Multi-tenant model (when `RBAC_ENABLED` on):**
- A **sector** = an email domain (`alice@acme.com` → sector `acme.com`), auto-created and
  auto-assigned at every login.
- Users see only documents/folders in **their sector** or shared with them; super-admin
  sees all.
- Toggle it live in **Settings → Access** (no restart). Before enabling on existing data,
  run `scripts/backfill_sectors.py` so pre-RBAC docs/users get a sector (else only the
  super-admin sees them).

Set up sectors, roles and groups under **Settings → Access**; add users under
**Settings → Users**.

---

## 7. Scaling: 100 → 1000 users

**The one rule:** the box is *not* the bottleneck — there's no local model, every LLM
call is remote, so app workers block on the network and CPU/RAM stay low. The real
ceilings are `LLM_MAX_CONCURRENCY` + your **OpenRouter rate tier**, and the Postgres
connection count (the "pool-math" wall).

**"40% concurrent"** = active at peak. People read each answer 15–60s before re-asking,
so in-flight requests ≈ active ÷ 7. The Q&A cache serves repeats at zero LLM.

### Sizing (40% concurrent)
| Users | Active | In-flight | App tier | `app=` | Database | Images | `PG_MAX` | `LLM_MAX` | $/mo |
|---:|---:|---:|---|---:|---|---|---:|---:|---:|
| 100 | 40 | 6 | 1× t3.xlarge | 2 | on-box / db.t3.small | local | 200 | 30 | ~145 |
| 250 | 100 | 14 | 1× m5.xlarge | 3 | RDS db.t3.medium | local | 200 | 40 | ~296 |
| 500 | 200 | 29 | 2× m5.xlarge + ALB | 5 | RDS m5.large Multi-AZ | **S3** | 300 | 60 | ~773 |
| 750 | 300 | 43 | 2–3× m5.xlarge + ALB | 7 | + read replica | **S3** | 300 | 80 | ~809 |
| 1000 | 400 | 57 | 3× m5.xlarge + ALB | 9 | m5.large MAZ + replica | **S3** | 400 | 100 | ~845 |

`WEB_CONCURRENCY=4`. **Pool-math invariant:**
`replicas × WEB_CONCURRENCY × DB_POOL_MAX + 15 < PG_MAX_CONNECTIONS`
(1000: `9×4×8+15 = 303 < 400` ✓). Plan any point: `python3 scripts/scale_plan.py --users N --concurrency 0.4`.

### The journey
```
100 single host → 250 split DB to RDS → 500 S3 + ALB (multi-host) → 750 +replicas → 1000 +read replica
```
- **≤250:** one box, then move Postgres to managed **RDS** (backups + PITR).
- **≥500:** app spans hosts → **`STORAGE=s3` required** (a bind mount can't be shared) +
  ALB + Multi-AZ RDS.
- **750→1000:** add app replicas + an **RDS read replica** for dashboards; raise
  `LLM_MAX_CONCURRENCY` and `PG_MAX_CONNECTIONS`.
- **The worker stays 1** at every stage (leader-locked). Scale ingest *up*
  (`WORKER_VISION_WORKERS`), never out.

### Where the limits bite
| Symptom | Fix |
|---|---|
| p95 latency climbing | ↑ `LLM_MAX_CONCURRENCY` (vs tier) · ↑ cache hit-rate |
| OpenRouter 429s | raise the OpenRouter tier · approve more Q&A cache pairs |
| "too many connections" | ↓ `DB_POOL_MAX` or ↑ `PG_MAX_CONNECTIONS` |
| DB CPU high (dashboards) | add an RDS read replica |
| ingest queue backing up | ↑ `WORKER_VISION_WORKERS` · dedicated worker host |

**Cost insight:** AWS is ~flat 500→1000 (~$700–850) — you add cheap replicas, not bigger
boxes. Variable cost + the wall = LLM ($15→$145/mo) + OpenRouter tier. The Q&A cache is
the biggest lever on both — keep it on.

---

## 8. Data safety — scale without losing data

**All data lives in Postgres + `data/`/S3, never in the app container.** So these are all
**safe** (no data loss): `up -d --build --force-recreate`, `--scale app=N`, `restart`,
and `down` (without `-v`).

⛔ **The only ways to lose data** (never in production):
```bash
docker compose ... down -v            # deletes the Postgres volume = all data + chat
docker volume rm ..._docsensei_pg     # deletes the database
rm -rf ./data                         # deletes all page images
```

**Persistence map:**
| Data | Lives in | Survives rebuild/scale? |
|---|---|---|
| Documents, pages | Postgres | ✅ |
| **Chat history** | Postgres (`conversations`/`messages`) | ✅ |
| Facts, Q&A, users, sectors | Postgres | ✅ |
| Page images | `data/` or S3 | ✅ |
| Secrets/flags | `.env` (host, gitignored) | ✅ |

Back up daily with `scripts/backup.sh` (pg_dump + page-image tar), or use RDS automated
backups + point-in-time recovery.

---

## 9. Operations (day-2)

| Task | Command |
|---|---|
| Update to new code | `git pull && docker compose up -d --build --force-recreate app worker` |
| Logs | `docker compose logs -f app` |
| Stop (keep data) | `docker compose down` |
| Back up | `./scripts/backup.sh` |
| Add capacity (prod) | `docker compose -f docker-compose.prod.yml up -d --scale app=4` |
| Verify everything | `python3 scripts/smoke.py` |
| Plan infra for N users | `python3 scripts/scale_plan.py --users N --concurrency 0.4` |

**Monitor** (admin dashboards): Performance (p95 latency, cache-hit-rate, cost),
System/Ops (ingest queue, daemon liveness, DB size), Vitals (active-now, cost today).

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Chat says "No LLM" / answers fail | `OPENROUTER_API_KEY` missing/wrong → fix `.env` + `up -d --force-recreate app` |
| Won't start in production | weak `JWT_SECRET` with `APP_ENV=production` (boot guard) → ≥32 random chars |
| Login fails | wrong `SUPERADMIN_*` (re-applied each boot) |
| New UI not showing | hard-refresh (Cmd/Ctrl+Shift+R) — content-hashed assets |
| Doc stuck at `ready_lite` | answerable; background enrichment finishing — not an error |
| Users see no docs after enabling RBAC | run `scripts/backfill_sectors.py` (pre-RBAC docs had no sector) |
| "too many connections" under load | pool-math violated → ↓ `DB_POOL_MAX` / ↑ `PG_MAX_CONNECTIONS` |

---

## 11. Reference

- `docs/DEPLOYMENT.md` — architecture + data-safety deep dive
- `docs/SCALING.md` / `docs/SCALING_100_1000.md` — sizing per user count
- `docs/INSTALL.md` — install steps
- `docs/SMOKE_TEST.md` — verification / go-no-go gate
- `docs/PRODUCTION.md` — production hardening checklist
- `EMBED.md` — embeddable chat widget
- `scripts/scale_plan.py` — users → config planner
- `scripts/smoke.py` — end-to-end health runner
- `scripts/backfill_sectors.py` — assign sectors before enabling RBAC

---

*City Agent Aria — bilingual, vectorless, self-improving runbook assistant. One Docker
image, one Postgres, remote LLM. Scales by adding stateless replicas; the LLM tier is the
ceiling, not the box.*
