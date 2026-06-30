# City Agent Aria — Deployment & Scaling Guide

Concrete AWS sizing + `.env.prod` config for three user buckets: **100**, **200–300**,
**300–500**. Grounded in the shipped stack: `docker-compose.prod.yml`, `deploy/nginx.conf`,
`.env.prod.example`.

---

## 0. The one thing to understand first

**The box is almost never the bottleneck — the LLM is remote.** App workers spend
~all their time *waiting on OpenRouter*, not computing. So CPU/RAM stay low under user
load. The real ceiling is:

1. `LLM_MAX_CONCURRENCY` — global cap on simultaneous OpenRouter calls (`agent._llm_slot`).
2. Your **OpenRouter rate tier** + **cost**.
3. **Postgres connections** (the pool-math wall, below).

Everything else (CPU, RAM, disk) is cheap because there is no local model — vision +
chat + ingest all call OpenRouter over the network.

### "Concurrent users" ≠ "in-flight requests"
A user reads each answer for 15–60s before asking again. So **N active users → roughly
N/5 to N/10 requests in flight** at any instant, and the zero-LLM Q&A cache serves
repeats. Throughput headroom is far larger than the user count suggests.

```
LLM throughput ceiling  =  LLM_MAX_CONCURRENCY ÷ avg_cold_seconds   (cold ≈ 15s)
  30 slots → 2 ans/s = 120/min
  60 slots → 4 ans/s = 240/min
 100 slots → 6.6 ans/s = 400/min
```
Even 250 concurrent users at 1 question / 2 min = ~125 answers/min — and most hit cache.

### The pool-math invariant (memorize it)
```
replicas × WEB_CONCURRENCY × DB_POOL_MAX  +  ~15 (daemons/leader)  <  PG_MAX_CONNECTIONS
```
Break this → "too many connections" errors under load. Every bucket below honors it.

### What scales which way
| Component | Scale type | How |
|---|---|---|
| **app replicas** | **OUT** | `--scale app=N` — stateless (JWT; rate-limit + OIDC state in PG) |
| nginx | UP | one LB; bigger box or AWS ALB |
| Postgres | UP | bigger instance → RDS → read replica for dashboards |
| **worker** | **stays 1** | `leader.py` PG advisory lock — only one runs daemons |
| page images | shared store | single host = `./data` EBS · multi-host = `STORAGE=s3` |
| OpenRouter | the real ceiling | raise `LLM_MAX_CONCURRENCY` vs your rate tier |

---

## 1. Bucket: ~100 users  (≈30–50 concurrent, ~8–15 in-flight peak)

**Verdict: one box does everything.** Comfortable; LLM ceiling at ~20% utilization.

### AWS
| Role | Instance | vCPU / RAM | Notes |
|---|---|---|---|
| **Recommended** | 1× **t3.xlarge** | 4 / 16 GB | all-in-one: nginx + 2 app replicas + worker + Postgres |
| Minimum | 1× t3.large | 2 / 8 GB | works steady-state; tight during ingest bursts |
| Storage | 100 GB **gp3** EBS | — | `./data` page images |
| Optional | + db.t3.small RDS | 2 / 2 GB | move DB off-box → easy backups/PITR |

### `.env.prod` (key lines)
```bash
APP_ENV=production
WEB_CONCURRENCY=4
DB_POOL_MIN=2
DB_POOL_MAX=10
PG_MAX_CONNECTIONS=200
LLM_MAX_CONCURRENCY=30
INGEST_IN_API=0            # worker owns ingest
WORKER_VISION_WORKERS=12
STORAGE=local
AUTO_QA_SERVE_ENABLED=1    # zero-LLM cache — biggest cost lever, keep ON
```
**Pool check:** `2 × 4 × 10 + 15 = 95 < 200` ✓ (room to grow to app=4).

### Deploy
```bash
cp .env.prod.example .env.prod   # fill OPENROUTER_API_KEY, JWT_SECRET(64), PUBLIC_URL, SUPERADMIN_*
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --scale app=2
```

### Memory budget (t3.xlarge)
2 replicas ×4 workers ×~200 MB ≈ 1.6 GB · worker idle ~300 MB / ingest burst ~1–2 GB ·
Postgres ~0.5–1 GB · OS+docker ~1 GB → **~5 GB steady, ~7 GB burst**. 16 GB safe.

### Cost (us-east-1, on-demand, approx/mo)
EC2 t3.xlarge ~$120 · EBS 100 GB ~$8 · (optional RDS db.t3.small ~$25) ·
**LLM ~$25–40** (100u × ~10 q/day × 3500 tok on flash-lite; less with cache).
**Reserved/Savings Plan cuts EC2 ~40%.**

---

## 2. Bucket: 200–300 users  (≈75–150 concurrent, ~20–40 in-flight peak)

**Verdict: split the database off the app box.** Add a third replica. Still single-host
for app + worker, but Postgres → RDS so the DB has its own resources, backups, PITR.

### AWS
| Role | Instance | vCPU / RAM | Notes |
|---|---|---|---|
| **App + worker** | 1× **m5.xlarge** | 4 / 16 GB | nginx + 3 app replicas + worker |
| ↑ headroom | t3.2xlarge | 8 / 32 GB | if heavy ingest overlaps peak chat |
| **Database** | **RDS db.t3.medium** | 2 / 4 GB | managed, backups, PITR; bump to db.m5.large if dashboards heavy |
| Storage | 150 GB gp3 EBS | — | page images (single host) |

### `.env.prod` changes vs bucket 1
```bash
PG_MAX_CONNECTIONS=300          # RDS parameter group
WEB_CONCURRENCY=4
DB_POOL_MAX=8                   # trimmed so 3 replicas fit
LLM_MAX_CONCURRENCY=60          # only if OpenRouter tier allows
WORKER_VISION_WORKERS=12
DATABASE_URL=postgresql://docsensei:***@aria-db.xxxx.rds.amazonaws.com:5432/docsensei
# delete the compose `db:` service when using RDS
```
**Pool check:** `3 × 4 × 8 + 15 = 111 < 300` ✓ (room to app=6).

### Deploy
```bash
docker compose -f docker-compose.prod.yml up -d --scale app=3
# DB now external → in docker-compose.prod.yml remove the `db:` service + its depends_on,
# point DATABASE_URL at RDS.
```

### Notes
- **Worker stays 1.** If mass-upload ingest lags chat, raise `WORKER_VISION_WORKERS`
  (CPU permitting) before adding a host.
- `STORAGE=local` is fine — all app replicas + worker share the same `./data` EBS on one host.
- Turn on a read-heavy dashboard? RDS read replica optional; the `usage_daily` rollup
  already keeps analytics off the hot path.

### Cost (approx/mo)
m5.xlarge ~$140 · RDS db.t3.medium ~$60 (single-AZ) · EBS ~$12 ·
**LLM ~$50–90**. Total ~$260–300 + LLM. Reserved cuts compute ~40%.

---

## 3. Bucket: 300–500 users  (≈150–250 concurrent, ~40–70 in-flight peak)

**Verdict: cattle, not pets.** Go multi-host-capable: object storage for images, managed
DB (ideally multi-AZ), 4–6 app replicas. The app tier becomes horizontally replaceable;
the worker stays a singleton via the leader lock.

### AWS
| Role | Instance | vCPU / RAM | Notes |
|---|---|---|---|
| **App tier** | 2× **m5.xlarge** behind ALB | 4 / 16 GB each | 4–6 replicas total; or 1× m5.2xlarge (8/32) app=5 single-host |
| **Worker** | 1× **t3.large** | 2 / 8 GB | dedicated ingest+daemons; 2nd container = hot standby (lock) |
| **Database** | **RDS db.m5.large, Multi-AZ** | 2 / 8 GB | + read replica for dashboards; PITR + failover |
| **Images** | **S3** (`STORAGE=s3`) | — | required once app spans >1 host |
| LB | AWS ALB (or keep nginx) | — | health checks, TLS, autoscaling target group |

### `.env.prod` changes vs bucket 2
```bash
PG_MAX_CONNECTIONS=500           # RDS parameter group (db.m5.large handles it)
WEB_CONCURRENCY=4
DB_POOL_MAX=8
LLM_MAX_CONCURRENCY=100          # HARD-gated by your OpenRouter rate tier — confirm first
WORKER_VISION_WORKERS=16
INGEST_IN_API=0
STORAGE=s3                       # MUST: replicas on different hosts can't share EBS
S3_BUCKET=aria-prod
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
DATABASE_URL=postgresql://docsensei:***@aria-db.xxxx.rds.amazonaws.com:5432/docsensei
```
**Pool check (6 replicas):** `6 × 4 × 8 + 15 = 207 < 500` ✓ (huge headroom; could raise
`DB_POOL_MAX` back to 10 → `6×4×10+15 = 255 < 500`).

### Deploy
- **Single big host** (simplest): `--scale app=5` on one m5.2xlarge, `STORAGE=s3` (or EBS).
- **Multi-host** (HA): run the prod compose per host OR move to ECS/EKS; ALB target group
  fronts all app tasks; one worker task with the leader lock; RDS Multi-AZ.

```bash
# single host
docker compose -f docker-compose.prod.yml up -d --scale app=5
```

### The real limits at this size
- **OpenRouter rate tier is now THE wall.** 100 concurrent LLM calls needs a tier that
  permits it; else you get 429s. `agent._chat_stream_with_retry` retries once, but past
  that you must (a) raise the tier or (b) lift cache hit-rate. **Keep `AUTO_QA_SERVE_ENABLED=1`
  and approve Q&A pairs aggressively** — a 50% cache rate halves cold load and cost.
- **Postgres** — 3 writers is trivial; dashboard reads grow → use the RDS read replica.
- **Page images** — S3 + `S3_PRESIGN=1` so browsers pull images straight from S3 (app
  serves zero image bytes).

### Cost (approx/mo)
2× m5.xlarge ~$280 · t3.large worker ~$60 · RDS db.m5.large Multi-AZ ~$250 ·
ALB ~$20 · S3 ~$5 · **LLM ~$110–160** (500u × ~10 q/day; cache cuts ~half).
Total ~$700 + LLM. Reserved/Savings Plan cuts compute ~40% → ~$450 + LLM.

---

## 4. Side-by-side

| | 100 | 200–300 | 300–500 |
|---|---|---|---|
| App host | 1× t3.xlarge | 1× m5.xlarge | 2× m5.xlarge (ALB) or 1× m5.2xlarge |
| Replicas (`--scale app=`) | 2 | 3 | 4–6 |
| Worker | on app host | on app host | dedicated t3.large |
| Database | on-box / db.t3.small | RDS db.t3.medium | RDS db.m5.large Multi-AZ + read replica |
| `LLM_MAX_CONCURRENCY` | 30 | 60 | 100 (tier-gated) |
| `PG_MAX_CONNECTIONS` | 200 | 300 | 500 |
| `DB_POOL_MAX` | 10 | 8 | 8 |
| Images | EBS (`local`) | EBS (`local`) | **S3** (`s3`) |
| Pool-math | 95<200 | 111<300 | 207<500 |
| AWS $/mo (on-demand) | ~$130 | ~$260 | ~$700 |
| LLM $/mo | ~$25–40 | ~$50–90 | ~$110–160 |

*All LLM costs assume `gemini-3.1-flash-lite`, ~3500 tok/answer, ~10 q/user/day, 22 working
days; ~40–60% Q&A cache hit cuts this roughly in half.*

---

## 5. Before go-live (every bucket) — `docs/PRODUCTION.md`

1. `APP_ENV=production` + strong `JWT_SECRET` ≥32 chars — boot guard
   (`config.check_prod_config`) hard-fails on weak/default secrets.
2. **TLS**: mount certs, uncomment `:443` in compose + nginx, set `PUBLIC_URL=https://…`
   (HTTPS also unlocks PWA install). HSTS middleware already present.
3. **Backups**: cron `scripts/backup.sh` (pg_dump -Fc + page-image tar + optional S3).
   Self-hosted single DB has no replica — backups are mandatory. RDS = automated PITR.
4. Rotate seeded dev creds (`admin@city.com/secret123`); set real `SUPERADMIN_*`.
5. `CORS_ALLOW_ORIGINS` = your domain only (empty default = same-origin, fine for the widget).

## 6. Monitoring → when to scale up

Watch the admin dashboards (all already built):
- **Performance** (`/api/analytics/perf`) — p95 latency, **cache_hit_rate**, cost/answer,
  **wider_rate** (blind-spot retries), token spend.
- **System** (`/api/ops`) — ingest queue/stuck, daemon liveness, DB size + biggest tables.
- **Cockpit vitals** (`/api/dashboard/vitals`) — active-now, questions today, cost today.

**Scale triggers:**
| Symptom | Cause | Action |
|---|---|---|
| p95 latency climbing under load | LLM slots saturated | ↑ `LLM_MAX_CONCURRENCY` (vs tier) or ↑ cache hit-rate |
| "too many connections" errors | pool-math violated | ↓ `DB_POOL_MAX` or ↑ `PG_MAX_CONNECTIONS` |
| OpenRouter 429s | rate tier exceeded | raise tier; approve more Q&A cache pairs |
| app CPU pegged (rare) | many concurrent streams | `--scale app=N+1` |
| ingest queue backing up | vision throughput | ↑ `WORKER_VISION_WORKERS` or dedicated worker host |
| DB CPU high from dashboards | analytics reads | add RDS read replica |

## 7. FAQ

**Q: Can I just run more workers to go faster?**
No — `worker` is leader-gated (one active via PG advisory lock `0x0D0C5E01`). A second
worker container is a hot standby that takes over if the leader dies. Scale ingest *up*
(`WORKER_VISION_WORKERS`), not out.

**Q: Why does the app barely use CPU?**
No local model. Vision/chat/ingest all call OpenRouter; workers mostly block on network.
That's why a small box serves many users — and why the LLM tier, not the box, is the limit.

**Q: Single host or split DB?**
≤100 users: single host is fine. ≥200: move DB to RDS for backups/PITR/failover even if
load is light — operational safety, not throughput.

**Q: When is S3 required?**
The moment app replicas live on >1 host. EBS can't be shared cross-host; `STORAGE=s3`
makes every replica read/write images from object storage.
