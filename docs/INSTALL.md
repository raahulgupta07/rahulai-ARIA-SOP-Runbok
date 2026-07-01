# City Agent Aria — Install Guide

Step-by-step setup, from zero to a running instance. Two paths: **Quick (local /
pilot)** and **Production**. For architecture, scaling and data-safety see
`docs/DEPLOYMENT.md`.

---

## 0. Prerequisites

- **Docker** + Docker Compose (Docker Desktop on Mac/Windows, or Docker Engine on Linux)
- An **OpenRouter API key** — https://openrouter.ai (the app's only external dependency; powers chat + ingest + vision)
- ~2 GB free disk; that's it (no GPU, no local model)

Check Docker works:
```bash
docker version
docker compose version
```

---

## 1. Get the code
```bash
git clone https://github.com/raahulgupta07/rahulai-ARIA-SOP-Runbok.git aria
cd aria
```

---

## 2. Configure (`.env`)
The app reads secrets + flags from `.env` (gitignored — never commit it).
```bash
cp .env.example .env
```
Edit `.env` and set the **3 required** values:
```bash
OPENROUTER_API_KEY=sk-or-...                 # REQUIRED
JWT_SECRET=<paste 64 random chars>           # REQUIRED — `openssl rand -hex 32`
SUPERADMIN_EMAIL=admin@yourco.com            # the first admin account
SUPERADMIN_PASSWORD=<a strong password>
```
Everything else has sensible defaults. (Leave `APP_ENV=dev` for local; set
`production` for prod — see §5.)

---

## 3. Build + run
```bash
docker compose up -d --build
```
This starts two containers:
- `docsensei-db` — Postgres 17 (data volume `docsensei_pg`)
- `docsensei-app` — the API + web UI (4 uvicorn workers)

Wait ~15s for boot (schema auto-creates, super-admin is seeded from `.env`).

---

## 4. Open + log in
- App: **http://localhost:8082**
- Log in with the `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` you set.

Verify it's healthy:
```bash
curl -s http://localhost:8082/api/health        # {"status":"ok",...}
curl -s http://localhost:8082/api/version        # version number
```
(If `curl` is unavailable: `python3 scripts/check.py http://localhost:8082/api/health`.)

---

## 5. First use
1. Go to **Sources** (top nav) → **+ Add** → upload a PDF/image SOP.
2. The doc shows **queued → processing → ready_lite → ready** (answerable in seconds; deep enrichment finishes in the background).
3. Go to **Chat** → ask a question about it → you get an answer with cited source pages.
4. (Optional) full health sweep:
   ```bash
   SMOKE_URL=http://localhost:8082 SMOKE_ADMIN_EMAIL=admin@yourco.com \
   SMOKE_ADMIN_PASSWORD=*** python3 scripts/smoke.py
   ```

That's a working install.

---

## 6. Production install

Use the dedicated prod stack (`docker-compose.prod.yml`: nginx load balancer +
scalable app replicas + a dedicated worker).

```bash
cp .env.prod.example .env.prod
# Fill: OPENROUTER_API_KEY, JWT_SECRET (≥32 chars), APP_ENV=production,
#       PUBLIC_URL=https://aria.yourco.com, SUPERADMIN_*, POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --scale app=2
```

Before opening to users (full list in `docs/PRODUCTION.md` + `docs/DEPLOYMENT.md`):
- **TLS** — mount certs, enable `:443` in nginx/compose, set `PUBLIC_URL=https://…`
- **Backups** — schedule `scripts/backup.sh` in cron (or use RDS automated backups)
- **Rotate** the dev/seed credentials
- Pick your size: `python3 scripts/scale_plan.py --users <N> --concurrency 0.4`

> `APP_ENV=production` activates a boot guard that **refuses to start** with a
> weak/default `JWT_SECRET` — set a strong one.

---

## 7. Day-2 operations

| Task | Command |
|---|---|
| Update to new code | `git pull && docker compose up -d --build --force-recreate app` |
| View logs | `docker compose logs -f app` |
| Stop (keep data) | `docker compose down` |
| Back up | `./scripts/backup.sh` |
| Add capacity (prod) | `docker compose -f docker-compose.prod.yml up -d --scale app=4` |

**Your data is safe** across rebuilds/updates — it lives in Postgres + `./data`, not
in the container. **Never** run `docker compose down -v` (deletes the database) or
`rm -rf ./data` (deletes page images) unless you intend a full wipe.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Chat says "No LLM" / answers fail | `OPENROUTER_API_KEY` missing or wrong in `.env` → fix + `docker compose up -d --force-recreate app` |
| App won't start in production | weak `JWT_SECRET` with `APP_ENV=production` (boot guard) → set ≥32 random chars |
| Login fails | wrong `SUPERADMIN_*`; the values in `.env` are re-applied on every boot |
| Port 8082 already in use | edit the `ports:` mapping in `docker-compose.yml` |
| New UI/feature not showing | hard-refresh the browser (Cmd/Ctrl+Shift+R) — content-hashed assets |
| Doc stuck at `ready_lite` | it's answerable; background enrichment is finishing — not an error |

---

## Reference docs
- `docs/DEPLOYMENT.md` — architecture + scale-without-data-loss
- `docs/SCALING.md` / `docs/SCALING_100_1000.md` — sizing per user count
- `docs/SMOKE_TEST.md` — verification / go-no-go
- `docs/PRODUCTION.md` — production hardening checklist
- `EMBED.md` — embeddable chat widget
