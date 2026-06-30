# City Agent Aria — Deployment, Scaling & Data Safety

How to run Aria for **100 users**, how to **scale up later**, and — most
importantly — how to **rebuild / redeploy / scale without losing any data
(documents, chat history, facts, users)**.

> TL;DR: **All data lives in Postgres + the `data/` directory, NEVER inside the
> app container.** You can rebuild and scale app containers freely. You only lose
> data if you delete the Postgres volume (`down -v`) or the `data/` dir. Back up
> regularly with `scripts/backup.sh`.

---

## 1. Architecture

```
                          ┌──────────────── one host (or k8s) ────────────────┐
   users ──HTTPS──▶ nginx │  app replica 1 ─┐                                  │
                    (TLS, │  app replica 2 ─┼─▶  reads/writes                  │
                     LB)  │  app replica N ─┘        │                         │
                          │       ▲ stateless        │                         │
                          │       │ (JWT in token)   ▼                         │
                          │  worker ×1 (leader)   ┌────────────┐  ┌──────────┐ │
                          │  ingest + daemons ───▶│ Postgres   │  │ data/    │ │
                          │                       │ (ALL DATA) │  │ (images) │ │
                          └───────────────────────┴────────────┴──┴──────────┘ │
                                          │ remote
                                          ▼
                                 OpenRouter (Gemini) — the LLM
```

- **app** — stateless FastAPI + SvelteKit SPA. Auth is a JWT carried by the client;
  rate-limits / OIDC state / caps live in Postgres. → you can run **N replicas**
  and rebuild them anytime; they hold no data.
- **worker** — runs ingest + all background daemons. **Exactly one** runs them
  (Postgres advisory leader-lock, `app/leader.py`), even with many replicas.
- **Postgres** — the single source of truth for **everything**: documents, page
  text, **conversations + messages (chat history)**, facts/Q&A, users, embeddings
  (none — vectorless), analytics.
- **data/** — rendered page images (PNGs) + the upload inbox. (Or S3, see §6.)
- **OpenRouter** — the only heavy dependency; remote. The box is I/O-bound on it.

---

## 2. Where every piece of data lives (the persistence model)

| Data | Stored in | Survives `up/build/recreate/scale`? | Lost if… |
|---|---|---|---|
| Documents, pages, outline | Postgres tables | ✅ yes | PG volume deleted |
| **Chat history (conversations, messages)** | Postgres `conversations`/`messages` | ✅ yes | PG volume deleted |
| Facts, Q&A bank, governance | Postgres | ✅ yes | PG volume deleted |
| Users, roles, sectors, folders | Postgres | ✅ yes | PG volume deleted |
| Page images (PNG) | `./data/pages/` (or S3) | ✅ yes (bind mount / S3) | `data/` deleted |
| App code / config | the container image | rebuilt each deploy | — (no data here) |
| Secrets, flags | `.env` (gitignored) | ✅ on host | host wiped |

**The golden rule:** containers are disposable; **Postgres + `data/` are not.**

---

## 3. Infra for ~100 users

100 internal users ≈ 30–50 concurrent peak, ~8–15 requests in flight. The box is
**over-provisioned** for this — the LLM (remote) is the only real ceiling.

| Role | Recommended | Notes |
|---|---|---|
| App + worker + Postgres | **1× t3.xlarge** (4 vCPU / 16 GB) | all-in-one via `docker-compose.prod.yml` |
| Disk | 100 GB **gp3** EBS | holds `./data` page images |
| (optional) DB | db.t3.small **RDS** | move DB off-box for managed backups/PITR |

`.env.prod` concurrency block for 100 users:
```bash
WEB_CONCURRENCY=4
DB_POOL_MIN=2
DB_POOL_MAX=10
PG_MAX_CONNECTIONS=200
LLM_MAX_CONCURRENCY=30
INGEST_IN_API=0          # the worker container ingests
AUTO_QA_SERVE_ENABLED=1  # zero-LLM cache ON (cost lever)
```
**Pool-math invariant** (must always hold):
`replicas × WEB_CONCURRENCY × DB_POOL_MAX + 15 < PG_MAX_CONNECTIONS`
→ `2×4×10 + 15 = 95 < 200` ✓

Rough cost: ~$130/mo AWS + ~$25–40/mo LLM. (See `docs/SCALING.md` for 200–500
buckets, or run `python3 scripts/scale_plan.py --users N`.)

---

## 4. First deploy (production)

```bash
cp .env.prod.example .env.prod      # fill: OPENROUTER_API_KEY, JWT_SECRET(64 chars),
                                    # APP_ENV=production, PUBLIC_URL, SUPERADMIN_*, POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --scale app=2
```
First boot creates the schema + the super-admin from `.env`. Then smoke-test:
```bash
SMOKE_URL=https://aria.yourco.com SMOKE_ADMIN_EMAIL=admin@yourco.com \
SMOKE_ADMIN_PASSWORD=*** python3 scripts/smoke.py --no-write
```

---

## 5. Scaling & redeploying WITHOUT losing data

Because data lives in Postgres + `data/`, these are all **safe** (no data loss):

```bash
# deploy new code (rebuild image, replace app containers — data untouched)
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --force-recreate app worker

# scale out / in (add or remove app replicas — data untouched)
docker compose -f docker-compose.prod.yml up -d --scale app=4

# restart everything (volume kept)
docker compose -f docker-compose.prod.yml restart

# stop the stack but KEEP data (NO -v)
docker compose -f docker-compose.prod.yml down        # ✅ safe — volume kept
```

### ⛔ DATA-LOSS commands — never run these in production
```bash
docker compose ... down -v            # ⛔ DELETES the Postgres volume (all data + chat)
docker volume rm <project>_docsensei_pg   # ⛔ deletes the DB
rm -rf ./data                          # ⛔ deletes all page images
```
`down -v` is ONLY for a deliberate fresh install on a throwaway environment.

### Scaling steps as you grow
1. **More users on one host** → `--scale app=N` (mind the pool-math; raise
   `PG_MAX_CONNECTIONS` if needed). Worker stays 1 (leader-locked).
2. **Multi-host / HA** → set `STORAGE=s3` (page images shared across hosts, since a
   bind mount can't be) and point `DATABASE_URL` at **managed Postgres (RDS)**. Now
   app containers are fully disposable cattle behind an ALB.
3. **The real ceiling** is `LLM_MAX_CONCURRENCY` + your OpenRouter rate tier — not
   the box. Keep the Q&A cache on (`AUTO_QA_SERVE_ENABLED=1`).

---

## 6. Page images: bind mount vs S3

- **Single host:** `STORAGE=local` — images in `./data/pages/` (the bind mount in
  compose). All replicas on that host share it. Back it up with the DB.
- **Multi-host:** `STORAGE=s3` + `S3_BUCKET=…` — every replica reads/writes images
  from object storage. Required once app spans more than one machine (EBS/bind
  mounts can't be shared cross-host). Optionally `S3_PRESIGN=1` so browsers pull
  images straight from S3.

Switching backends after ingest: images written under one backend aren't
auto-migrated to the other — pick the backend **before** ingesting, or re-ingest.

---

## 7. Backups & restore (do this — it's your only real safety net)

A single self-hosted Postgres has **no replica**; backups are mandatory.

```bash
# back up (pg_dump custom-format + page-image tar + optional S3 upload)
./scripts/backup.sh

# schedule it (cron, daily 02:30) — edit crontab -e:
30 2 * * *  cd /opt/aria && ./scripts/backup.sh >> /var/log/aria-backup.log 2>&1
```

**Restore drill (practice it once before go-live):**
```bash
# restore the DB dump into a (fresh) Postgres
pg_restore -U docsensei -d docsensei --clean --if-exists path/to/backup.dump
# restore page images
tar xzf path/to/pages-backup.tar.gz -C ./data
```
Managed RDS gives you automated daily backups + point-in-time recovery — preferred
at 200+ users.

---

## 8. Go-live checklist

- [ ] `APP_ENV=production` + strong `JWT_SECRET` (≥32 chars) — boot guard enforces it
- [ ] TLS: certs mounted, `:443` enabled in nginx/compose, `PUBLIC_URL=https://…`
- [ ] `scripts/backup.sh` scheduled in cron **and one restore drill passed**
- [ ] Dev creds rotated (`admin@city.com/secret123` → real `SUPERADMIN_*`)
- [ ] `AUTO_QA_SERVE_ENABLED=1` (cache on for cost)
- [ ] `python3 scripts/smoke.py` → GO (see `docs/SMOKE_TEST.md`)
- [ ] (multi-tenant?) flip **Settings → Access → Multi-tenant access** ON
- [ ] Watch first hour: `/api/dashboard/vitals`, `/api/analytics/perf`, `/api/ops`

---

## 9. Quick reference

| Want to… | Command |
|---|---|
| Deploy new code | `docker compose -f docker-compose.prod.yml up -d --build --force-recreate app worker` |
| Add capacity | `… up -d --scale app=4` |
| Stop (keep data) | `… down` |
| Back up | `./scripts/backup.sh` |
| Plan infra for N users | `python3 scripts/scale_plan.py --users N` |
| Verify everything | `python3 scripts/smoke.py` |
| **Never (data loss)** | `… down -v` · `volume rm …_docsensei_pg` · `rm -rf ./data` |

Deeper detail: `docs/SCALING.md` (per-bucket sizing), `docs/SMOKE_TEST.md`
(verification), `docs/PRODUCTION.md` (prod hardening).
