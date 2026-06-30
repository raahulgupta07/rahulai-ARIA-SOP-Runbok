# Production Smoke Test — City Agent Aria

Run this against a **freshly-deployed prod instance** before opening it to users.
Two parts: an **automated runner** (`scripts/smoke.py`) that exercises every API
path, and a **manual checklist** for things a script can't verify (TLS, backups,
real ingest, integrations). End with the **go / no-go gate**.

---

## A. Automated runner — `scripts/smoke.py`

Hits every critical `/api` path end-to-end, prints PASS/FAIL, exits non-zero on any
critical failure (CI-gate friendly). Pure stdlib — works where curl is rewritten.
Read-mostly; the fact + embed key it creates, it deletes.

```bash
# staging / first boot (full, includes write checks)
SMOKE_URL=http://localhost:8082 \
SMOKE_ADMIN_EMAIL=admin@city.com SMOKE_ADMIN_PASSWORD=secret123 \
python3 scripts/smoke.py

# against a live prod URL — use --no-write so it never mutates real data
SMOKE_URL=https://aria.yourco.com \
SMOKE_ADMIN_EMAIL=admin@yourco.com SMOKE_ADMIN_PASSWORD=*** \
python3 scripts/smoke.py --no-write
```

### What it covers
| # | Phase | Checks |
|---|---|---|
| 1 | Boot / public | `/health`, `/version`, `/stats/public`, `/auth/config` |
| 2 | Security | `nosniff` header, HSTS (prod), bad-password→401, unauthed API→401 |
| 3 | Auth | admin login→token, `/auth/me` resolves role=admin |
| 4 | Corpus / retrieval | `/documents` paginated, doc detail+stats, full text, **public page image** |
| 5 | Brain | `/brain/search`, `/brain/graph` nodes+links, `/catalog` |
| 6 | **Chat pipeline** | `/ask/stream` full NDJSON (meta→token→done), grounding flag, token/cost telemetry, repeat-ask / cache |
| 7 | Self-learning | `/governance`, `/memory` (+pending), `/qa` bank; teach→confirm→**delete** a test fact |
| 8 | Dashboards / ops | cockpit, admin, vitals, perf, ops, audit, security, ingest-state |
| 9 | Embed widget | create key → `/embed/session` mints widget token → widget chat → **delete** key |

Exit code: `0` = GO, `1` = critical failure, `2` = couldn't authenticate.

---

## B. Manual checklist (script can't verify these)

### B1. Pre-flight config (before first boot)
- [ ] `.env.prod` filled: `OPENROUTER_API_KEY`, `JWT_SECRET` (≥32 random chars),
      `APP_ENV=production`, `PUBLIC_URL`, `SUPERADMIN_EMAIL/PASSWORD`, `POSTGRES_PASSWORD`.
- [ ] **Boot guard works**: with a weak `JWT_SECRET`, container must **refuse to start**
      (`config.check_prod_config`). Confirm the fail, then set a strong one.
- [ ] Concurrency block matches your user bucket (`python3 scripts/scale_plan.py --users N`).
- [ ] `git ls-files | grep -E '^\.env$'` returns nothing (secrets not committed).

### B2. Deploy / infra
- [ ] `docker compose -f docker-compose.prod.yml up -d --scale app=N` — all services healthy.
- [ ] **TLS**: site loads over `https://`, valid cert, HTTP→HTTPS redirect, HSTS header present.
- [ ] **Streaming not buffered**: open chat, confirm tokens appear incrementally (nginx
      `proxy_buffering off` on `/api/ask/stream`).
- [ ] **Leader lock**: with `--scale app>=2`, exactly ONE process logs
      "leader acquired"; others log "another process holds leadership". (`docker compose logs | grep leader`)
- [ ] **Pool math holds** under load: no "too many connections" in logs.
- [ ] Multi-host only: `STORAGE=s3` set; a page image opened from each replica loads.

### B3. Real ingest (do a true upload — script skips file upload)
- [ ] Upload one real SOP PDF in Brain → row goes `queued → processing → ready`.
- [ ] Progress bar climbs; Jobs drawer shows stages (Render→Read→…→Ready).
- [ ] Ask a question answerable ONLY by that doc → answer cites its page, image opens.
- [ ] Delete the test doc → pages/images gone.

### B4. Auth methods you actually use
- [ ] Local login + `/login/admin` break-glass works.
- [ ] Brute-force lockout: 5 wrong passwords → 429 "locked"; a good login resets it.
- [ ] LDAP (if used): a directory user logs in (test against your real AD).
- [ ] OIDC/SSO (if used): full redirect→callback→`#token=` flow against your IdP;
      `email_verified` honored; `PUBLIC_URL` redirect URI matches the app registration.
- [ ] Normal (non-admin) user: chat works, `/workspace|/settings|/brain` are blocked (403/redirect),
      KB-mutation endpoints return 403.

### B5. Integrations (only the ones you enable)
- [ ] Microsoft 365 / SharePoint / OneDrive: Test connection green, an import queues files.
- [ ] Teams: manifest.zip downloads; a message in Teams gets an Adaptive Card reply.
- [ ] S3/MinIO storage: Test connection green; ingest writes images to the bucket.
- [ ] Embed widget on a real customer page: snippet loads, origin allow-list enforced,
      rate + daily caps trip a 429 when exceeded.

### B6. Data safety / ops
- [ ] `scripts/backup.sh` runs clean and produces a restorable dump; **cron is scheduled**.
- [ ] Restore drill: load the backup into a scratch DB, confirm docs/facts present.
- [ ] Dev creds rotated: `admin@city.com/secret123`, `staff@city.com` removed or disabled.
- [ ] Alert webhook (optional): a warn/error notification reaches Slack/Discord.

### B7. Load / capacity (before high traffic)
- [ ] Run a concurrency burst (e.g. 50–100 parallel `/api/ask`) — 0 errors, p95 within budget.
      (Reference: documented 100-concurrent test → cached p50 ~2s, cold p50 15s/p95 30s, 0 fail.)
- [ ] Watch `/api/analytics/perf` during the burst: cache-hit-rate, cost, no 429 storm.
- [ ] Confirm OpenRouter rate tier supports your `LLM_MAX_CONCURRENCY`.

---

## C. Go / No-Go gate

**GO** only when ALL are true:
1. `scripts/smoke.py` (full, on staging) → **exit 0, no critical fails**.
2. `scripts/smoke.py --no-write` (against prod URL) → **exit 0**.
3. Manual B1 (config) + B2 (deploy/TLS/leader) + B3 (real ingest) → all checked.
4. Auth methods you use (B4) verified against real providers.
5. Backups run + cron scheduled + one restore drill passed (B6).
6. Load burst (B7) passed with 0 errors and acceptable p95.

**NO-GO triggers (hard stop):**
- Boot guard didn't fire on a weak secret (means `APP_ENV` not production).
- Any unauthed API call returns data (auth bypass).
- `/ask/stream` doesn't complete or returns ungrounded answers as if sourced.
- "too many connections" under load (pool math wrong).
- No working backup + cron.

---

## D. First-hour-live watch
Keep these open for the first hour after opening to users:
- **Cockpit / vitals** (`/api/dashboard/vitals`) — active-now, queue, cost today.
- **Performance** (`/api/analytics/perf`) — p95 latency, cache-hit-rate, 429s.
- **System** (`/api/ops`) — ingest stuck/failed, daemon liveness, DB growth.
- `docker compose -f docker-compose.prod.yml logs -f` — leader, worker, errors.
