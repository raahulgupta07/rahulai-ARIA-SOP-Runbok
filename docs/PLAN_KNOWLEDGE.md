# Aria Knowledge Quality — Phased Improvement Plan

Goal: capture EVERYTHING in a document once at ingest, convert it into a
user-friendly "what / how / how-to-achieve" form, and connect many docs
(SOP, runbook, procedure, policy, reference) into one brain.

Principles (do not break):
- Vision/LLM runs ONCE at ingest, stored forever. Chat never re-reads images live.
- Every new compiler = fail-soft (any error → keep what we already have).
- Every phase = flag-gated in `app/config.py` + `docker-compose.yml` `${VAR:-default}`,
  default OFF on first ship, flipped ON after verify.
- Reuse existing infra: `compile_wiki.py`, `doc_qa.py`, `doc_facts.py`,
  `categorize.py`, knowledge graph (`doc_links`), `audit.py`.
- User approves each phase before the next.

Legend: [BE]=backend  [DB]=schema  [API]=route  [UI]=svelte  [FLAG]=config  [V]=verify

---

## PHASE 0 — Close the capture gaps  (small, do first)

Fixes the measured holes: unreadable embedded screenshots + missing visual cues.

- [ ] 0.1 [BE] `ingest.py` `RENDER_DPI` 150 → 220 (legible screenshot-in-screenshot).
- [ ] 0.2 [FLAG] make DPI an env knob `RENDER_DPI` (compose + .env) — tune without rebuild.
- [ ] 0.3 [BE] `vision.py` `_PROMPT` v2: keep exact-transcription block, ADD a
      "VISUAL CUES:" block — which button/field is highlighted/circled/arrowed,
      what to click, the active tab/screen. Bump `max_tokens` 1200 → 1600.
- [ ] 0.4 [BE] re-ingest screenshot docs via `/documents/{id}/retry` (not all docs).
- [ ] 0.5 [V] re-pull doc 9 page 5 → confirm the step-5 mini-screenshot now reads;
      confirm "VISUAL CUES" lines present.

Landmine: bigger PNGs = more storage + slower render; 220 is the sweet spot, don't go 300+.

---

## PHASE 1 — Playbook compiler (user-friendly how-to, pre-made)

Turn each procedural doc into a stored, ready-to-serve guide. No live conversion.

- [ ] 1.1 [DB] new table `doc_playbook(doc_id PK, goal, who, prerequisites,
      steps_md, verify_md, if_fails_md, escalate, raw_json, chars, created_at)`.
- [ ] 1.2 [BE] new `app/compile_playbook.py` `compile_playbook(doc_id)` — ONE LLM
      pass over `doc_wiki.md` → JSON {goal, who, prerequisites[], steps[], verify[],
      if_fails[], escalate}. "REFORMAT into plain user steps, keep every code/path/SQL
      inline, never invent." Fail-soft.
- [ ] 1.3 [BE] hook in `ingest.process_doc` after `compile_wiki` + `categorize`
      (gated `COMPILE_PLAYBOOK`).
- [ ] 1.4 [FLAG] `COMPILE_PLAYBOOK=1`, `PLAYBOOK_MODEL` (empty→CHAT_MODEL).
- [ ] 1.5 [BE] `scripts/compile_playbooks.py` backfill all ready docs (idempotent).
- [ ] 1.6 [BE] chat: when a doc has a playbook, inject it as the primary context
      block for "how do I…" intents (retrieve.py / agent.py `_context`).
- [ ] 1.7 [API] `GET /api/documents/{id}/playbook`.
- [ ] 1.8 [UI] Brain doc reader: new "Playbook" view tab (goal/steps/verify/if-fails).
- [ ] 1.9 [V] doc 9 → playbook shows Goal + numbered steps + "you're done when trt=1
      returns rows" + "if fails delete file in both paths".

---

## PHASE 2 — Doc-type-aware capture

Different stored shape per type so each doc answers in its best form.

- [ ] 2.1 [BE] extend `categorize.py` → also classify `doc_type ∈
      {sop, runbook, policy, reference, other}` (store `docs.doc_type`).
- [ ] 2.2 [DB] `docs.doc_type` column (idempotent ALTER).
- [ ] 2.3 [BE] runbook compiler → incident flow (symptom → diagnose → resolve →
      rollback) into `doc_playbook` (reuse table, type-tagged) or `doc_runbook`.
- [ ] 2.4 [BE] policy compiler → rules + do's/don'ts + applies-to.
- [ ] 2.5 [BE] reference compiler → lookup rows (term → meaning/code) into a
      `doc_lookup(doc_id, term, value, page_id)` table (powers exact "what is 3001").
- [ ] 2.6 [BE] dispatcher: `process_doc` picks compiler by `doc_type` (SOP→Phase1).
- [ ] 2.7 [FLAG] `TYPE_AWARE_COMPILE=1`.
- [ ] 2.8 [UI] doc card shows a type badge; reader picks the matching view.
- [ ] 2.9 [V] upload one of each type → each lands in the right structure.

---

## PHASE 3 — Entity index (cross-document brain)

Same thing (a GOLD menu code, "purchase price", a server IP) lives in many docs.

- [ ] 3.1 [DB] `entities(id, name, kind, norm_key)` +
      `entity_mention(entity_id, doc_id, page_id)`.
- [ ] 3.2 [BE] `app/entities.py` extract canonical entities at ingest from compiled
      text (codes, menu paths, systems, IPs, screen names) → dedupe by norm_key (pg_trgm).
- [ ] 3.3 [BE] entity page builder: "Purchase price → SOP 9, 11, 12 (pages …)".
- [ ] 3.4 [API] `GET /api/entities`, `GET /api/entities/{id}`.
- [ ] 3.5 [BE] feed entity nodes into the knowledge graph (`brain/graph`).
- [ ] 3.6 [BE] chat: entity match expands retrieval across all docs touching it.
- [ ] 3.7 [FLAG] `ENTITY_INDEX=1`.
- [ ] 3.8 [UI] Brain "Entities" tab + entity drawer (where-it-appears chips).
- [ ] 3.9 [V] search "purchase price" → lists every doc/page that covers it.

---

## PHASE 4 — Conflict lint + freshness (trust at scale)

- [ ] 4.1 [BE] contradiction lint: same entity/term defined differently across docs
      → `kb_conflict(entity_id, doc_a, doc_b, detail)`.
- [ ] 4.2 [BE] run at ingest + on-demand `POST /api/kb/lint`.
- [ ] 4.3 [BE] surface conflicts in `audit.py` coverage report.
- [ ] 4.4 [BE] freshness: read doc date (e.g. `30/07/24`) + `docs.created_at`;
      flag `stale` past `DOC_STALE_DAYS`.
- [ ] 4.5 [API] `GET /api/kb/conflicts`, `GET /api/kb/stale`.
- [ ] 4.6 [FLAG] `KB_LINT=1`, `DOC_STALE_DAYS=365`.
- [ ] 4.7 [UI] Audit page: Conflicts card + Stale-docs card (review/dismiss).
- [ ] 4.8 [V] seed two docs with a clashing definition → conflict surfaces.

---

## PHASE 5 — Procedure chaining / prerequisites

- [ ] 5.1 [BE] at playbook compile, extract `requires:` links ("create user before
      assigning rights") → `doc_dependency(from_doc, to_doc, reason)`.
- [ ] 5.2 [DB] `doc_dependency` table.
- [ ] 5.3 [BE] chat: prepend "Before this, complete: <dep doc>" when a dep exists.
- [ ] 5.4 [BE] dependency edges into the knowledge graph.
- [ ] 5.5 [FLAG] `PROC_CHAINING=1`.
- [ ] 5.6 [UI] playbook view shows a "Prerequisites" strip linking the prior SOP.
- [ ] 5.7 [V] user-creation chain → "first create the user (SOP 2), then assign rights".

---

## PHASE 6 — Delivery: how the user experiences it

- [ ] 6.1 [BE/UI] **Guided step-by-step mode** — serve one step at a time,
      "Done? → next" (drives off `doc_playbook.steps`). Chat toggle "Guide me".
- [ ] 6.2 [BE/UI] **Troubleshooting tree** — runbook if/then → interactive narrowing
      (ask → branch → fix).
- [ ] 6.3 [BE] **Role-based answer depth** — `end_user` (simplified) vs `it_support`
      (full); pick by user role; same playbook, two renderings.
- [ ] 6.4 [UI] **Annotated visual answer** — use Phase-0 visual cues to say "click the
      circled Validate button" beside the page image.
- [ ] 6.5 [FLAG] `GUIDED_MODE`, `ROLE_DEPTH`.
- [ ] 6.6 [V] run a procedure in guided mode end-to-end; end-user vs admin answer differ.

---

## PHASE 7 — Catalog + multi-doc synthesis

- [ ] 7.1 [BE] auto master catalog: "what Aria can help with", grouped by
      system/task (from entities + categories + playbooks).
- [ ] 7.2 [API] `GET /api/catalog`.
- [ ] 7.3 [UI] home/onboarding "Capabilities" view; feeds starter chips.
- [ ] 7.4 [BE] multi-doc synthesis: question spanning N SOPs → one merged procedure
      (combine playbooks of all retrieved docs before answering).
- [ ] 7.5 [FLAG] `MULTIDOC_SYNTH=1`.
- [ ] 7.6 [V] ask a cross-SOP question → single coherent merged answer with all sources.

---

## Recommended order

1. **Phase 0** (tiny, fixes real gaps) → 2. **Phase 1** (biggest single quality jump)
→ 3. **Phase 2** (mixed corpus pays off) → 4. **Phase 3 + 4** (many-doc brain + trust)
→ 5. **Phase 5–7** (depth + delivery polish).

Each phase: build → in-container verify → flip flag ON → user sign-off → next.

Deploy per phase: `cd frontend && rm -rf .svelte-kit/output build && npm run build`
then `rtk proxy docker compose build app` + `up -d --force-recreate app`; verify
flag via `docker exec docsensei-app sh -c 'echo $VAR'` and the new endpoint/table.
