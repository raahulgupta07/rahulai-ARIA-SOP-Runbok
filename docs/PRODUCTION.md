# Production checklist — City Agent Aria

Aria is pilot/internal-ready out of the box. Before exposing it to real users
and real data, work through this list. Items are ordered by importance.

## 1. Environment & secrets (hard gate)
- [ ] `APP_ENV=production` — this turns on `config.check_prod_config()`, which
      **fails boot** if the JWT secret is weak/default or the LLM key is missing,
      and enables HSTS. Do NOT run prod with `APP_ENV=dev`.
- [ ] `JWT_SECRET` — 64-char random (already set). Rotating it logs everyone out.
- [ ] `OPENROUTER_API_KEY` — real key, billing alerts on the OpenRouter account.
- [ ] `PUBLIC_URL` — your real https URL (used in Teams cards, share links, embed).
- [ ] `CORS_ALLOW_ORIGINS` — only your domains (empty = same-origin; never `*`).
- [ ] Remove/rotate the dev accounts (`admin@city.com`, `staff@city.com`). Bootstrap
      a real admin via `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`.
- [ ] `.env` is gitignored — confirm it never gets committed (`git ls-files | grep .env`).

## 2. Data safety
- [ ] Schedule backups: `scripts/backup.sh` via cron (it pg_dumps ALL tables +
      tars page images, keeps last `BACKUP_KEEP`). Set `BACKUP_S3_BUCKET` for
      off-box copies. Test a restore with `scripts/restore.sh`.
- [ ] Persist the Postgres volume (`docsensei_pg`) and `./data` on durable disk.
- [ ] There is NO undo on the admin "wipe" — gate that screen / keep backups.

## 3. Scale & cost
- [ ] Run the **dedicated ingest worker**: set `INGEST_IN_API=0`, then
      `docker compose --profile worker up -d app worker`. Keeps vision/PageIndex
      off the API GIL. Tune `WORKER_VISION_WORKERS`.
- [ ] `WEB_CONCURRENCY` ≥ 2; ensure `WEB_CONCURRENCY*DB_POOL_MAX (+ worker) <
      PG max_connections` (default 200).
- [ ] Cost awareness: each ingested doc runs many LLM calls — vision (1/page) +
      wiki compile (1/page) + ~7 enrichers + PageIndex. For a large corpus this is
      real spend. To cut cost, disable optional compilers via flags (e.g.
      `TROUBLESHOOT_TREE=0`, `ENTITY_INDEX=0`, `AUTO_QA_ENABLED=0`) — chat still works.
- [ ] `LLM_MAX_CONCURRENCY` bounds user-facing answer concurrency (default 15);
      raise per OpenRouter rate limits, not the box.
- [ ] Q&A cache (`AUTO_QA_SERVE_ENABLED=1`) keeps repeat questions ~zero-cost.

## 4. Observability & ops
- [ ] `ALERT_WEBHOOK_URL` — wire warn/error alerts to Slack/Teams.
- [ ] `USAGE_ROLLUP_ENABLED=1` (on) for analytics; `VERIFY_ENABLED` optional (costs LLM).
- [ ] `EMBED_PRUNE_ENABLED=1` if you use the embed widget (prunes anon visitors).
- [ ] Watch the Operations Cockpit (Dashboard) + `/api/ops` for ingest/daemon health.

## 5. Validate before go-live
- [ ] Smoke-test ingest on a representative sample of YOUR docs (mixed types), not
      just SOPs — confirm playbook/entities/lint/tree produce sane output and chat
      answers cite the right pages.
- [ ] Load-test `/api/ask` at expected concurrency (prior runs: 100 conc, 0 fail).
- [ ] Verify auth method end-to-end against your real IdP (LDAP/OIDC/Entra). For
      Azure, patch `oidc.py` email fallback (`preferred_username`/`upn`).

## 6. Deploy target
- [ ] AWS (port 8001) is currently many releases behind local. Bring it up to the
      current image + run the migrations (they run on boot, idempotent).
