"""Version + changelog, served publicly at /api/version.

Bump VERSION + prepend a CHANGELOG entry by hand before cutting a release
(see release.sh). BUILD_SHA / BUILD_DATE are stamped into the image at build
time (Dockerfile ARG/ENV) and read here from the environment.
"""
import os

VERSION = "2.20.0"
BUILD_SHA = os.getenv("BUILD_SHA", "dev")
BUILD_DATE = os.getenv("BUILD_DATE", "")

# newest first
CHANGELOG = [
    {
        "version": "2.20.0",
        "date": "2026-07-14",
        "title": "Folders, access groups, one-account users & rock-solid Keycloak SSO",
        "lines": [
            "Organise sources into nested folders with a colour and a real line-icon; rename, move, expand/collapse; 'Include subfolders' to browse a whole subtree",
            "Access groups (OpenWebUI-style): default Administrators & Users, grant tab access + Manage content / Teach knowledge per group — Settings stays super-admin only",
            "Every member auto-joins the Users group; new members added on first sign-in",
            "Users table shows ID and each person's login methods (local / LDAP / SSO), merged into one row per email",
            "Turn each login method on or off (email · LDAP · SSO) from Settings → Authentication — users only see what's enabled",
            "Admin sign-in stays reachable at /login/admin even with every method off, so SSO/LDAP trouble can't lock admins out",
            "Keycloak / OIDC sign-in fixed: accepts Keycloak's audience (aud=account, client in azp) and verifies the issuer",
            "SSO callback can no longer return 'Internal Server Error' — every failure is logged and shown on the login page with a clear reason",
            "SSO works behind a WAF/reverse-proxy: all identity-provider calls use a normal User-Agent (fixes the JWKS '403 Forbidden')",
            "Falls back to the /userinfo endpoint when the token omits email, so sign-in never fails on a missing email claim",
            "OIDC nonce replay-protection and a deterministic redirect URL — full parity with Open WebUI's SSO",
            "Merge-accounts-by-email toggle so one person is one account across LDAP and SSO",
        ],
    },
    {
        "version": "2.19.0",
        "date": "2026-06-19",
        "title": "Installable app, graph-walk retrieval, animated graph, fully-auto brain",
        "lines": [
            "Install Aria on your phone (PWA): Add to Home Screen, fullscreen, offline shell",
            "Graph-walk retrieval: answers now pull in linked/prerequisite docs FTS alone missed",
            "Knowledge Graph animates everywhere (firing-synapse) while keeping all 7 layouts",
            "Dream cycle can run fully hands-off: auto-resolve conflicts + auto-promote facts",
            "Mobile polish: one-row docs toolbar + calendar picker, Filters sheet, pull-to-refresh",
            "Cleaner chat-only shell for normal users (no empty nav)",
        ],
    },
    {
        "version": "2.18.1",
        "date": "2026-06-19",
        "title": "Mobile & tablet ready — bottom tab bar, header menu, responsive rails",
        "lines": [
            "Works on phone & iPad: rails slide in from one header hamburger (consistent everywhere)",
            "Native-style bottom tab bar: Chat · Workspace · Brain · Settings",
            "Wide tables scroll and document lists stack as cards on small screens",
            "Documents toolbar on one row + a calendar date-range picker",
            "Jobs drawer opens reliably and never hides the menu",
        ],
    },
    {
        "version": "2.18.0",
        "date": "2026-06-19",
        "title": "Dream cycle — a self-improving brain that consolidates itself nightly",
        "lines": [
            "Nightly dream cycle: dedups facts, promotes/retires, resolves conflicts while idle",
            "Salience scoring from citation usage now boosts what surfaces in answers",
            "Zero-LLM entity auto-linking grows the cross-doc knowledge graph for free",
            "New Workspace → Self-improvement panel: run now, live graph, metrics, settings",
            "Live config — flip dream-cycle behaviour without a redeploy",
        ],
    },
    {
        "version": "2.17.0",
        "date": "2026-06-17",
        "title": "Knowledge quality pipeline — playbooks, entities, chaining, trees, catalog",
        "lines": [
            "Playbooks: every doc compiled into a follow-along guide (goal/steps/verify); chat answers from it",
            "Guided step-by-step walker + role-based depth (full for IT, simplified for end users)",
            "Doc-type aware: classify sop/runbook/policy/reference; reference docs get a term->value lookup",
            "Cross-doc entity index: a question naming a system/code pulls pages from every doc that shares it",
            "Procedure chaining: Aria tells you the prerequisite SOPs to complete first (+ graph edge)",
            "Troubleshooting trees for branching docs, conflict + freshness lint on the Audit page",
            "Visual cues captured from screenshots (re-ingest at higher DPI) shown as 'On this screen'",
            "Capabilities catalog + multi-doc synthesis (merge several SOPs into one procedure)",
        ],
    },
    {
        "version": "2.16.0",
        "date": "2026-06-17",
        "title": "Operations Cockpit — real-time mission control",
        "lines": [
            "New admin Cockpit screen (Dashboard + Workspace rail): one live view of everything",
            "System vitals, throughput, cache-hit %, p50/p95 latency — refresh on the live heartbeat",
            "Ingest pipeline panel: in-flight docs with progress + click-to-expand live event log",
            "Live answers feed: recent questions with cache-hit, retrieval funnel and latency",
            "Retrieval funnel viz (scanned -> pool -> reranked -> cited) for the latest answer",
            "New endpoints: /api/ops/answers/recent and /api/documents/{id}/log; ingest_events table",
        ],
    },
    {
        "version": "2.15.0",
        "date": "2026-06-17",
        "title": "Contextual Retrieval — more accurate answers, still vectorless",
        "lines": [
            "Each page now gets a short AI-written context blurb at ingest (what doc/section it's from)",
            "Folded into full-text search so paraphrased / vague questions find the right page",
            "Measured: paraphrased-question recall 80% -> 100% on the live corpus",
            "Reranker upgraded: larger candidate pool + optional Cohere reranker (off by default)",
            "No vectors, no GPU — pure Postgres FTS, true to the vectorless design",
        ],
    },
    {
        "version": "2.14.2",
        "date": "2026-06-16",
        "title": "Fix: Documents show instantly in Workspace (no empty flash)",
        "lines": [
            "Landing on Workspace now shows your documents immediately, first time",
            "No more empty 'No documents yet' before the list appears",
            "Brain documents, facts and Q&A share one cache — loaded once, shown everywhere",
            "Cold reload paints the last-known list instantly, then refreshes in the background",
        ],
    },
    {
        "version": "2.14.1",
        "date": "2026-06-16",
        "title": "Fix: Documents grid could stay stuck on loading skeletons",
        "lines": [
            "The Brain → Documents grid now shows your documents even if the loading flag sticks",
            "Skeletons only show when nothing has loaded yet, never over loaded content",
        ],
    },
    {
        "version": "2.14.0",
        "date": "2026-06-16",
        "title": "Microsoft 365 setup moved to Settings; pick folder in Add",
        "lines": [
            "New Settings → Integrations → Microsoft 365: connect once (tenant, client, secret)",
            "Turn SharePoint and OneDrive on/off there, and test the credentials",
            "Add → Import from Microsoft 365 now just picks the folder and imports",
            "Import dialog guides you to Settings when Microsoft 365 isn't connected yet",
        ],
    },
    {
        "version": "2.13.0",
        "date": "2026-06-16",
        "title": "Set the auto-sync schedule from the UI too",
        "lines": [
            "Choose how often each source syncs (in hours) right in the dialog",
            "Every SharePoint/OneDrive setting is now configurable in the UI — no env vars",
            "Each source runs on its own schedule; changes take effect within a minute",
        ],
    },
    {
        "version": "2.12.0",
        "date": "2026-06-16",
        "title": "Configure SharePoint & OneDrive entirely in the UI",
        "lines": [
            "Admin sets the app client secret right in the dialog — no server access needed",
            "Per-source auto-sync toggle in the UI (no env var, no restart)",
            "Test, preview and import all from the same dialog",
            "Secret is stored securely and never shown back; one click to clear it",
        ],
    },
    {
        "version": "2.11.0",
        "date": "2026-06-16",
        "title": "Import from OneDrive too (same as SharePoint)",
        "lines": [
            "New OneDrive lane — pull every PDF/image from a user's OneDrive",
            "One 'Import from cloud' dialog with a SharePoint / OneDrive switch",
            "Both sources get the same features: test, preview, bulk import, auto-sync",
            "Auto-sync now keeps both SharePoint and OneDrive in sync on a schedule",
        ],
    },
    {
        "version": "2.10.1",
        "date": "2026-06-16",
        "title": "Fuller answers — complete step-by-step procedures",
        "lines": [
            "Answers now give the full numbered procedure with every code and value",
            "Cached answers are detailed too (no more one-line replies)",
            "Re-mined the whole corpus with the richer answer format",
        ],
    },
    {
        "version": "2.10.0",
        "date": "2026-06-16",
        "title": "Admin owns the knowledge base; everyone else just chats",
        "lines": [
            "Only admins can upload, edit, approve or delete knowledge (docs, facts, Q&A)",
            "Regular users get a clean chat-only experience — Workspace/Brain/Settings hidden",
            "All knowledge-management APIs now require admin (were open to any logged-in user)",
        ],
    },
    {
        "version": "2.9.0",
        "date": "2026-06-16",
        "title": "Cache-hit observability in the Performance panel",
        "lines": [
            "See how often answers are served instantly from cache vs the live model",
            "Performance panel now splits cached vs cold answer latency (p50/p95)",
            "Cache hits are now recorded in telemetry (were previously invisible)",
        ],
    },
    {
        "version": "2.8.0",
        "date": "2026-06-16",
        "title": "Faster, sturdier under heavy concurrent load",
        "lines": [
            "Higher answer throughput under load (LLM concurrency cap 6 -> 15)",
            "Automatic one-time retry on a transient LLM error — fewer failed answers",
            "Measured: 100 concurrent users, 0 failures, cold-answer tail cut ~in half",
        ],
    },
    {
        "version": "2.7.0",
        "date": "2026-06-16",
        "title": "SharePoint auto-sync",
        "lines": [
            "Optional background sync keeps pulling new SharePoint files on a schedule",
            "Off by default; enable with SHAREPOINT_SYNC_ENABLED, runs on the leader worker",
        ],
    },
    {
        "version": "2.6.0",
        "date": "2026-06-16",
        "title": "Import documents straight from SharePoint",
        "lines": [
            "New SharePoint lane: pull every PDF/image from a Microsoft 365 library",
            "Add menu -> Import from SharePoint: configure, test, and import in one place",
            "Files flow through the normal pipeline with duplicate-name skipping",
        ],
    },
    {
        "version": "2.5.0",
        "date": "2026-06-16",
        "title": "Rewrite Q&A in place + a more responsive app under load",
        "lines": [
            "Edit any Q&A pair right in Brain - rewrite weak answers instead of just hiding",
            "The app stays responsive while large documents are processing",
            "Burmese questions reliably get Burmese answers (verified end to end)",
        ],
    },
    {
        "version": "2.4.0",
        "date": "2026-06-16",
        "title": "Starter questions that learn from what you click",
        "lines": [
            "Home starter questions now adapt over time — popular ones surface more",
            "New questions still get a fair chance to be seen (explore vs exploit)",
            "A POPULAR badge marks the most-clicked question this week",
        ],
    },
    {
        "version": "2.3.1",
        "date": "2026-06-16",
        "title": "Better, bilingual follow-up questions",
        "lines": [
            "Follow-up suggestions are now complete questions in your language, not topics",
            "Instant cached answers now suggest follow-ups too (from the same document)",
        ],
    },
    {
        "version": "2.3.0",
        "date": "2026-06-16",
        "title": "Bilingual, LLM-curated home starter questions",
        "lines": [
            "Home starter questions now offer English and မြန်မာ — switch with one tap",
            "Questions are AI-curated from your real Q&A to span the whole corpus",
            "Each starter shows the document it comes from",
            "Chips refresh automatically when you add documents (and on demand)",
        ],
    },
    {
        "version": "2.2.0",
        "date": "2026-06-16",
        "title": "Fuller Q&A coverage & a self-correcting answer cache",
        "lines": [
            "Every document now has mined Q&A — better starter chips and instant answers",
            "A thumbs-down on a cached answer retires it automatically (out of serve)",
            "Removed a duplicate uploaded SOP so sources and chips no longer double",
        ],
    },
    {
        "version": "2.1.2",
        "date": "2026-06-16",
        "title": "Fix Brain knowledge feed stuck on loading",
        "lines": [
            "Agent Brain → Documents / Facts / Q&A no longer hangs on skeleton loaders",
            "Feed loads instantly on open and on every tab switch (no remount stall)",
        ],
    },
    {
        "version": "2.1.1",
        "date": "2026-06-16",
        "title": "Don't serve vague cached answers",
        "lines": [
            "The instant-answer cache no longer replies with vague escalation stubs",
            "Questions the cache can't answer well now go to the live agent for a real answer",
            "Crisp factual cached answers (IPs, commands, run times) still serve instantly",
        ],
    },
    {
        "version": "2.1.0",
        "date": "2026-06-16",
        "title": "Smarter starter chips, duplicate-upload guard & a steadier Workspace",
        "lines": [
            "Home starter questions now span the whole corpus — one per document, rotating",
            "Chips for documents without Q&A are derived from the document's real topic",
            "Block uploading a document whose name already exists — clear inline message",
            "Workspace tabs stay in sync — switching Documents / Facts / Graph no longer hangs",
            "Ingest keeps running in the background while you browse other pages",
        ],
    },
    {
        "version": "2.0.0",
        "date": "2026-06-15",
        "title": "Workspace, dashboards, embedding, Teams & smarter knowledge",
        "lines": [
            "Unified Workspace — Brain + dashboards in one shell with a grouped left rail",
            "Full analytics suite: Exec, Users, Performance, Knowledge, Review, System (admin)",
            "Embeddable chat widget + multi-tenant keys, daily caps and rate limits",
            "Run Aria inside Microsoft Teams; share any answer via a read-only link",
            "Auto mode picks Quick vs Deep; agentic retrieval reformulates hard questions",
            "Knowledge as Open Knowledge Format (OKF) — import & export bundles",
            "Add documents menu: upload files, a whole folder, or import from the server",
            "Right-side Jobs panel — watch ingest at scale (stages, live log, queue)",
            "Auto-facts: durable facts pulled from each document at ingest",
            "Fact approval governance — choose what goes live automatically vs needs review",
            "Every answer shows tokens, cost and an automatic accuracy score",
            "Documents list now shows uploaded / updated dates and who uploaded",
        ],
    },
    {
        "version": "1.6.0",
        "date": "2026-06-11",
        "title": "Coverage Audit, weekly digest & a review gate for learned facts",
        "lines": [
            "Brain → Audit: Aria grades her own knowledge gaps (score + 4 pillars)",
            "Blind spots — questions answered with no source page, ranked by frequency",
            "Highest-leverage gaps surfaced first (fix what breaks the most answers)",
            "Weekly knowledge digest posts to the activity feed on its own (cadence)",
            "Facts learned in chat now wait for review before they affect answers",
            "One-click Approve / Reject on pending facts in Brain → Facts",
            "Fact freshness: see how often each fact is actually used; retire stale ones",
        ],
    },
    {
        "version": "1.5.0",
        "date": "2026-06-11",
        "title": "Self-learning — Aria remembers what you teach her",
        "lines": [
            "Teach a fact mid-chat — say 'remember…' / 'from now on…' / 'correction…' and Aria saves it",
            "Learned facts show a 🤖 'learned in chat' badge in Brain → Facts",
            "Taught facts and corrections override the documents when they disagree",
            "A 'Learned something new' step appears in the live thinking trace",
            "Normal questions store nothing — only what you explicitly teach",
            "Every learned fact is recalled in all future answers (no retraining)",
        ],
    },
    {
        "version": "1.4.0",
        "date": "2026-06-11",
        "title": "Async ingest — instant upload, background processing",
        "lines": [
            "Upload returns instantly — no more frozen 'uploading…' on big PDFs",
            "Documents process in the background: queued → processing → ready",
            "Live progress in Brain: per-page bar, status badges, auto-refresh",
            "Retry failed documents in one click",
            "Drop files straight into the storage inbox — auto-detected & ingested",
            "Half-ingested docs never answer queries until fully ready",
            "Per-page vision timeout so one stalled page can't hang an upload",
        ],
    },
    {
        "version": "1.3.0",
        "date": "2026-06-11",
        "title": "City Agent Aria — new chat, thinking & one Brain",
        "lines": [
            "Rebranded to City Agent Aria (Agent for Runbooks & IT Assistance) with logo",
            "New chat home: big title, live stat tiles, 4 category starter cards",
            "Live 'thinking' trace — see each step the agent takes, Claude-style",
            "AI follow-up questions suggested after every answer",
            "Stop mid-answer, Copy, and 👍/👎 feedback on each reply",
            "Agent Brain: documents + facts merged into one page (top tabs + inspector)",
            "Redesigned login: greeting, live corpus stats, animated capability panel",
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-06-11",
        "title": "Redesigned Brain — see everything in a document",
        "lines": [
            "Friendly document titles (raw SOP codes shown as a chip)",
            "Documents list: search, sort, filter chips, grid/list, health status",
            "Document detail: Overview (extraction health + preview), Pages, Outline, Full text",
            "Readable Full-text view — page-jump rail, search, Clean/Text/Vision toggle, .txt export",
            "Per-page image + text-layer + vision transcript side by side",
            "'Ask about this doc' jumps to a pre-filled chat",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-06-11",
        "title": "Faster, more detailed answers",
        "lines": [
            "Answers now stream live, token by token",
            "Full page text fed to the model — every step, code, path and table value",
            "Inline [p.N] citations: click a citation to open that source page",
            "Quick mode (fast, text-first) and Deep mode (reads page images + navigates)",
            "Per-user chat history with conversations in the left rail",
            "Activity bell + version / What's-new panel",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-06-11",
        "title": "DocSensei — first release",
        "lines": [
            "Bilingual EN + Burmese SOP/policy agent with vectorless PageIndex RAG",
            "Reads page images via vision model; answers cite the source page image",
            "Vision pre-read transcribes screenshot text at ingest",
            "Multi-method auth: local (bcrypt) + LDAP/AD + SSO/OIDC, merged by email",
            "User management console: Users table, Authentication config, role + approval",
            "Top navigation with Brain (Documents + Knowledge) and Settings areas",
            "Self-learning: upload docs or teach facts, no model retraining",
            "Packaged as one Docker image (FastAPI serves the SvelteKit SPA)",
        ],
    },
]


def version_info() -> dict:
    return {
        "version": VERSION,
        "sha": BUILD_SHA,
        "built": BUILD_DATE,
        "up_to_date": True,
        "changelog": CHANGELOG,
    }
