# Upgrading an existing Aria install to v2.25.0

Data-safe: the Postgres volume and `./data` page images persist; the schema
auto-migrates on boot (idempotent). `update.sh` takes a pg_dump backup first.

## 1. Upgrade

```bash
cd /path/to/rahulai-ARIA-SOP-Runbok      # the install directory
sudo bash update.sh                       # backup → git pull → build → recreate app+worker
```

First boot takes ~1 extra minute: Postgres builds two new search-index
columns (`pages.tsv_en`, `nodes.tsv_en` — English word-stem matching).

## 2. One-time post-steps

```bash
# Seed casual/broken-English question variants for THIS corpus (1 LLM call/doc)
docker compose --env-file .env.prod exec -T app python scripts/seed_casual_qa.py

# Verify the running version
docker compose --env-file .env.prod exec -T app \
  python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8077/api/version').read()[:60])"
```

## 3. Nginx Proxy Manager check (fixes blank-screen-while-waiting)

NPM admin (`http://SERVER_IP:81`) → your proxy host → **Advanced** tab must contain:

```
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
client_max_body_size 512m;
```

Websockets ON. Without `proxy_buffering off` the answer stream buffers and
users stare at a blank page until the whole answer finishes.

## 4. Tell users

Hard-refresh once (Ctrl/Cmd+Shift+R) to drop the cached app shell.

## What this upgrade delivers (2.21.0 → 2.25.0)

- Casual / broken-English / typo'd questions answered (word-stem index +
  runbook-router recovery); off-topic still declined
- Topics buried inside a runbook found (exact-phrase rescue)
- Instant "Working…" thinking trace — no blank wait
- Follow-up suggestion chips stay on the asked topic
- Casual-phrasing Q&A bank (instant cached answers)
- New **Insights** tab (admins): hours saved, labour value vs AI cost (ROI),
  resolved/deflection, DAU/WAU/MAU, usage by domain and department
  (departments auto-fill from the AD `department` attribute at LDAP login),
  token/model usage, per-user productivity + Excel export
- Deep-mode answers now meter real tokens/cost

## Rollback

```bash
# update.sh printed the backup path; then:
git checkout <previous-tag-or-sha>
docker compose --env-file .env.prod build app && \
docker compose --env-file .env.prod up -d --force-recreate app worker
```
