# City Agent Aria

**Agent for Runbooks & IT Assistance** (codename DocSensei). Bilingual (English +
Burmese) AI agent for company SOPs, runbooks & policies. Reads document pages **as
images** (incl. scanned / Burmese / screenshot-heavy), answers in the **user's
language**, **streams** the reply with a live **thinking trace**, and **cites the
source page** (click a `[p.N]` citation to open it). Vectorless RAG via
[PageIndex](https://github.com/VectifyAI/PageIndex) + Postgres full-text — no
embeddings, no separate OCR. **Self-learning**: drop in files, teach facts — or
just *tell* Aria something mid-chat ("remember…", "correction…") and she saves it
herself and recalls it forever (taught facts even override the docs).

**Auto-growing Q&A bank**: beyond facts, Aria builds a reviewable bank of
question/answer pairs — mined from each document at ingest (page-cited), harvested
free from chat answers you **upvote**, and (optional daemon) synthesized to fill the
topics users ask about but the docs answer poorly. Approve the good ones in
**Workspace → Q&A**; once approved, a repeat question is **served straight from the
bank** — instant, zero-agent, zero-cost.

Packaged OpenWebUI-style: one image serves the API + the SvelteKit SPA on a
single port, plus Postgres. Multi-method login, per-user chat history, AI
follow-up questions, stop/copy/feedback. (Model labelled "ARIA 1.0" in the UI;
backend uses Gemini via OpenRouter.)

## Stack
- **FastAPI** + **Python 3.12**, **Postgres 17**
- **PageIndex** (vendored, vectorless reasoning tree) + Postgres FTS/pg_trgm
- **Agno** + **Gemini flash-lite** via OpenRouter (one key: ingest + chat + vision + titles)
- **pymupdf** render + vision pre-read (screenshot text transcribed at ingest)
- **Auth**: local (bcrypt) + LDAP/AD (ldap3) + SSO/OIDC (JWKS), merged by email, JWT
- **Roles**: one **admin owns the knowledge base** (upload / edit / approve / delete docs, facts, Q&A; S3 / SharePoint import). Everyone else is **chat-only** — Workspace / Brain / Settings are hidden and every knowledge-management API returns `403`. Answers, citations, follow-ups and 👍/👎 stay available to all.
- **Frontend**: SvelteKit5 SPA, streaming NDJSON, Claude-style UI; dashboards use **ECharts** (animated gradient charts, light theme) via a shared `lib/EChart.svelte` + `lib/charts.ts`

## Run (Docker — one command)
```bash
# 1. put your OpenRouter key in .env
echo "OPENROUTER_API_KEY=sk-or-..." >> .env

# 2. build (stamps version) + start
./release.sh
docker compose up -d --force-recreate app
```
- App → **http://localhost:8082** · Postgres on host 5437.
- First sign-up becomes **admin**. (No real SSO/LDAP backend needed for local — local email/password works out of the box.)

To cut a release: bump `VERSION` + add a `CHANGELOG` entry in `app/version.py`, then `./release.sh`.

### Scale: dedicated ingest worker (optional)
Document ingest is LLM-heavy (per-page vision + PageIndex + compilers). To keep the
API snappy under load, run it in a **separate worker container** so that work never
shares the API's GIL:
```bash
# in .env:
INGEST_IN_API=0
# then start API + worker:
docker compose --profile worker up -d app worker
```
Default (`docker compose up -d`) runs everything in one container — fine for small
deployments. Background daemons are leader-locked, so exactly one process ingests.

**Going to production?** Work through `docs/PRODUCTION.md` (env/secrets, backups via
`scripts/backup.sh`, cost guardrails, validation).

### Local dev (no Docker)
```bash
docker compose up -d db
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/uvicorn app.main:app --port 8077         # http://127.0.0.1:8077
```

## Load your SOPs
Three ways, all feed the same async worker:
- **In-app `+ Add ▾` menu** (Workspace top bar, OpenWebUI-style dropdown): **Upload files** (one or many) · **Upload directory** (whole folder, recurses) · **Import from server** (admin — scans the mounted `./documents` folder and queues every new file, dedup by name) · **Import from S3** (admin, when configured) · **Import from SharePoint / Import from OneDrive** (admin — pulls every PDF/image from a Microsoft 365 document library or a user's OneDrive via Microsoft Graph).
- **Microsoft 365 (SharePoint + OneDrive)** — fully UI-configured, no env vars needed. Connect once in **Settings → Integrations → Microsoft 365** (tenant ID, client ID, client secret, enable SharePoint / OneDrive, Test credentials); one app-only Azure app serves both sources (`Sites.Read.All` + `Files.Read.All` app permissions + admin consent). Then pick what to import in **Add → Import from SharePoint / OneDrive**: the library site/path or a user's UPN, optional folder, and a per-source auto-sync toggle + interval (hours) that keeps pulling new files on a schedule. The secret is stored server-side and never shown back (env `GRAPH_CLIENT_SECRET` works as a fallback).
- **Drop into the server folder**: copy SOPs into host `./documents` (mounted to `/app/documents`, env `IMPORT_DIR`) → `+ Add ▾ → Import from server`. Or drop straight into `data/inbox/` (a watcher rows them automatically).
- **CLI**: `.venv/bin/python scripts/bulk_upload.py /path/to/sop_folder` (copies into the inbox).

Watch progress in the right-side **Jobs drawer** (auto-opens on upload; reopen via the `⟳ Jobs` pill): an overall bar + stage lane counts (Render/Read/Structure/Compile/Tag/Queued/Ready/Failed), the actively-processing docs with a 7-stage stepper + live log + cancel, the queue (searchable), and failed/done — scales cleanly to hundreds of files. The document table itself shows newest/in-flight first with `Uploaded · Updated · By` columns.

### Async ingest (how it works)
Upload is **non-blocking**: `POST /upload` saves the file to the inbox, inserts a
`queued` document row, and returns instantly. A daemon **worker** (in the app
process) claims queued docs with `FOR UPDATE SKIP LOCKED`, renders + vision-reads
+ trees them with **live progress**, then flips the row to `ready` (or `failed`).
The DB `docs` table *is* the queue — no Redis, one image.
- **Crash-safe:** docs left `processing` are requeued on boot; reprocessing wipes
  old pages/nodes first (no dupes).
- **Gated:** a doc is invisible to answers until `status='ready'`.
- **Retry:** failed docs re-queue in one click (or `POST /documents/{id}/retry`).
- **Knobs (.env):** `INGEST_WORKERS` (parallel docs), `INGEST_WATCHER`,
  `WATCHER_POLL_SECONDS`, `VISION_TIMEOUT`, `VISION_WORKERS`, `STORAGE=local|s3`.
- **Compiled wiki (vision once, text forever):** at ingest each page's vision text is
  reformatted into **clean markdown once** (tables/steps/codes preserved) and stored,
  so chat reads pre-digested markdown and answers in full detail without re-scanning the
  file or needing a bigger model. Knobs: `COMPILE_ON_INGEST=1`, `CHAT_USE_WIKI=1`,
  `COMPILE_MODEL` (defaults to the chat model). Backfill existing docs:
  `PYTHONPATH=. .venv/bin/python scripts/compile_all.py`.

### Self-learning (agent writes back, no retraining)
Four ways the brain grows: upload docs (which now **auto-extract facts** — see
below), **Brain → Teach a fact**, **teach in chat**, or fix a 👎 with a
correction. The chat path is reactive + gated (Scout-style write-back, no ML): when
your message signals teaching — *remember…, note that…, from now on…,
correction…, the correct value is…* (English or မြန်မာ) — a cheap post-turn pass
extracts the durable fact and saves it (`source='chat'`). Normal questions store
nothing, so there's no cost or noise on ordinary turns.
- Learned facts get a **🤖 "learned in chat"** badge in Brain → Facts (vs 💡 for
  hand-taught), tagged with who taught them.
- A **"💡 Learned something new — saved for review"** step shows in the trace.
- **Review gate (Intern Rule):** facts Aria learns in chat land **pending** and
  *don't* affect answers until you **Approve** them in Brain → Facts (one click;
  or Reject). Facts you teach by hand are active immediately.
- **Active facts override the documents on conflict** — so an approved correction
  actually sticks (Aria answers the fact and notes the SOP is outdated). Wrong
  fact? delete it in Brain → Facts.
- **Freshness:** each fact tracks how often it's actually used; the Coverage
  Audit flags ones never used in 14+ days so you can retire them.
- **AI vs Manual:** every fact carries an origin badge — **AI** (pulled from a
  document or learned in chat) or **Manual** (you taught it) — plus a
  **confidence score** chip (green / amber / red). Filter the Brain fact feed by
  **All · AI · Manual · Pending**.

### Auto-facts (documents teach themselves)
When a document finishes ingesting, Aria does **one extra pass over its compiled
markdown** and pulls up to a handful of **durable facts** (each with its own
confidence), saving them as `source='doc'` facts. So the brain grows the moment
you upload, not just when someone asks. Re-uploading a doc won't pile up dupes
(facts are fuzzy-deduped). Knobs: `AUTO_FACTS_ENABLED=1`, `AUTO_FACTS_MAX` (=6).
Whether an auto-fact goes live immediately or waits for review is decided by the
**governance policy** below.

### Knowledge quality pipeline (capture once, answer like a guide, connect docs)
On top of the compiled wiki, ingest now builds a layered, user-facing knowledge base —
all flag-gated, fail-soft, made **once** at upload so chat never re-reads the file:
- **Playbooks** — each doc is reformatted into a follow-along guide (Goal · Who ·
  Prerequisites · Steps · Verify · If-it-fails · Escalate). Chat answers FROM it, and
  the Brain reader has a **Playbook** view + a **Guide me** step-by-step walker.
  (`COMPILE_PLAYBOOK`, `USE_PLAYBOOK_IN_CHAT`)
- **Doc types** — each doc is classified sop / runbook / policy / reference and routed
  to the right compiler (reference → a term→value **Lookup** table). (`TYPE_AWARE_COMPILE`)
- **Entity index** — recurring things (systems, menus, codes, screens, fields) become
  cross-document links ("Gold Central → appears in 6 SOPs"); a question naming an entity
  pulls pages from every doc that shares it. Brain **Entities** tab. (`ENTITY_INDEX`,
  `ENTITY_RETRIEVE`)
- **Conflicts + freshness** — a deterministic lint flags the same term defined two ways
  across docs, and stale documents by date, on the **Audit** page. (`KB_LINT`,
  `DOC_STALE_DAYS`)
- **Procedure chaining** — Aria detects cross-doc prerequisites and tells you what to
  complete first ("do the Night Batch SOP before the price change"). (`PROC_CHAINING`)
- **Troubleshooting trees** — branching ("if X → do Y") docs compile to an interactive
  **Troubleshoot** walker. (`TROUBLESHOOT_TREE`)
- **Role-based depth** — admins/IT get the full procedure; ordinary users get a plain,
  simplified answer. (`ROLE_DEPTH`)
- **Visual cues** — at ingest Aria also notes what a screenshot is telling you (which
  button is highlighted, what to click); shown as an "On this screen" panel. (`RENDER_DPI`)
- **Capabilities catalog + multi-doc synthesis** — an auto "what Aria can help with"
  map, and answers that **merge several SOPs into one procedure** when a question spans
  them. (`MULTIDOC_SYNTH`)

See `docs/PLAN_KNOWLEDGE.md` for the full phased plan.

### Fact Approval (governance — choose what goes live automatically)
**Settings → Fact Approval** lets an admin set, **per source**, whether new facts
go **Auto** (active immediately) or **Review** (held pending), plus a minimum
**confidence floor**. Recommended mix (the defaults):

| Source | Policy | Min confidence |
|---|---|---|
| **Human** (you taught it) | Auto | — |
| **Document** (auto-fact) | Auto | 0.90 |
| **Chat** (learned mid-conversation) | Review | 0.95 |
| **Feedback** (👎 correction) | Review | — |

The page shows a **per-source pending count** and a **bulk-approve** button (by
ids, by whole source, or everything). One function — `decide_status(source,
confidence, is_admin)` — drives every write path (auto-facts, chat learning, and
hand-taught facts) so the policy is honored everywhere.

### Coverage Audit + weekly digest (Aria grades herself)
**Brain → 📊 Audit** is a self-audit over data Aria already logs (no ML): a
score /100 across four pillars (Context, Coverage, Freshness, Signal), the
**highest-leverage gaps to fix first**, **blind spots** (questions answered with
no source page, ranked by how often they're asked — these need a doc or a fact),
stale facts, and recent 👎. Once a week Aria **posts a digest to the activity
feed on her own** ("Score X/100 · N answers · M had no source · K awaiting
review") — proactive, no prompt needed. Admins can trigger it now with **Run
now**. Knobs: `DIGEST_ENABLED`, `DIGEST_INTERVAL_DAYS`.

### Dream cycle (a self-improving brain)
**Workspace → Self-improvement.** While the system is idle, one background pass
**consolidates the brain on its own** so the next day's answers are better — no
human, no retraining, no embeddings. Each step reuses data Aria already has and
is fail-soft: it **dedups** near-duplicate facts, **promotes** aged high-
confidence facts to active, **retires** stale never-used ones, **resolves**
same-term/divergent-value conflicts, **scores salience** from real citation
usage (so the pages people actually rely on rank higher in retrieval), and
**auto-links entities across documents with zero LLM calls** (a denser knowledge
graph for free). Optionally it fills coverage gaps with grounded Q&A. Every run
is logged, and the **Self-improvement panel** shows the last run, a live
knowledge graph, metric tiles, recent runs, a **Run now** button, and **settings
you can flip live** (no redeploy). Knobs: `DREAM_ENABLED`, `DREAM_INTERVAL_H`,
`DREAM_STALE_DAYS`, `DREAM_PROMOTE_AGE_H`, `DREAM_AUTO_RESOLVE`, `DREAM_GAP_FILL`,
`DREAM_AUTOLINK`, `SALIENCE_BOOST`.

## Using it
A **full-width top bar** is the shell: the **brand logo** (left) · top nav **💬 Chat · ▦ Dashboard · 🧠 Brain · ⚙ Settings** (Chat first) · then the **notification bell** + your **account menu** (right) — identical on every page (activity + What's-new / version in the bell). On **Chat**, a **left rail** (warm sand) holds **＋ New chat** · 🔍 search · your **chat history** · connection status. On **Dashboard / Brain / Settings** that chat rail is replaced by the page's own layout (Brain → Brain/Graph/Audit + Documents/Facts; Settings → Overview/Users/Authentication). The selected nav item shows in a coral peach pill.

- **Dashboard** — a **role-aware section** with a sticky sub-nav; each item is its own **full-width page**, and a shared **time filter** (7d / 30d / 90d) reshapes every page. Regular users see **Overview** (their questions / chats / sourced % / helpful %, activity chart, the docs that answered them, recent questions, facts they taught — all scoped to *them*) and **My map** (their personal knowledge graph). **Admins** get the full org cockpit:
  - **Overview** — org KPIs (active users · questions · helpful · coverage ring) + volume & 👍/👎 trend.
  - **Exec** — the **management scorecard** with **▲▼ deltas vs the previous period**: Adoption (activation %, DAU/WAU/MAU, stickiness), Engagement (Qs/user, multi-turn %, retention), Quality (helpful / sourced / blind %), and **Value** — answered = deflected, **≈ hours saved** + **LLM $ cost** + monthly run-rate. Plus an **adoption funnel** (registered → activated → weekly → power users), an **8-week active-user trend**, an editable ROI model, and **Export / Print**.
  - **Users** — a **per-user monitor by id, no chat content**: a **segment bar** (active / dormant / inactive / never, click to filter), a 14-day **sparkline** per user, **flags** (🔥 power · ⚠ at-risk · 💤 dormant), role + auth filters, and **CSV export** — searchable, sortable, paginated for 10k+ users (nightly usage rollup). Click a row → a **profile drawer** (KPIs, activity, topic mix, docs relied on, facts taught, account info — never a question string).
  - **Knowledge** — health grid · most-cited docs · blind spots · stale facts.
  - **Graph** — a full-screen **living "deep-learning brain"**: glowing neurons, synapses firing along the edges, gentle breathing, and **newly-learned docs/facts flashing in real time** (a live "consolidating / learned" ticker; Pause toggle). Toggle **Knowledge** (whole brain) or **People** (who-knows-what). Click any node → jump to it in Brain.
  - **People** — top-user leaderboard. **Review** — an inline **work-queue**: audit score + pillars, **pending facts with Approve / Reject in place**, downvote corrections, blind-spot questions to document, and the latest digest.
  - **Topics** *(privacy-safe)* — keyword analysis over **all** chats that **never exposes a message**: chip-cloud, phrases, intent mix, rising terms, an **EN / မြန်မာ split**, and a **demand-vs-coverage** table that flags topics asked a lot but rarely sourced (what to document next). Aggregate counts only; anything mentioned fewer than twice is hidden.
- **Chat** — a Claude-style home: a **centered hero** (time-of-day greeting + your name) with the composer as the focal point, a **2×2 grid of starter cards** (category icon + question, one click sends) and a single tiny line of stats. The starter cards are **corpus-derived, not hardcoded** (`GET /api/suggestions`, zero LLM at request time): an LLM curation pass picks the most useful, distinct questions across the whole corpus, writes a clean English version **and a Burmese translation**, and stores them — so a one-tap **English / မြန်မာ toggle** switches the cards' language, each card shows its source document, and a click-learning **bandit** (Thompson sampling over impressions/clicks) surfaces popular cards more while still exploring new ones (a `POPULAR` badge marks the week's most-clicked). Falls back to live corpus logic on a fresh install. Ask in EN or မြန်မာ — Burmese renders via bundled **Padauk / Noto Sans Myanmar** webfonts (no fallback boxes); the answer streams with:
  - a **live thinking trace** (each step the agent takes — understanding → searching N runbooks → reading pages → composing),
  - a collapsing **"Thought for Ns"** reasoning trace and a blinking streaming caret,
  - **source coins** — compact numbered circles (`Doc·pN`); click one (or the `N sources →` pill) to open the **Sources drawer** with a **provenance mini-map** ("how this answer connects"), per-page summaries, and the full page images (with a Page / Text toggle + inline zoom),
  - **🔗 Related runbooks** chips — grounded in the knowledge graph (co-citation neighbors of the cited pages), click to ask,
  - a **⚠ thin-coverage** hint pointing at the nearest runbook when an answer couldn't be sourced,
  - a **transparency line** under each answer — **⚡ real token count**, **$ cost**, response time, and a **grounding badge** (`✓ N sources` / `⚠ unsourced`); plus a one-click **✦ check accuracy** that has an LLM judge re-read every cited page and report what share actually backs the answer (`✦ 92% accurate`),
  - **AI follow-up questions** to click next — **folded into the answer stream** (no extra LLM call), so the suggestion chips appear the instant the answer ends.
  - the **✦ check accuracy** judge is **on-demand** (click it) — not run automatically, to save a call per answer.
  - **Stop** mid-answer, **Copy**, and **👍/👎** feedback.
  - **Snappy first token** — pre-answer LLM hops are minimized (`AUTO_ROUTE_LLM=0` regex routing, `AGENTIC_MAX_SUBS=1`); query-expansion + relevance rerank stay on for quality.
  - an **animated logo** that comes alive (spin + glow) while the agent is working and settles when done.
  - **Conversation memory** — follow-ups like "give me more details" or "why is that needed?" continue the topic; Aria remembers the thread.
  - **Blind-spot self-correction** — Aria investigates before committing: if her first answer isn't grounded in a real source page, she **searches wider and tries again**; if she still can't find it, she says **"I couldn't find this in the runbooks — verify before relying on it"** instead of bluffing. Those moments are logged so admins see (and can close) the gaps on the dashboard.
  - **Quick** (default): fast, detailed, from the document text. **Deep**: also navigates page images for hard/ambiguous questions. Answers extract the **full** detail — every step, code, table and SQL, with all alternative methods listed — because each page is **compiled into clean markdown once at upload** (vision runs only at ingest, never per question), and retrieval (a) **expands your wording** (casual *"adj code"* → the doc's formal *"Adjustment Reason Code / 506"*, acronyms, screen/table names — so the right page is found), (b) **reranks** the candidates by which page actually answers you, and (c) **depth-fills** the most-relevant document so the actual procedure pages (not just title pages) reach the model.
- **Brain** — full-width, with its own **left sub-rail**: `Brain N · 🕸 Graph · Audit ⟨score⟩` on top, then a **LIBRARY** group (`All / 📄 Documents / 💡 Facts`), `⬆ Add` top-right of the content. Documents + Facts are **one unified knowledge feed** (Scout-style single brain): a search box over **everything Aria knows** — uploaded pages, their documents, and the facts you taught — shown as a **compact list by default** (toggle **≣ List / ▦ Cards**), each row/card typed (📄 doc / 📕 page / 🧠 fact, facts flagged `⚡ overrides docs`), with the pending-review strip and a **Teach a fact** button. Click any item → its reader / fact popup.
  - **📄 Docs** chip → a **document browser** (newest→old): **category chips** with live counts + a **date pill** row (`Today / 7d / 30d / Year / All`) + a sort select (`Newest · Oldest · Most pages · Name A–Z · Most used`) + **▦ Card / ≣ List** toggle. "All" shows collapsible category sections; pick a category for a flat grid. The list view is a dense `Title · Lang · Pages · Used · Accuracy` table.
  - **🕸 Graph** — an **Obsidian-style knowledge graph** of the whole brain (docs, pages, facts as nodes; *contains / reading-order / answered-together / related-docs / cited-by-fact* as edges). Nodes are **coloured by document** and sized by usage; labels appear on **hover / zoom**; typing in the search box **highlights** matching nodes. Seven views via the switcher — **Force · Circular · Clustered · 🌳 Tree · ☀️ Sunburst · ▦ Treemap · 🔀 Sankey**. Click a node → a **left detail panel** with its summary, stats, and a **"How this connects"** section that explains each relationship in plain English (clickable to hop to the linked node), plus *Open in reader* / *Open fact*.
  - **Documents** *(legacy view)* — switch between a **▦ Card grid** and a **≣ List** view (remembered). Each **card** has a **generated cover** (a soft gradient unique to the doc, tinted by language, leading with the **document title**) + a health dot, language, page count, and a 3-stat footer (`Process · Used · Accuracy`); the **list** is a dense `Title · Lang · Pages · Used · Accuracy` table. Filter by language/status, sort, and uploads stream in with a **live status badge** (⏳ queued · ⚙ pages-done bar · ✓ ready · ✗ failed + reason + ↻ retry).
  - **Quick look** (hover a card → 👁) opens a **side drawer**: flip pages, see insights (uploaded/ingested/last-answered/feedback/extraction + any weak pages/structure), jump the outline, then **Open full** or **Ask doc** — no page change.
  - **Open** a document → a **full-screen reader** (its own screen, refresh-safe URL): **Read** (the whole doc rendered as continuous page images, zoom + thumbnail rail + scroll-spy), **Outline** (PageIndex tree with summaries → jump), **Text** (readable full text — find, page rail, Clean/Text/Vision, `.txt`), **Split** (page image ‖ extracted text per page, with char counts). A right panel keeps insights + outline + Ask.
  - **Facts** = a summary strip (active / pending / chat-learned / hand-taught / unused) + a pending-review block (Approve/Reject) + **cover-cards that match the document cards** — gradient tinted by source (blue = learned-in-chat, amber = hand-taught), the fact key as the title, `⚡ overrides docs` tag, and a 3-stat footer (Used · Last · Status), sortable. **Click a card → a rich popup** (`/api/facts/{id}`) with the full fact, stats, **Where it came from** (the origin chat/feedback message), **Cited in answers** (recent questions that used it), and **Related** chips (linked docs/pages/facts — click to hop), plus **inline Edit** (key/value) and Approve/Reject/Delete (the grid stays behind).
  - **Audit** = a hero with a **score ring** + four **pillar bars** (Context/Coverage/Freshness/Signal) + a plain-English verdict, then KPI chips, the weekly digest, leverage gaps, blind spots, stale facts, and a **recent-activity** feed of clickable cards (repeated weekly-digest rows collapse into one; click a row for the full event + exact time).
- **Settings** — top pill tabs: **Overview** (account + 8 KPI tiles + system health + recent questions + knowledge snapshot), and for admins **Users** (KPI strip, filter, search, manage), **Fact Approval** (per-source Auto/Review toggle + confidence slider + pending counts + bulk-approve — the governance policy), and **Authentication** (top tabs — **Methods / LDAP / SSO** — for sign-in toggles + LDAP and OIDC config; works with Keycloak, Microsoft Entra/Azure AD, Google). All full-width.
- **Bell** — activity feed (ingests, sign-ups, digests…) + What's-new / version.
- **Activity robot** — a floating robot (bottom-right) that opens a live terminal-style **ingest log**: watch a freshly uploaded document get read page-by-page (`👁 reading page 4/22`), the tree build, the wiki compile, and `✓ ready` — plus learn/digest events stream in. (It's an *activity* log, not training — Aria reads & indexes, it doesn't train.)
- **Theme** — claude.ai warm **terracotta/coral** (`--clay #c2683f`) on ivory; one token drives the whole UI.

## API (under `/api`, JWT Bearer)
| Path | Purpose |
|---|---|
| `/api/auth/{config,signup,login,ldap,oidc/login,oidc/callback,me}` | auth |
| `/api/ask/stream` | streamed answer (NDJSON: `step`* → `token`* → `done`) `{q, conversation_id, mode}` |
| `/api/followups` · `/api/feedback` | AI follow-up Qs · 👍/👎 |
| `/api/suggestions` | corpus-derived home starter chips (zero LLM) — most-served Q&A → top doc sections → curated fallbacks |
| `/api/conversations[/{id}]` | per-user chat history (list/new/get/rename/delete) |
| `/api/upload` | queue a SOP for async ingest (returns `{doc_id, status:"queued"}` instantly; records uploader) |
| `/api/ingest/scan` (GET/POST) | server-folder bulk import (admin) — GET previews `{found,new,skipped}`, POST queues every new file from `IMPORT_DIR`, dedup by filename |
| `/api/documents[...]` · `/api/documents/{id}/retry` · `/api/documents/{id}/cancel` | list (with live `status`/`progress`/`uploaded_by`/`updated_at`) + manage · re-queue a failed doc · cancel an in-flight doc |
| `/api/memory` · `/api/memory/{id}` (PATCH/DELETE) · `/api/memory/{id}/approve\|reject` | teach / list facts (`{memory, pending}`, `?status=`) · edit key+value / delete · approve/reject a pending learned fact |
| `/api/governance` (GET/POST) · `/api/memory/approve-bulk` | read / save the **fact-approval policy** (per-source auto-vs-review + confidence floor, + pending counts) (admin) · bulk-approve pending facts (`{ids}` \| `{source}` \| all) (admin) |
| `/api/facts/{id}` | rich fact detail for the popup — `{…, origin, cited_in[], related[]}` (provenance + citation log + linked nodes) |
| `/api/brain/search?q=&type=all\|doc\|fact` | **unified brain** — one ranked feed over pages + active facts + doc-summaries (typed `items[]`) |
| `/api/brain/graph` · `/api/brain/node?id=` | knowledge graph (`{nodes,links}`, kinds contains/cocite/sequence/similar/cite) · node detail + explained `relations[]` |
| `/api/answer/sources?ids=` | Perplexity-style sources for an answer — grouped by doc with `doc_wiki.summary` + page snippets |
| `/api/audit/coverage` · `/api/digest/latest` · `/api/digest/run` | self-audit (blind spots, gaps, stale facts) · latest weekly digest · trigger digest (admin) |
| `/api/dashboard/me?days=` · `/api/dashboard/admin?days=` | personal dashboard (caller-scoped) · org cockpit (admin) — KPIs/charts/leaderboard/health/review |
| `/api/dashboard/graph/me` · `/api/dashboard/graph/people` | personal ego knowledge map · org users↔docs *who-knows-what* bipartite (admin) |
| `/api/dashboard/keywords?days=` | **privacy-safe** keyword/topic analysis — aggregate only, no raw chat text, rare terms hidden (admin) |
| `/api/analytics/management?days=` | executive usage scorecard — adoption/engagement/quality/value (hours saved + LLM $) (admin) |
| `/api/analytics/users` · `/api/analytics/users/{id}` | per-user monitor (paginated/sortable) · single-user profile — **by id, no chat text** (admin) |
| `/api/analytics/config` · `/api/analytics/rollup/run` · `/api/analytics/users/export` | ROI knobs · rebuild rollup · CSV export of the user monitor (admin) |
| `/api/answer/related?ids=` · `/api/answer/graph?ids=` | graph-grounded related runbooks (co-citation) · provenance subgraph for an answer's cited pages |
| `/api/analytics/perf?days=` | performance scorecard — p50/p95 latency, **real token spend**, retrieval hit-rate + wider-retry rate, model split, slowest answers (admin) |
| `/api/analytics/docs?days=` | per-document scorecard — cites, helpful%, votes, age, indexed%, plus **orphan** (never cited) + **cold** (going stale) detection (admin) |
| `/api/analytics/verify?days=` · `/api/analytics/verify/run?limit=` | citation-accuracy report (avg trust score, support/refute rate, weakest answers) · run the LLM verify pass on demand (admin) |
| `/api/admin/audit?days=&action=` | governance trail — who approved/rejected/edited/deleted facts, deleted/retried docs, changed config (admin) |
| `/api/dashboard/cockpit?days=` | Overview command-center rollup — needs-attention alerts, health rings (coverage/trust/helpful/ingest), KPIs, trends (admin) |
| `/api/answer/verify/{message_id}` | on-demand citation-accuracy check for one chat answer — LLM judges whether each cited page backs the answer, returns a trust score % (any signed-in user) |
| `/api/ops` | system health — ingest fail-rate + stuck docs, daemon liveness, DB growth (admin) |
| `/api/analytics/engagement?days=` | session depth, follow-up rate, **D1/D7/D30 retention**, dormant users (admin) |
| `/api/analytics/learning?days=` | self-learning funnel (extracted→pending→approved→cited), reject-rate, fact-usefulness leaderboard (admin) |
| `/api/analytics/security?days=` | auth events — failed logins, repeat-offenders, role changes, last-login per user (admin) |
| `/api/analytics/corpus` | corpus hygiene — conflicting (same key, two values) + duplicate facts (admin) |
| `/api/feedback/corrections` | downvotes that came with a proposed correction — the review work-queue (admin) |
| `/api/analytics/learning-overview?days=` | self-learning overview — learned-over-time, knowledge growth, confidence buckets, most-cited (admin) |
| `/api/ops/ask` | ask the **ops agent** a plain-English question about the system (answers from a fail-soft snapshot of health/corpus/learning/coverage) (admin) |
| `/api/messages/{id}/share` · `/api/messages/{id}/unshare` · `/api/share/{token}` | create / revoke a share link for one answer · open a shared answer (members-only by default; `SHARE_PUBLIC_ENABLED` opens it) |
| `/api/teams/messages` · `/api/teams/config` · `/api/teams/manifest[.zip]` | Microsoft Teams bot webhook · bot config (admin) · download the Teams app manifest / package (admin) |
| `/api/pages/{id}` · `/api/stats/public` | source page PNG · login corpus stats (both public) |
| `/api/ingest/log?after=&limit=` · `/api/ingest/active` | live per-step ingest log (cursor poll, for the activity robot) · current processing doc + progress |
| `/api/notifications[...]` · `/api/version` | activity feed · version (public) |
| `/api/admin/{users,auth-config}` | admin (role-gated) |
| `/api/embed/session` | **public, Origin-gated** — widget exchanges its `pk_live_…` for a 30-min anonymous visitor token |
| `/api/embed/token` | server-to-server — exchange `sk_live_…` (Bearer) for a visitor token bound to your user id (SSO passthrough) |
| `/api/embed/keys[...]` · `/api/embed/keys/{id}/rotate` · `/api/embed/keys/{id}/sandbox-token` | manage embed keys · rotate a secret · mint a live test token (admin) |
| `/api/embed/brand` · `/api/embed/stats?days=` | default widget appearance (GET/PUT) · per-widget usage (visitors/sessions/messages/blocked) (admin) |
| `/api/okf/preview` · `/api/okf/export` | preview the export bundle · download the whole brain as an **Open Knowledge Format** `.tar.gz` (admin) |
| `/api/okf/import` | import an OKF `.tar.gz` (`?dry_run=true` previews) — markdown straight in, no vision, facts land pending (admin) |

## Embedding (drop the agent on any site)
1000+ people reach the same brain two ways: **member login** (JWT, unchanged) or an **embeddable widget**. An admin opens **Settings → Embed Widget**, sets the brand, creates a key, copies one line:
```html
<script src="https://YOUR-HOST/widget.js" data-key="pk_live_xxx" async></script>
```
- **Public key** (`pk_live_…`) ships in the snippet; the browser trades it for a short-lived visitor token. Choose **🔌 Any website (plug & play)** — an empty origin allow-list works on any domain (cost stays bounded by the per-key rate limit + daily message/cost caps) — or **🔒 Lock to my sites** to gate by Origin. (That 403 "origin not in allowlist" is the app's per-key Origin gate, not CORS.) Anonymous visitors, no login.
- **Secret key** (`sk_live_…`, shown once) is server-side only — exchange it for an identity-bound token to pass your own logged-in users through (SSO).
- The **Embed hub** has top pill tabs: Overview · Widgets · **Theme** (brand + live preview) · **Sandbox** (single-screen: generate a token + chat in a live iframe, snippet/curl alongside) · Monitoring (ECharts) · Developer. Full dev handoff in [`EMBED.md`](EMBED.md).

## Knowledge portability — Open Knowledge Format (OKF)
No vendor lock-in. **Settings → Export (OKF)** downloads the entire brain as an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) v0.1 bundle — a `.tar.gz` of plain markdown + YAML frontmatter that opens in any editor, renders on GitHub, version-controls in git, and re-imports anywhere.
```
aria-okf/
├── index.md            # bundle root (okf_version: 0.1)
├── documents/          # one .md per SOP — frontmatter (type/title/resource/tags) + compiled tables
├── facts/              # active facts, each with a # Citations block
├── log.md              # ingest history, date-grouped
└── viz.html            # self-contained knowledge-graph viewer
```
Aria's compiled wiki already *is* OKF-shaped, so export is a read-only walk over existing data — nothing new computed, nothing written.

**Import** goes the other way: drop an OKF bundle (from another Aria, an Obsidian vault, any OKF producer) and it loads straight in — the markdown body **is** the compiled wiki, so there's **no vision pass and no LLM cost**. Documents are searchable instantly; imported facts land in review (pending) so they never silently override your own docs. `?dry_run=true` previews the counts before committing.

## Run Aria inside Microsoft Teams
Aria can answer **in a Teams 1:1 chat or @mentioned in a channel** via an Azure Bot (Bot Framework). **Settings → Microsoft Teams**: fill the Bot App ID / secret / public URL, copy the messaging endpoint into your Azure Bot, then **download the app package (.zip)** and upload it in Teams → Apps → Manage your apps → Upload a custom app. The page has an 8-step Azure walkthrough. Replies come back as **Adaptive Cards with source buttons** that open the cited page. (Inbound requests are JWT-verified against the Bot Framework; a dev-only "skip auth" toggle exists for dev-tunnel testing.)

## Share a reply
Any answer has a **Share** button → a stable link to a read-only view of that reply (`/s/<token>`). Members-only by default; flip `SHARE_PUBLIC_ENABLED` to allow anonymous opens. Revoke any time.

## Auto mode + agentic retrieval + the ops agent
- **Auto · Quick · Deep** — the chat mode toggle defaults to **Auto**: Aria picks Quick (fast text answer) or Deep (navigates page images) per question from the wording/length, with an LLM tie-breaker. You can still force Quick or Deep.
- **Agentic retrieval** — hard questions are broken into sub-queries and the search is reformulated when the first pass comes back thin (part of the blind-spot self-correction loop).
- **Ops agent** — admins can ask the system about itself in plain English (`/api/ops/ask`); it answers from a live snapshot of health, corpus hygiene, learning funnel, coverage and growth.
- **Auto-learning (Layer A + B, on by default)** — beyond the gated "remember…" path, a post-turn pass quietly extracts durable facts from answers, **fuzzy-dedupes** them (vectorless pg_trgm similarity, so reworded repeats don't pile up), and high-confidence admin facts can auto-approve. Everything is visible in **Workspace → Learning** (learned-over-time, knowledge growth, confidence, most-cited) and reviewable in the queue.

## Why vectorless + vision pre-read
- **No embedding model** → removes the Burmese-retrieval weak link.
- **No OCR pack** → the vision model reads page images directly at ingest.
- Every page is **vision-transcribed** at ingest (screenshot UI labels, codes,
  tables) into `pages.text`, so the answer can quote exact detail — and it's all
  in fast indexed SQL, not a vector store.

## Notes / deploy
- **Version-controlled** since `5848264` (`main`). `.gitignore` excludes `.env`, `node_modules`, `.venv`, build output, and `data/*` runtime — never commit your `.env` (it holds the live OpenRouter key).
- **Enterprise login (LDAP + OIDC/SSO) is verified end-to-end** against real providers (OpenLDAP + Keycloak): bind-search-bind, JWKS signature verification, audience check, and tamper rejection all pass. Configure under Settings → Authentication.
- Set real `JWT_SECRET`, `PUBLIC_URL` (for SSO redirect), `CORS_ORIGINS` before a real deploy.
- The frontend is built **inside the image** — every deploy must rebuild: `docker compose up -d --build --force-recreate app`. `--build` alone can build the image but leave the old container running (new `app/*.py` won't be live); use `--force-recreate` and verify the container actually swapped.
- **SPA cache headers (fixed):** `index.html` is served `no-cache` and the hashed `/_app/*` assets `immutable`, so a new deploy reaches the browser on the next navigation without a manual hard-refresh. (Before this, the shell was heuristically cached and could point at chunk hashes the new deploy deleted → a stuck/blank screen. If you still see a stale page once right after upgrading from an old build, hard-refresh `Cmd+Shift+R` a single time.)
- **Cache gotcha:** `compose up -d --build` can cache the `COPY app/` layer and ship a **new frontend with a stale backend** (a missing route then 404s and falls through to the SPA). If a just-added endpoint 404s, run `docker compose build app` first (this busts the layer — you'll see `COPY app/` run, not `CACHED`), then `up -d --force-recreate`, and confirm with `docker exec <app> grep <symbol> /app/app/routes.py`. Registry `EOF`/TLS timeouts mid-build are common — just retry `build` until it prints `Built`.
- **S3 / MinIO (optional):** files default to local disk (`./data`). Configure object storage two ways — (a) **super-admin UI: Settings → Storage** (backend, bucket, keys, endpoint, prefixes; Test connection + Save, live with no redeploy — DB config overrides env), or (b) env for headless deploys: `STORAGE=s3` + `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (real AWS needs nothing more; for MinIO add `S3_ENDPOINT_URL=http://minio:9000` + `S3_FORCE_PATH_STYLE=1`). Drop documents under the import prefix (default `inbox-drop/`) then **Workspace → Add ▾ → Import from S3**. Page images get a 1-day browser cache; flip the **presigned** toggle to have browsers pull images straight from S3 (app serves zero bytes). Our managed files live under `S3_PREFIX` (default `docsensei/`): `inbox/` → `processed/`|`failed/`, page images under `pages/doc_<id>/`. Bucket auto-created on boot. Note: a corpus ingested under `STORAGE=s3` has its page images in S3 only — keep that backend (or re-ingest) rather than flipping back to local.
- **Scaling (read-heavy / MS Teams):** sized for many concurrent readers, few writers.
  - **DB connection pool** — one shared `psycopg_pool` per process (`DB_POOL_MIN`/`DB_POOL_MAX`/`DB_POOL_TIMEOUT`, default 2/20/30) instead of a fresh connection per request, so a burst of concurrent reads no longer exhausts Postgres `max_connections`.
  - **Multiple uvicorn workers** — set `WEB_CONCURRENCY` (default 4). All background work (ingest worker, inbox watcher, daemons) is **leader-gated** via a Postgres advisory lock so exactly one worker runs it no matter how many you scale to. Raise db `max_connections` (compose `PG_MAX_CONNECTIONS`, default 200) so `WEB_CONCURRENCY × DB_POOL_MAX` fits with headroom.
  - **Q&A bank cache** — approved Q&A pairs are served with **zero LLM** on both the web chat **and the Microsoft Teams** path, so repeat SOP questions don't re-hit the model. Serving fires on first-turn questions (verified under load: cached questions stay ~2s even while cold questions saturate the model).
  - **Backpressure + retry** — `LLM_MAX_CONCURRENCY` (default 15) caps simultaneous user-facing LLM calls (excess queue) to avoid provider rate-limit storms; a transient provider error (5xx / 429 / connection / timeout) is retried once at stream setup; inbound Teams messages run on a bounded pool (`TEAMS_WORKERS`, default 8) instead of an unbounded thread-per-message.
  - **Measured at 100 concurrent users** (`/api/ask`, 60 cached + 40 cold-unique): **0 failures**, cached p50 ~2s, cold p50 15s / p95 30s, Postgres ~43/200 connections. The DB and worker layers are not the limit — the **LLM concurrency cap is the ceiling on cold questions**, so scale by raising it (bounded by your OpenRouter rate limit + cost) and keeping the Q&A cache warm.
  - **SSO across workers** — OIDC login state lives in Postgres, so authorize/callback can land on different workers without a spurious "invalid state".
- Knobs: `BLINDSPOT_LOOP=1` (self-correction loop on/off), `RETRIEVE_K` / `RETRIEVE_DEPTH` / `RETRIEVE_RERANK` (retrieval breadth), `COMPILE_ON_INGEST` / `CHAT_USE_WIKI` (compiled-markdown layer), `USAGE_ROLLUP_ENABLED=1` (nightly usage-analytics rollup daemon for 10k-scale; dashboards also refresh it on read), `WEB_CONCURRENCY` / `DB_POOL_MIN` / `DB_POOL_MAX` / `DB_POOL_TIMEOUT` / `PG_MAX_CONNECTIONS` (scaling), `LLM_MAX_CONCURRENCY` / `TEAMS_WORKERS` (concurrency caps), `AUTO_QA_SERVE_ENABLED` / `AUTO_QA_SERVE_MIN_SIM` (Q&A bank serve), `VALUE_MINUTES_SAVED` / `LLM_PRICE_PER_MTOK` (ROI model defaults).
- rtk shell hook fakes `curl`/`docker` output here — verify via `scripts/check.py` / `rtk proxy docker …`.
- See **CLAUDE.md** for architecture internals, landmines, and the full DB schema.
