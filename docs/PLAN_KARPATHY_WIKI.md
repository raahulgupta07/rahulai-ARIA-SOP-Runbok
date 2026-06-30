# Karpathy LLM-Wiki — Gap-Closing Plan

City Agent Aria (DocSensei) is already ~75% an implementation of Karpathy's "LLM
Knowledge Bases" / "LLM wiki" pattern (his tweet + gist, ~April 2026): vectorless,
compile-once-into-markdown, self-consolidating (the `dream.py` nightly cycle). This
plan closes the remaining gaps so the brain *thinks about conflicts* and becomes a
*human-walkable wiki*.

His three layers → ours:
- **Raw sources** (immutable) → `docs` / `pages` (vision text + page images)
- **The wiki** (LLM cross-linked markdown) → `doc_pages_md` / `doc_wiki` (+ entities,
  edges) — *exists, but not cross-linked into browsable entity pages yet*
- **The schema** (conventions config) → **Phase 0 adds this** (`appcfg.wiki_schema`)

His maintenance passes (compile / lint / health-check) → `compile_wiki` ✓,
`kb_lint` ⚠️ shallow, `dream.py` ✓ partial.

---

## Phase 0 · Foundation + quick wins — DONE
- **0.1 Wiki schema doc** ✓ — `appcfg.get_wiki_schema()` / `save_wiki_schema()` +
  `GET/POST /api/settings/wiki-schema` + Settings → Answer Behaviour editor.
  Defaults: `entity_types`, `link_style`, `contradiction.compare_attributes`,
  `contradiction.auto_resolve_newer`, `freshness_days`, `orphan_min_inbound`.
  Read by Phases 1–4. DB overrides env defaults; one-level-deep merge.
- **0.2 Re-ingest failed doc** ✓ — no failed docs (the previously-failed
  `User Creation & Disable` is ready).
- **0.3 Header sanitize** — N/A for DocSensei (PDF SOPs, no tabular headers; that
  gap belongs to a different project's CSV connector). Dropped.

## Phase 1 · Critical thinking on ingest ⭐ — DONE (built by 3 sub-agents)
New data now *challenges* old data. Flag `SEMANTIC_CONTRADICTION` (compose default
OFF; dev `.env` ON). Tables `doc_claims` + `claim_conflict` (db.py). E2E proven:
planted a conflicting value → flagged → LLM-confirmed → review queue → resolve.
- **1.1** `app/claims.py` ✓ — `extract_claims(doc_id)` reads compiled markdown +
  schema `compare_attributes`/`entity_types`, LLM → atomic `{entity, attribute,
  value, page, raw}` → `doc_claims` (rerun-safe). `claims_for_entity()` lookup.
- **1.2** `app/contradiction.py` ✓ — `detect_for_doc(doc_id)`: each new claim →
  `claims_for_entity` shared-entity match → keep differing values → ONE LLM judge
  per claim (real conflict vs false match) → `claim_conflict` (deduped). Respects
  `contradiction.auto_resolve_newer` (auto `kept_new` vs `pending`).
- **1.3** Two-way is inherent — comparison spans *every* existing shared-entity doc.
- **1.4** Review queue ✓ — `GET /api/contradictions` + `POST /…/{id}/resolve`
  (new|old|both|dismiss) + Dashboard → **Contradictions** page (side-by-side
  new-vs-existing cards, 4 actions). Ingest hook = `_t_contradiction` enricher.
- **Model fix:** `CONTRADICTION_MODEL` must NOT default to `INGEST_MODEL` (carries
  an `openrouter/` prefix the chat client rejects) → falls back to `CHAT_MODEL`.
- Phase-2 will make resolve actually supersede the losing claim (temporal).

## Phase 2 · Temporal layer — DONE
Flag `BITEMPORAL` (compose default OFF; dev `.env` ON). E2E proven.
- **2.1** ✓ bi-temporal cols on `doc_claims`: `valid_from`, `superseded_at`,
  `superseded_by_doc`, `supersede_reason` (idempotent ALTERs in db.py).
- **2.2** ✓ resolve supersedes the LOSER (invalidate-don't-delete — `superseded_at`
  stamped, row kept) AND promotes the WINNING value to an **active fact**
  (`source='contradiction'`) → overrides the docs in answers via existing
  precedence. Verified: resolve `new` → `{superseded_claim, promoted_fact}`;
  old claim kept in history; winning fact active.
- **2.3** ✓ `GET /api/claims/superseded` = temporal history ("what was the rule
  before"). Full "as of date" retrieval deferred until claims feed retrieval
  (claims are a contradiction side-channel today; the winning-value→fact path
  already makes resolutions take effect in answers).

## Phase 3 · Browsable wiki surface ⭐ — DONE
Read-only, always on. Backend `app/wiki.py` (pure reads + regex linker, fail-soft) +
4 routes; FE 3 pages + top-nav "Wiki" (built by 1 sub-agent). E2E proven on the
live 174-entity corpus.
- **3.1** ✓ entity hub `/wiki/entity/{id}` — `entity_hub()`: appears-in (mentions),
  asserted values (claims, superseded struck-through), conflicts, related
  (co-mentioned entities ranked by overlap). Proven: AMS hub shows mentions +
  related CMHL/Confirm Password/…
- **3.2** ✓ backlinks — `doc_view()` returns referenced-by (doc_dependency to_doc +
  doc_links) + prerequisites (depends_on). Sparse on a 2-doc corpus (honest).
- **3.3** ✓ auto-hyperlink — `link_entities()` wraps the first occurrence of each
  known entity name in compiled markdown as `[name](/wiki/entity/ID)`, skips code
  fences, longest-name-first. Proven: 78 links injected into doc 17 (Gold Central,
  User, CMHL → hubs). FE intercepts clicks → SPA goto.
- **3.4** ✓ index/landing `/wiki` — `wiki_index()`: top entities by doc-count +
  `catalog.build_catalog()` groups + totals; live client-side search.
- **3.5** ✓ conflict badges on the entity hub (links to `/dashboard/contradictions`);
  superseded claims marked. (Freshness badge deferred — needs Phase 4 staleness.)
- Routes: `GET /api/wiki/{index,entity/{id},doc/{id},resolve}`.
- Cosmetic: catalog category = None when docs uncategorized (run Re-tag to fill).

## Global-query router — DONE (added ahead of Phase 4)
Fixes the class of question the per-page pipeline always refused: corpus-level /
meta questions ("summarize all documents", "what do you cover", "list all SOPs").
Root cause was: retrieval is per-page keyword match → a meta query scores ~0.07 →
the scope gate (floor 0.10) refused it as off-topic; there was no global path.
- `app/global_answer.py` (sub-agent) — `is_global_query(q)` (tight regex allow-list,
  precision-biased; catches misspelling "summaries", bare "all documents") +
  `build_overview(sectors, folders)` → deterministic markdown from the doc list +
  per-doc `doc_wiki.summary` (fallback first compiled page) + `catalog` topics.
  RBAC-scoped, fail-soft, NO LLM (grounded recital).
- Wired into BOTH `/ask` + `/ask/stream` BEFORE cache/retrieval/scope-gate (me).
  `done` carries `global: true`, `grounded: true`.
- Proven: "summaries all documents" / "summarize everything" / "list all sops" →
  corpus overview; "how do I disable a user" → still normal grounded answer (control).
- Accuracy note: content-answer accuracy unchanged; meta-question answered-rate goes
  0% (always refused) → ~100% (grounded from real summaries). Overview line quality
  = the doc's `doc_wiki.summary` quality (thin while a doc is still `ready_lite`).

## Phase 4 · Consolidation upgrades
- **4.1** orphan health-check in `dream.py` (uses `orphan_min_inbound`).
- **4.2** nightly community summaries (wire `graphrag.build_communities` +
  `global_answer`).

## Phase 5 · Eval gate across deployments
- **5.1** frozen golden set per project. **5.2** auto-run on settings/model/ingest
  change → flag regressions. **5.3** eval dashboard. Flag `EVAL_GATE` (default OFF).

## Phase 6 · Cleanup
- **6.1** bulk select in cards view. **6.2** kill the Phase-2 page re-insert window
  (in-place UPDATEs).

---

### Research basis (verified)
- Karpathy "LLM Knowledge Bases" tweet + gist: LLM maintains a persistent,
  compounding, cross-linked markdown wiki; "contradictions already flagged";
  no embeddings; three layers (sources / wiki / schema). Scoped to ~100 sources.
- Field patterns: Microsoft GraphRAG (Leiden communities + hierarchical summaries),
  Zep/Graphiti (bi-temporal edge-invalidation for contradiction + supersession),
  Mem0 (ADD/UPDATE/DELETE/NOOP on new facts), DeepWiki (auto entity pages + source
  backlinks).
