"""Version + changelog, served publicly at /api/version.

Bump VERSION + prepend a CHANGELOG entry by hand before cutting a release
(see release.sh). BUILD_SHA / BUILD_DATE are stamped into the image at build
time (Dockerfile ARG/ENV) and read here from the environment.
"""
import os

VERSION = "2.1.1"
BUILD_SHA = os.getenv("BUILD_SHA", "dev")
BUILD_DATE = os.getenv("BUILD_DATE", "")

# newest first
CHANGELOG = [
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
