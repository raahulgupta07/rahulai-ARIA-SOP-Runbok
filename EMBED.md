# City Agent Aria — Embedding & Multi-Tenant Access

Two ways 1000+ people reach the same agent brain:

| Path | Who | Auth |
|------|-----|------|
| **Member login** | Internal staff | email + password / LDAP / OIDC → JWT (7-day) |
| **Embed widget** | Anyone on a customer site | public embed key → 30-min visitor token |

Members are unchanged. This doc covers the **embed** path for the dev team.

---

## 1. Quick start — drop the widget on any site

An admin creates a key in **Settings → Embed Widget**, copies the snippet, dev pastes it before `</body>`:

```html
<script src="https://YOUR-HOST/widget.js" data-key="pk_live_xxxxx" async></script>
```

Optional attributes:

```html
<script src="https://YOUR-HOST/widget.js"
        data-key="pk_live_xxxxx"
        data-accent="#c2683f"
        data-title="Ask Aria"
        data-position="right"     <!-- right | left -->
        async></script>
```

That's it. A launcher bubble appears; clicking opens an isolated iframe (`/embed`) that
handles auth, streaming, citations — zero member login.

---

## 2. How it works (the two keys)

Every key is a **pair**:

- **`pk_live_…` public key** — safe to expose. Ships in the snippet. The browser calls
  `POST /api/embed/session {public_key}`; the server checks the request **Origin** against
  the key's allow-list and returns a short-lived **visitor JWT** (`role=widget`, 30 min).
- **`sk_live_…` secret key** — server-side only, shown **once** at creation. For
  identity passthrough (SSO): your backend calls `POST /api/embed/token` with the secret
  to mint a visitor token bound to *your* user id.

Both tokens are accepted by the chat endpoints via the `current_principal` guard. Each
visitor is backed by an isolated `users` row (`role='widget'`) so conversations &
analytics work per-visitor, never leaking across sites.

**Security model**
- Origin allow-list stops a stolen `pk_` being used off-domain (empty list = any origin, **dev only**).
- Per-visitor rate limit (default 30/min, configurable per key) → `429` when exceeded. Backed by a
  shared Postgres minute-bucket, so the cap holds across **multiple workers/replicas** (not per-process).
- Per-key **daily ceilings** — `daily_msg_cap` (messages/day) and `daily_cost_cap` (USD/day). Either hitting
  its limit returns `429` until midnight. Caps a hostile (but allowed-origin) caller's LLM spend. `0` = unlimited.
- Secret key is bcrypt-hashed at rest; rotate anytime (old secret dies instantly).
- Widget tokens expire in `EMBED_TOKEN_TTL_MIN` (default 30); the widget re-mints silently.
- Disable or delete a key to cut off an embed immediately.
- `APP_ENV=production` refuses to boot with a weak/default `JWT_SECRET` and emits HSTS on every response.

---

## 3. SSO / identity passthrough (optional, secret key)

If the customer site already authenticates its users and you want each one tracked as a
stable identity, do the exchange server-side and pass the token to the widget:

```js
// YOUR backend (never expose sk_ to the browser)
const r = await fetch("https://YOUR-HOST/api/embed/token", {
  method: "POST",
  headers: { "Content-Type": "application/json",
             "Authorization": "Bearer sk_live_xxxxx" },
  body: JSON.stringify({ visitor_id: user.id, name: user.name }),
});
const { token } = await r.json();   // 30-min visitor JWT, hand to your frontend
```

Then call the chat API directly with that token (see §4), or build your own UI.

---

## 4. API reference (all under `/api`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/embed/session` | none (Origin-gated) | pk_ → visitor token (anonymous) |
| POST | `/embed/token` | `Bearer sk_` | sk_ → visitor token (identified) |
| POST | `/ask/stream` | member **or** visitor JWT | streamed answer (NDJSON) |
| POST | `/followups` | member or visitor | suggested follow-up questions |
| POST | `/feedback` | member or visitor | 👍/👎 on an answer |
| GET | `/answer/sources` | member or visitor | citation drawer payload |
| GET | `/pages/{id}` | public | page image |
| GET | `/pages/{id}/md`, `/text` | member or visitor | citation text |
| GET/POST/PATCH/DELETE | `/embed/keys…` | **admin** | manage keys |

`/ask/stream` request body: `{ "q": str, "conversation_id": int?, "mode": "quick"|"deep" }`
Response = newline-delimited JSON events: `meta` → `step`* → `token`* → `done`.
`done` carries `{ message_id, tokens, cost, cited_n, grounded, pages[] }`.

Minimal raw call:

```js
const res = await fetch("https://YOUR-HOST/api/ask/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Authorization": `Bearer ${visitorToken}` },
  body: JSON.stringify({ q: "How do I reset a price?", mode: "quick" }),
});
const reader = res.body.getReader();
// parse NDJSON lines: JSON.parse(line); collect ev.v on type==="token"
```

---

## 5. Deploy

Single Docker image (`cityagentdocsensei-app`), host port **8081 → 8077**. Frontend builds
inside the Dockerfile (incl. `widget.js` + `/embed`).

```bash
docker compose build app          # rebuilds image (busts COPY cache)
docker compose up -d --force-recreate app
curl -s http://HOST:8081/api/health   # {"status":"ok"}
```

DB tables (`embed_keys`, `embed_visitors`, `embed_rate`, `embed_usage`) auto-create on boot via
`init_db()` — no manual migration.

### Env vars

| Var | Default | Notes |
|-----|---------|-------|
| `APP_ENV` | `dev` | set `production` → hard-fails boot on weak `JWT_SECRET`/missing LLM key, emits HSTS |
| `JWT_SECRET` | `dev-docsensei-change-me` | **set a strong ≥32-char secret in prod** — signs all tokens |
| `JWT_TTL_DAYS` | `7` | member login lifetime |
| `EMBED_TOKEN_TTL_MIN` | `30` | widget visitor-token lifetime |
| `CORS_ALLOW_ORIGINS` | _(empty)_ | comma-list of cross-origin browser callers. **Empty = same-origin only** (the widget iframe is same-origin, so leave blank unless a customer calls the API from their own page JS) |
| `OPENROUTER_API_KEY` | — | LLM key (gitignored `.env`) |
| `DATABASE_URL` | local PG17 | Postgres 17 |

### Scale notes (1000+ members)
- Stateless app → run N replicas behind a load balancer; tokens are self-contained JWTs.
- Rate limiter + daily caps are **Postgres-backed** → hard limits hold across all replicas (no Redis needed).
- Per-key `daily_msg_cap` / `daily_cost_cap` bound LLM spend even from an allowed origin.
- Page images are public by id; everything else needs a token.
- CORS defaults to **same-origin**; widen per-deployment via `CORS_ALLOW_ORIGINS`. Per-**site** access is
  gated by each key's Origin allow-list, not by global CORS.
- Terminate **TLS** at the proxy; with `APP_ENV=production` the app sends HSTS so browsers never downgrade.
- Anonymous visitors create one `users` row each (`role='widget'`). At very high churn, periodically prune
  stale `embed_visitors` + their `users`/`conversations` (no auto-prune yet — manual/cron job).

---

## 6. Smoke test (verified)

```
admin login                      → 200
create key (origins=[example.com]) → 200, pk_ + sk_ (once)
session wrong origin             → 403
session right origin             → 200 + token + greeting + ttl
ask/stream (visitor token)       → streams real answer, conv created, 12 pages cited
secret /embed/token              → 200, identity-bound token
rate cap 5/min                   → req6,7 = 429
no auth                          → 401
/embed page                      → 200
```
