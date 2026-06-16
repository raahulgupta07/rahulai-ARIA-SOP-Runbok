<script lang="ts">
  // Agent Brain — one full-width library that unifies Documents (uploaded files)
  // and Facts (taught knowledge). Stacked left rail (Docs on top, Facts below) +
  // an inline inspector on the right (doc = 4 tabs, fact = card). One search, one Add.
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { get } from 'svelte/store';
  import { api } from '$lib/api';
  import { parseDocName, cleanText } from '$lib/docname';
  import Lightbox from '$lib/Lightbox.svelte';
  import { auth } from '$lib/auth';
  import { brainTeachSignal, brainFilesSignal, brainScanSignal, brainS3Signal, brainData, loadBrainData } from '$lib/dashstore';
  let _me = $state<any>(auth.cachedUser());
  $effect(() => { if (!_me) auth.me().then((u) => (_me = u)).catch(() => {}); });
  let isAdmin = $derived(_me?.role === 'admin');

  type Doc = {
    id: number; name: string; lang: string; page_count: number; sections: number; created_at: string;
    status?: string; progress?: number; pages_done?: number; error?: string | null; ready_at?: string | null;
    cover_page_id?: number | null; category?: string | null;
    vision_pages?: number; text_pages?: number; used_count?: number; last_used_at?: string | null;
    uploaded_by?: string | null; updated_at?: string | null;
  };
  type Fact = { id: number; key?: string; value: string; source?: string; created_by?: string;
    status?: string; cited_count?: number; last_cited_at?: string; created_at?: string };

  // The Brain data bundle now lives in ONE shared store (loaded at the Workspace
  // shell + hydrated from localStorage) so the Documents grid paints instantly on
  // every tab and never races first paint. These are read-only $derived views.
  let docs = $derived<Doc[]>($brainData.docs);
  let facts = $derived<Fact[]>($brainData.facts);
  let pending = $derived($brainData.pending);          // chat-learned facts awaiting review
  let stats = $derived<any>($brainData.stats);
  let loading = $derived(!$brainData.loaded);

  // ── Q&A bank (auto-mined from docs + harvested from upvoted chat) ──
  type QAPair = {
    id: number; question: string; answer: string; source?: string;
    status?: string; confidence?: number | null; doc_id?: number | null;
    page_ids?: number[]; origin?: string | null; cited_count?: number;
    created_at?: string;
  };
  let qaPairs = $derived<QAPair[]>($brainData.qaPairs);
  let qaCounts = $derived<{ active: number; pending: number; rejected: number }>($brainData.qaCounts);
  let qaFilter = $state<'all' | 'active' | 'pending' | 'rejected'>('all');

  let q = $state('');                                  // one search over docs + facts
  let collapsed = $state<Record<string, boolean>>({}); // collapsed category groups
  let _railTab = $state<'brain' | 'audit' | 'graph'>('brain');   // backing state — standalone mode only

  // ── embedded mode (inside unified Workspace) — hide own rail, drive view from props ──
  let { embedded = false, showTabs = true, extView = null } = $props<{ embedded?: boolean; showTabs?: boolean; extView?: { tab: 'brain' | 'graph' | 'audit'; feed?: 'all' | 'doc' | 'fact' } | null }>();
  // SINGLE SOURCE OF TRUTH: embedded → view is the extView prop (Workspace rail drives it);
  // standalone → the local _railTab/_feedType buttons drive it. (Was a fragile $effect that
  // mirrored extView into local state and desynced rail vs content on tab switch.)
  let railTab: 'brain' | 'audit' | 'graph' = $derived(embedded && extView ? extView.tab : _railTab);
  $effect(() => { if (embedded && extView) { extView.tab; extView.feed; sel = null; } });  // close any open reader on rail nav

  // ===== UNIFIED BRAIN FEED (Scout-style single knowledge feed) =====
  type FeedItem = {
    type: 'page' | 'fact' | 'doc'; id: number; title: string; snippet: string;
    doc_id: number | null; page_no: number | null; image_url: string | null;
    source: string | null; status: string | null; score: number;
  };
  let feedItems = $state<FeedItem[]>([]);
  let feedLoading = $state(false);
  let _feedType = $state<'all' | 'doc' | 'fact' | 'qa'>('doc');  // backing state — standalone mode only
  let feedType: 'all' | 'doc' | 'fact' | 'qa' = $derived(embedded && extView?.feed ? extView.feed : _feedType);  // embedded → from prop
  let feedErr = $state('');
  let feedSeq = 0;                                         // ignore stale responses
  function authHeaders(): Record<string, string> {        // mirror api.ts Bearer auth
    const h: Record<string, string> = {};
    try { const t = localStorage.getItem('docsensei_token'); if (t) h['Authorization'] = `Bearer ${t}`; } catch {}
    return h;
  }
  async function loadFeed() {
    const seq = ++feedSeq;
    feedLoading = true; feedErr = '';
    try {
      const u = new URL(`${api.base}/brain/search`, location.origin);
      u.searchParams.set('q', q.trim());
      u.searchParams.set('type', feedType);
      const r = await fetch(u, { headers: authHeaders() });
      if (!r.ok) throw new Error('search failed');
      const data = await r.json();
      if (seq === feedSeq) feedItems = data.items || [];
    } catch (e: any) {
      if (seq === feedSeq) { feedItems = []; feedErr = e?.message || 'search failed'; }
    } finally {
      // ALWAYS clear loading — never gate behind the seq guard. A remount (Workspace
      // mounts Brain inside {#key active.id}) or a superseding call bumps feedSeq; if
      // this finally skipped the reset, feedLoading stuck true forever → skeleton-hang.
      // Keep seq only for DATA writes (feedItems/feedErr) above.
      feedLoading = false;
    }
  }
  let feedDebounce: ReturnType<typeof setTimeout> | null = null;
  function scheduleFeed() {
    if (feedDebounce) clearTimeout(feedDebounce);
    feedDebounce = setTimeout(loadFeed, 280);
  }
  // re-search whenever the query or chip changes (and we're on the Brain tab, not in the reader)
  $effect(() => { q; feedType; if (railTab === 'brain') scheduleFeed(); });
  // Load once immediately on mount/remount (don't wait out the 280ms debounce, which a
  // fast remount could cancel mid-flight) + clear any pending timer when this instance
  // dies. One-shot guard — an empty result is valid, must NOT re-fetch in a loop.
  let feedKicked = false;
  $effect(() => {
    if (!feedKicked && railTab === 'brain') { feedKicked = true; loadFeed(); }
    return () => { if (feedDebounce) clearTimeout(feedDebounce); };
  });
  // single-letter type chip (no emoji): F fact / D document / P page
  function typeIcon(t: string) { return t === 'fact' ? 'F' : t === 'doc' ? 'D' : 'P'; }
  // Clean a feed-row PREVIEW snippet (display-only — never mutates stored data, never the title).
  // Strips a leading duplicate "<title>: <title>…" prefix, cuts everything from "# Citations",
  // drops inline [1]/[2] citation markers and ISO timestamps, collapses whitespace + ellipsizes.
  function cleanSnippet(raw: string, title = '', max = 160): string {
    let s = (raw || '').replace(/\r\n/g, '\n');
    // cut everything from a "# Citations" heading onward
    s = s.replace(/#+\s*Citations[\s\S]*$/i, '');
    // drop a leading duplicate "X: X" — when the snippet repeats the title as a prefix
    const t = (title || '').trim();
    if (t) {
      const pre = t + ':';
      if (s.trimStart().startsWith(pre)) s = s.trimStart().slice(pre.length);
    }
    // also collapse a generic "Foo: Foo …" self-duplicate at the start
    const dup = s.match(/^\s*(.{4,120}?)\s*:\s*\1\b/);
    if (dup) s = s.slice(dup[0].length - dup[1].length);
    // remove ISO timestamps like (2026-06-13T13:03:47.436811+00:00)
    s = s.replace(/\(?\d{4}-\d{2}-\d{2}T[\d:.]+(?:[+-]\d{2}:?\d{2}|Z)?\)?/g, ' ');
    // remove inline citation markers like [1] [12]
    s = s.replace(/\[\d+\]/g, ' ');
    // collapse whitespace + trim
    s = s.replace(/\s+/g, ' ').trim();
    if (s.length > max) s = s.slice(0, max).replace(/\s+\S*$/, '') + '…';
    return s;
  }
  function typeTint(t: string) { return t === 'fact' ? 'linear-gradient(140deg, hsl(212 44% 95%), hsl(204 40% 90%))' : t === 'doc' ? 'linear-gradient(140deg, hsl(36 42% 95%), hsl(30 38% 90%))' : 'linear-gradient(140deg, hsl(150 24% 95%), hsl(150 22% 90%))'; }
  function typeInk(t: string) { return t === 'fact' ? 'hsl(214 34% 42%)' : t === 'doc' ? 'hsl(34 42% 40%)' : 'hsl(150 26% 36%)'; }
  function typeLabel(t: string) { return t === 'fact' ? 'FACT' : t === 'doc' ? 'DOCUMENT' : 'PAGE'; }
  // feed card click → reuse existing nav (doc reader URL-driven / fact modal)
  function openFeedItem(it: FeedItem) {
    if (it.type === 'fact') {
      const f = facts.find((x) => x.id === it.id);
      if (f) { selectFact(f); return; }
      // fallback: synthesize a minimal fact for the modal from feed data
      selectFact({ id: it.id, value: it.snippet || it.title, source: it.source || undefined, status: it.status || undefined } as Fact);
      return;
    }
    if (it.type === 'page' && it.doc_id) {
      goto(readerHref({ doc: it.doc_id, view: 'read', pg: it.page_no && it.page_no > 1 ? it.page_no : null }), { keepFocus: true, noScroll: true });
      return;
    }
    if (it.type === 'doc' || it.doc_id) {
      goto(readerHref({ doc: it.doc_id ?? it.id, view: null, pg: null }), { keepFocus: true, noScroll: true });
    }
  }

  // selection / inspector
  let sel = $state<{ type: 'doc' | 'fact' | 'teach'; id: number } | null>(null);
  let addOpen = $state(false);
  let dragOver = $state(false);
  let uploads = $state<{ name: string; status: string; dup?: boolean }[]>([]);   // only used for upload ERRORS / skipped now
  let fileInput: HTMLInputElement;

  // Thin wrapper over the shared store loader. force=true so explicit reloads
  // after mutations (approve/reject/upload/retry/qa edit) always refetch.
  // Dedup + stale-while-revalidate now live in loadBrainData.
  async function load(_show = true) { await loadBrainData(true); }
  // Standalone /brain route has no Workspace shell to kick the load → trigger it
  // here (loadBrainData dedups + SWR-guards, so it's cheap when the shell already did).
  $effect(() => { loadBrainData(); loadAudit(); });   // audit loads on mount too → tab chip + dashboard

  function band(s: number) { return s >= 85 ? '#5fa463' : s >= 60 ? '#d3a13e' : '#cf6a4c'; }
  function scoreVerdict(s: number) { return s >= 90 ? 'Excellent' : s >= 75 ? 'Healthy' : s >= 55 ? 'Needs work' : 'At risk'; }
  // collapse runs of consecutive digest notifications into one "×N" row
  function foldActivity(list: any[]) {
    const out: any[] = [];
    for (const n of list) {
      const prev = out[out.length - 1];
      if (n.kind === 'digest' && prev && prev.kind === 'digest') { prev._count = (prev._count || 1) + 1; continue; }
      out.push({ ...n, _count: 1 });
    }
    return out;
  }
  let recentDocs = $derived(
    [...docs].filter((d) => (d.status || 'ready') === 'ready')
      .sort((a, b) => (new Date(b.created_at).getTime() || 0) - (new Date(a.created_at).getTime() || 0))
      .slice(0, 4)
  );

  // ---- live ingest polling: poll every 2s while any doc is queued/processing ----
  let inFlight = $derived(docs.some((d) => d.status === 'queued' || d.status === 'processing'));
  let pollTimer = $state<ReturnType<typeof setInterval> | null>(null);
  $effect(() => {
    if (inFlight && !pollTimer) {
      pollTimer = setInterval(() => load(false), 2000);
    } else if (!inFlight && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    return () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };
  });

  // ---- process flow: 7-stage pipeline derived from status + progress ----
  const STAGES = ['Queued', 'Render', 'Read', 'Structure', 'Compile', 'Tag', 'Ready'];
  function stageOf(d: Doc): { idx: number; label: string; sub: string; failed?: boolean; cancelled?: boolean; queued?: boolean } {
    const st = (d.status || 'ready').toLowerCase();
    if (st === 'failed') return { idx: -1, label: 'Failed', sub: '', failed: true };
    if (st === 'cancelled') return { idx: -1, label: 'Cancelled', sub: '', cancelled: true };
    if (st === 'queued') return { idx: 0, label: 'Queued', sub: '', queued: true };
    if (st === 'ready') return { idx: 6, label: 'Ready', sub: '' };
    const p = d.progress ?? 0;   // processing
    if (p < 30) return { idx: 1, label: 'Rendering pages', sub: '' };
    if (p < 80) return { idx: 2, label: 'Reading pages', sub: `${d.pages_done ?? 0}/${d.page_count || '?'}` };
    if (p < 86) return { idx: 3, label: 'Structuring', sub: '' };
    if (p < 96) return { idx: 4, label: 'Compiling', sub: '' };
    if (p < 100) return { idx: 5, label: 'Categorizing', sub: '' };
    return { idx: 6, label: 'Ready', sub: '' };
  }
  // active docs for the "Processing now" strip: processing first, then queued (queue order)
  let activeDocs = $derived(
    docs.filter((d) => d.status === 'processing' || d.status === 'queued')
      .sort((a, b) => {
        const r = (a.status === 'processing' ? 0 : 1) - (b.status === 'processing' ? 0 : 1);
        return r !== 0 ? r : a.id - b.id;
      })
  );
  let queuedOrder = $derived(activeDocs.filter((d) => d.status === 'queued').map((d) => d.id));
  function queuePos(d: Doc): number { return queuedOrder.indexOf(d.id) + 1; }

  // fact origin flag: AI = LLM-extracted (doc/chat), Manual = a person entered it
  function factIsAI(src?: string): boolean { return src === 'doc' || src === 'chat'; }
  function factScore(f: any): number | null { return f?.confidence != null ? Math.round(f.confidence * 100) : null; }
  let factFilter = $state<'all' | 'ai' | 'manual' | 'pending'>('all');
  function factMatch(it: any): boolean {
    if (it.type !== 'fact') return true;
    if (factFilter === 'ai') return factIsAI(it.source);
    if (factFilter === 'manual') return !factIsAI(it.source);
    if (factFilter === 'pending') return it.status === 'pending';
    return true;
  }

  // batch-scale summary (B+C): bounded rows = only the docs actively processing;
  // everything else rolls up into lane counts so 100+ uploads never wall the screen.
  let procDocs = $derived(docs.filter((d) => d.status === 'processing'));
  let queuedN = $derived(docs.filter((d) => d.status === 'queued').length);
  let readyN = $derived(docs.filter((d) => (d.status || 'ready') === 'ready').length);
  let failedN = $derived(docs.filter((d) => d.status === 'failed').length);
  let remaining = $derived(procDocs.length + queuedN);
  let overallPct = $derived(remaining ? Math.round(procDocs.reduce((a, d) => a + (d.progress ?? 0), 0) / remaining) : 0);
  let lanes = $derived.by(() => {
    const c = { Render: 0, Read: 0, Structure: 0, Compile: 0, Tag: 0 };
    for (const d of procDocs) {
      const i = stageOf(d).idx;
      if (i === 1) c.Render++; else if (i === 2) c.Read++; else if (i === 3) c.Structure++;
      else if (i === 4) c.Compile++; else if (i === 5) c.Tag++;
    }
    return c;
  });

  // ---- right-side Jobs slide-over (Mockup D) ----
  let jobsOpen = $state(false);
  let jobQ = $state('');
  let queuedDocs = $derived(docs.filter((d) => d.status === 'queued').sort((a, b) => a.id - b.id));
  let failedDocs = $derived(docs.filter((d) => d.status === 'failed'));
  let readyRecent = $derived(
    docs.filter((d) => (d.status || 'ready') === 'ready')
      .sort((a, b) => (new Date(b.ready_at || b.updated_at || 0).getTime()) - (new Date(a.ready_at || a.updated_at || 0).getTime()))
      .slice(0, 10)
  );
  let queuedShown = $derived(jobQ.trim() ? queuedDocs.filter((d) => parseDocName(d.name).title.toLowerCase().includes(jobQ.trim().toLowerCase())) : queuedDocs);

  // ---- inline per-doc flow expand + live log ----
  let flowOpen = $state<number | null>(null);
  let flowLog = $state<{ ts: string; msg: string; level?: string }[]>([]);
  let flowTimer = $state<ReturnType<typeof setInterval> | null>(null);
  function toggleFlow(d: Doc) { flowOpen = flowOpen === d.id ? null : d.id; }
  async function loadFlowLog(id: number) {
    try {
      const r = await api.ingestLog(0, 200);
      flowLog = (r.lines || []).filter((l: any) => l.doc_id === id).slice(-8);
    } catch { flowLog = []; }
  }
  $effect(() => {
    const id = flowOpen;
    if (flowTimer) { clearInterval(flowTimer); flowTimer = null; }
    if (id != null) {
      loadFlowLog(id);
      flowTimer = setInterval(() => loadFlowLog(id), 2500);
    } else { flowLog = []; }
    return () => { if (flowTimer) { clearInterval(flowTimer); flowTimer = null; } };
  });

  $effect(() => {
    if (!addOpen) return;
    const close = () => (addOpen = false);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  });

  function fmtDate(s?: string) { try { return s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''; } catch { return ''; } }
  function fmtDateShort(s?: string) { try { return s ? new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).toUpperCase() : ''; } catch { return ''; } }
  // compact date for list columns (e.g. "Jun 15")
  function fmtDay(s?: string | null) { try { return s ? new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—'; } catch { return '—'; } }
  function shortUser(e?: string | null) { return e ? e.split('@')[0] : '—'; }
  // ingest duration (upload → ready), shown as Process stat on a card; null if unknown/absurd
  function ingestSecs(d: Doc): number | null {
    if (!d.ready_at || !d.created_at) return null;
    const s = (new Date(d.ready_at).getTime() - new Date(d.created_at).getTime()) / 1000;
    return s > 0 && s < 86400 ? Math.round(s) : null;
  }
  function fmtSecs(s: number) { return s < 90 ? `${s} sec` : `${Math.round(s / 60)} min`; }
  // ---- generated cover (no raw page image): deterministic soft gradient by id, tinted by language ----
  function isMyLang(l?: string) { const x = (l || '').toLowerCase(); return x.startsWith('my') || x === 'mm'; }
  function coverBg(d: Doc): string {
    const base = isMyLang(d.lang) ? 36 : 212;                 // amber (MY) vs blue (EN)
    const off = ((d.id * 47) % 46) - 23;                      // ±23° spread, stable per doc
    const h2 = (base + off + 360) % 360;
    return `linear-gradient(140deg, hsl(${base} 42% 95%) 0%, hsl(${h2} 38% 90%) 100%)`;
  }
  function coverInk(d: Doc): string { const base = isMyLang(d.lang) ? 34 : 214; return `hsl(${base} 32% 42%)`; }
  // fact-card covers mirror the document covers: blue tint for chat-learned, amber for hand-taught
  function factCover(isChat: boolean): string { const h = isChat ? 212 : 38; const h2 = isChat ? 204 : 32; return `linear-gradient(140deg, hsl(${h} 44% 95%) 0%, hsl(${h2} 40% 90%) 100%)`; }
  function factInk(isChat: boolean): string { return isChat ? 'hsl(214 34% 42%)' : 'hsl(34 42% 40%)'; }
  function fileGlyph(name?: string): string {
    const n = (name || '').toLowerCase();
    if (/\.(png|jpe?g|gif|webp|bmp|tiff?)$/.test(n)) return 'IMG';
    return 'DOC';
  }
  function initials(t?: string): string {
    return (t || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join('') || '?';
  }

  // ---- doc-list filters (P3) ----
  let fLang = $state<'all' | 'en' | 'my'>('all');
  let fStatus = $state<'all' | 'ready' | 'failed'>('all');
  let fSort = $state<'recent' | 'oldest' | 'pages' | 'name' | 'used'>('recent');
  let docView = $state<'cards' | 'list'>('cards');
  // A3: category chip + date pill filters for the doc browser
  let docCat = $state('all');
  let docDate = $state<'all' | 'today' | '7d' | '30d' | 'year'>('all');
  // group the feed by auto-category or by upload date (newest first)
  let docGroupBy = $state<'date' | 'category'>('date');
  function setGroupBy(g: 'date' | 'category') { docGroupBy = g; try { localStorage.setItem('aria_docgroup', g); } catch {} }
  $effect(() => { try { const v = localStorage.getItem('aria_docgroup'); if (v === 'date' || v === 'category') docGroupBy = v; } catch {} });
  // server LLM category only (filename "GLDCENTRAL"-style prefix is NOT a category)
  function catOf(d: Doc) { return (d.category && d.category.trim()) || 'Uncategorized'; }
  const CAT_CHIP_MAX = 6;   // show top-N chips inline, rest go in the "More" dropdown
  let catMoreOpen = $state(false);
  function setDocView(v: 'cards' | 'list') { docView = v; try { localStorage.setItem('aria_docview', v); } catch {} }
  $effect(() => { try { const v = localStorage.getItem('aria_docview'); if (v === 'list' || v === 'cards') docView = v; } catch {} });
  function langColor(l?: string) {
    const x = (l || '').toLowerCase();
    if (x.startsWith('my') || x === 'mm') return { bg: '#fbecd4', fg: '#a9742a' };   // Burmese — amber
    return { bg: '#e3edf7', fg: '#3a6b9c' };                                          // EN / other — blue
  }
  function statusDot(st: string) {
    if (st === 'failed') return '#cf6a4c';
    if (st === 'queued' || st === 'processing') return '#d3a13e';
    return '#5fa463';                                                                 // ready
  }
  function relTime(s?: string) {
    if (!s) return '';
    const d = (Date.now() - new Date(s).getTime()) / 86400000;
    if (d < 1) return 'today';
    if (d < 2) return 'yesterday';
    if (d < 30) return `${Math.round(d)}d ago`;
    if (d < 365) return `${Math.round(d / 30)}mo ago`;
    return `${Math.round(d / 365)}y ago`;
  }

  // ---- left rail data ----
  let needle = $derived(q.trim().toLowerCase());
  let docMatches = $derived.by(() =>
    docs.filter((d) => {
      const st = d.status || 'ready';
      if (fStatus === 'ready' && st !== 'ready') return false;
      if (fStatus === 'failed' && st !== 'failed') return false;
      if (fLang !== 'all') {
        const x = (d.lang || '').toLowerCase();
        const isMy = x.startsWith('my') || x === 'mm';
        if (fLang === 'my' && !isMy) return false;
        if (fLang === 'en' && isMy) return false;
      }
      // A3: apply date filter (dateOk uses docDate state, reactive)
      if (!dateOk(d)) return false;
      if (!needle) return true;
      const p = parseDocName(d.name);
      return `${p.title} ${d.name} ${p.code} ${catOf(d)}`.toLowerCase().includes(needle);
    })
  );
  // in-flight docs (queued/processing/failed) always bubble to the top so the
  // user watches them finish; rank by urgency, then the chosen sort within each group.
  function statusRank(d: Doc): number {
    const s = (d.status || 'ready').toLowerCase();
    if (s === 'processing') return 0;
    if (s === 'queued') return 1;
    if (s === 'failed') return 2;
    return 3; // ready / anything settled
  }
  function sortDocs(list: Doc[]) {
    let a = [...list];
    if (fSort === 'pages') a.sort((x, y) => (y.page_count || 0) - (x.page_count || 0));
    else if (fSort === 'name') a.sort((x, y) => parseDocName(x.name).title.localeCompare(parseDocName(y.name).title));
    else if (fSort === 'oldest') a.sort((x, y) => (new Date(x.created_at).getTime() || 0) - (new Date(y.created_at).getTime() || 0));
    else if (fSort === 'used') a.sort((x, y) => (y.used_count || 0) - (x.used_count || 0));
    else a.sort((x, y) => (new Date(y.created_at).getTime() || 0) - (new Date(x.created_at).getTime() || 0)); // recent
    // stable secondary pass: unfinished docs first (Array.sort is stable in modern engines)
    return a.sort((x, y) => statusRank(x) - statusRank(y));
  }
  // A3 date filter: returns true if doc passes the docDate filter
  function dateOk(d: Doc): boolean {
    if (docDate === 'all') return true;
    const t = d.created_at ? new Date(d.created_at).getTime() : 0;
    if (!t) return docDate === 'all';
    const now = Date.now();
    const dayMs = 86400000;
    if (docDate === 'today') {
      const dn = new Date(); const dd = new Date(t);
      return dn.getFullYear() === dd.getFullYear() && dn.getMonth() === dd.getMonth() && dn.getDate() === dd.getDate();
    }
    if (docDate === '7d') return now - t <= 7 * dayMs;
    if (docDate === '30d') return now - t <= 30 * dayMs;
    if (docDate === 'year') return now - t <= 365 * dayMs;
    return true;
  }
  let maxPages = $derived(Math.max(1, ...docs.map((d) => d.page_count || 0)));
  let factMatches = $derived.by(() =>
    facts.filter((f) => !needle || `${f.key || ''} ${f.value}`.toLowerCase().includes(needle))
  );
  // group docs by category
  let docGroups = $derived.by(() => {
    const m = new Map<string, Doc[]>();
    for (const d of docMatches) {
      const c = catOf(d);
      if (!m.has(c)) m.set(c, []);
      m.get(c)!.push(d);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([cat, list]) => ({ cat, list: sortDocs(list) }));
  });

  // group docs by UPLOAD DATE, newest bucket first: Today / Yesterday /
  // Earlier this month / then calendar months (May 2026, …). Empty buckets dropped.
  const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  function dateBucket(d: Doc): { key: string; label: string; order: number } {
    const t = d.created_at ? new Date(d.created_at).getTime() : 0;
    if (!t) return { key: 'unknown', label: 'No date', order: -1 };
    const dd = new Date(t); const now = new Date();
    const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const yest = new Date(now); yest.setDate(now.getDate() - 1);
    if (sameDay(dd, now)) return { key: 'today', label: 'Today', order: 3e12 };
    if (sameDay(dd, yest)) return { key: 'yest', label: 'Yesterday', order: 3e12 - 1 };
    if (dd.getFullYear() === now.getFullYear() && dd.getMonth() === now.getMonth())
      return { key: 'tmonth', label: 'Earlier this month', order: 3e12 - 2 };
    const key = `${dd.getFullYear()}-${dd.getMonth()}`;
    return { key, label: `${MONTHS[dd.getMonth()]} ${dd.getFullYear()}`, order: dd.getFullYear() * 12 + dd.getMonth() };
  }
  let docDateGroups = $derived.by(() => {
    const m = new Map<string, { label: string; order: number; list: Doc[] }>();
    for (const d of docMatches) {
      const b = dateBucket(d);
      if (!m.has(b.key)) m.set(b.key, { label: b.label, order: b.order, list: [] });
      m.get(b.key)!.list.push(d);
    }
    return [...m.values()].sort((a, b) => b.order - a.order)
      .map((g) => ({ cat: g.label, list: sortDocs(g.list) }));
  });

  // A3: static date pill options (defined here to avoid inline string-array literals in template)
  const DOC_DATE_OPTS: [string, string][] = [['today','Today'],['7d','7d'],['30d','30d'],['year','Year'],['all','All']];
  // A3: category chips with counts (derived from full docMatches, ignoring docCat for the counts)
  let docCatCounts = $derived.by(() => {
    const m = new Map<string, number>();
    for (const d of docMatches) {
      const c = catOf(d);
      m.set(c, (m.get(c) || 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  });
  // A3: flat list of docs for a specific category selection (respects fSort + docCat)
  let docCatFlat = $derived.by(() => {
    if (docCat === 'all') return [] as Doc[];
    return sortDocs(docMatches.filter((d) => catOf(d) === docCat));
  });
  // sections to render when "All" is selected: date buckets or category groups
  let docGroupsFiltered = $derived.by(() => {
    if (docCat !== 'all') return [] as { cat: string; list: Doc[] }[];
    return docGroupBy === 'date' ? docDateGroups : docGroups;
  });
  // A3: boolean helpers so class: directives avoid string literals in template
  let isCatAll = $derived(docCat === 'all');
  let isDateAll = $derived(docDate === 'all');
  let isDateToday = $derived(docDate === 'today');
  let isDate7d = $derived(docDate === '7d');
  let isDate30d = $derived(docDate === '30d');
  let isDateYear = $derived(docDate === 'year');
  let isViewCards = $derived(docView === 'cards');
  let isViewList = $derived(docView === 'list');

  let visionPct = $derived(stats && stats.pages ? Math.round((stats.vision_pages / stats.pages) * 100) : 0);

  // ---- upload ----
  // aggregate sync progress (folder/multi-file): shows ⟳ X/N while the POST loop runs
  let sync = $state<{ total: number; done: number; cur: string; folder: boolean; finished: boolean } | null>(null);
  let syncFade: any = null;
  async function handleFiles(files: FileList | File[]) {
    const ok = /\.(pdf|png|jpe?g)$/i;
    const list = Array.from(files).filter((f) => ok.test(f.name));   // folder picks include junk → keep SOPs only
    if (!list.length) return;
    jobsOpen = true;   // slide the Jobs panel in so the user watches progress
    if (syncFade) { clearTimeout(syncFade); syncFade = null; }
    const folder = list.length > 1 || Array.from(files).length !== list.length;
    sync = { total: list.length, done: 0, cur: '', folder, finished: false };
    let okN = 0;
    for (const f of list) {
      sync = { ...sync, cur: f.name };
      // Upload returns instantly with status:"queued". The doc row shows live progress —
      // we only keep a transient banner entry to surface upload *errors*.
      try {
        await api.upload(f);
        okN++;
        await load(false);   // new queued doc appears immediately; polling takes over
      } catch (e: any) {
        const msg = e?.message || 'failed';
        const dup = /already exists/i.test(msg);
        uploads = [{ name: f.name, status: dup ? '⚠ already exists — skipped' : '✗ ' + msg, dup }, ...uploads];
      }
      sync = { ...sync, done: sync.done + 1 };
    }
    sync = { ...sync, finished: true, done: okN, total: okN, cur: '' };
    syncFade = setTimeout(() => (sync = null), 4000);   // "queued · indexing now" then fade; robot takes over
  }
  function onDrop(e: DragEvent) { e.preventDefault(); dragOver = false; if (e.dataTransfer?.files) handleFiles(e.dataTransfer.files); }

  let retagging = $state(false);
  async function retagAll() {
    if (retagging) return;
    retagging = true;
    try { await api.categorizeAll(true); await load(); } catch {} finally { retagging = false; }
  }
  async function retryDoc(d: Doc) {
    try { await api.retryDoc(d.id); } catch {}
    await load(false);
  }
  async function cancelDoc(d: Doc) {
    try { await api.cancelDoc(d.id); } catch {}
    await load(false);
  }

  // ================= DOC INSPECTOR =================
  let dDoc = $state<any>(null);
  let dStats = $state<any>(null);
  let dOutline = $state<any[]>([]);
  let dPages = $state<any[]>([]);
  let tab = $state<'overview' | 'pages' | 'outline' | 'fulltext'>('overview');
  // ---- full-bleed reader ----
  let view = $state<'read' | 'outline' | 'text' | 'split'>('read');
  let zoomPct = $state(100);
  let activePage = $state(1);
  let readEl = $state<HTMLElement>();
  const VIEWS = [['read', 'Read'], ['outline', 'Outline'], ['text', 'Text'], ['split', 'Split']] as const;
  function scrollToReadPage(n: number) {
    view = 'read';
    requestAnimationFrame(() => {
      (readEl?.querySelector(`#rp-${n}`) as HTMLElement | null)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
  }
  function onReadScroll() {
    if (!readEl || !dPages.length) return;
    const mid = readEl.scrollTop + readEl.clientHeight / 2;
    let best = dPages[0]?.page_no ?? 1, bd = Infinity;
    for (const p of dPages) {
      const el = readEl.querySelector(`#rp-${p.page_no}`) as HTMLElement | null;
      if (!el) continue;
      const c = el.offsetTop + el.offsetHeight / 2;
      const dd = Math.abs(c - mid);
      if (dd < bd) { bd = dd; best = p.page_no; }
    }
    if (best !== activePage) { activePage = best; syncReaderUrl(); }
  }
  let selPage = $state<any>(null);
  let selText = $state<any>(null);
  let pageView = $state<'clean' | 'text' | 'vision'>('clean');
  let zoom = $state(''); let zoomCap = $state('');
  let fullText = $state<any[]>([]);
  let ftView = $state<'clean' | 'text' | 'vision'>('clean');
  let ftQuery = $state(''); let ftActive = $state(1); let matchIdx = $state(0);
  let rightCol: HTMLElement | undefined = $state();

  let dMeta = $derived(dDoc ? parseDocName(dDoc.name) : { title: '', code: '', category: '', raw: '' });

  // Build a reader URL on the CURRENT path (so embedded /workspace stays in /workspace,
  // standalone /brain stays in /brain) — fixes "Back lands on the wrong page".
  function readerHref(extra: Record<string, string | number | null>) {
    const u = new URL(get(page).url);
    for (const [k, v] of Object.entries(extra)) {
      if (v == null) u.searchParams.delete(k);
      else u.searchParams.set(k, String(v));
    }
    return u;
  }

  // Opening a doc = navigating to ?doc=ID (URL is the source of truth → refresh-safe,
  // real Back button). selectDoc just routes; the $effect below opens the reader.
  function selectDoc(d: Doc) {
    if (d.status && d.status !== 'ready') return;   // queued/processing/failed have no pages
    goto(readerHref({ doc: d.id, view: null, pg: null }), { keepFocus: true, noScroll: true });
  }
  function closeReader() { goto(readerHref({ doc: null, view: null, pg: null }), { keepFocus: true, noScroll: true }); }
  // push view / page into the URL (replaceState — no history spam) so refresh restores them
  function syncReaderUrl() {
    if (sel?.type !== 'doc') return;
    const u = new URL(get(page).url);
    u.searchParams.set('doc', String(sel.id));
    u.searchParams.set('view', view);
    if (view === 'read' && activePage > 1) u.searchParams.set('pg', String(activePage));
    else u.searchParams.delete('pg');
    goto(u, { replaceState: true, keepFocus: true, noScroll: true });
  }
  function setView(v: 'read' | 'outline' | 'text' | 'split') { view = v; syncReaderUrl(); }

  async function openDoc(id: number) {
    sel = { type: 'doc', id };
    tab = 'overview'; view = 'read'; activePage = 1; zoomPct = 100; selPage = null; selText = null; fullText = []; ftQuery = ''; ftActive = 1;
    dDoc = null; dStats = null; dOutline = []; dPages = [];
    const r = await api.docDetail(id);
    if (sel?.type === 'doc' && sel.id === id) { dDoc = r.doc; dStats = r.stats; dOutline = r.outline || []; }
    const pr = await api.docPages(id);
    if (sel?.type === 'doc' && sel.id === id) dPages = pr.pages || [];
    // restore view + scroll position from URL (refresh / deep-link)
    const sp = get(page).url.searchParams;
    const v = sp.get('view');
    if (v && ['read', 'outline', 'text', 'split'].includes(v)) view = v as any;
    const pg = +(sp.get('pg') || '1');
    if (pg > 1) scrollToReadPage(pg);
  }
  // URL → reader state (open/close). Single source of truth.
  $effect(() => {
    const id = $page.url.searchParams.get('doc');
    if (id) {
      const nid = +id;
      if (!(sel?.type === 'doc' && sel.id === nid)) openDoc(nid);
    } else if (sel?.type === 'doc') {
      sel = null;
    }
  });

  async function delDoc(d: Doc) {
    if (!confirm(`Delete "${parseDocName(d.name).title}"?`)) return;
    await api.deleteDoc(d.id);
    if (sel?.type === 'doc' && sel.id === d.id) closeReader();
    await load();
  }

  // ================= QUICK-LOOK PEEK (right drawer) =================
  let peek = $state<Doc | null>(null);
  let peekPages = $state<any[]>([]);
  let peekOutline = $state<any[]>([]);
  let peekDetail = $state<any>(null);
  let peekIdx = $state(0);
  let peekBusy = $state(false);
  let peekMeta = $derived(peek ? parseDocName(peek.name) : { title: '', code: '', category: '', raw: '' });
  async function openPeek(d: Doc) {
    if (d.status && d.status !== 'ready') return;          // not ready → nothing to preview
    peek = d; peekIdx = 0; peekPages = []; peekOutline = []; peekDetail = null;
    peekBusy = true;
    try {
      const pr = await api.docPages(d.id);
      if (peek?.id === d.id) peekPages = pr.pages || [];
      const r = await api.docDetail(d.id);
      if (peek?.id === d.id) { peekOutline = r.outline || []; peekDetail = r; }
    } finally { peekBusy = false; }
  }
  function closePeek() { peek = null; }
  function peekPrev() { if (peekIdx > 0) peekIdx--; }
  function peekNext() { if (peekIdx < peekPages.length - 1) peekIdx++; }
  function openFull() { if (peek) { const id = peek.id; peek = null; goto(readerHref({ doc: id, view: null, pg: null }), { keepFocus: true, noScroll: true }); } }
  function openFullAt(page_no: number) { if (!peek) return; const id = peek.id; peek = null; goto(readerHref({ doc: id, pg: page_no }), { keepFocus: true, noScroll: true }); }
  function peekAsk() { if (peek) goto('/?ask=' + encodeURIComponent(`About "${parseDocName(peek.name).title}": `)); }
  function peekZoom() { if (peek && peekPages[peekIdx]) { zoom = api.pageImg(peekPages[peekIdx].page_id); zoomCap = `${peekMeta.title} — page ${peekPages[peekIdx].page_no}`; } }

  async function selectPage(p: any) { selPage = p; selText = await api.pageText(p.page_id); }
  function openZoom() { if (selPage) { zoom = api.pageImg(selPage.page_id); zoomCap = `${dMeta.title} — page ${selPage.page_no}`; } }
  let pageBody = $derived.by(() => {
    if (!selText) return '';
    if (pageView === 'text') return selText.text_layer || '—';
    if (pageView === 'vision') return selText.vision_text || '—';
    return cleanText(((selText.text_layer || '') + '\n' + (selText.vision_text || '')).trim()) || '—';
  });
  async function loadFullText() { if (fullText.length || !dDoc) return; fullText = (await api.docText(dDoc.id)).pages || []; }
  $effect(() => { if (view === 'text' || view === 'split') loadFullText(); });
  function jumpToPage(page_no: number) { scrollToReadPage(page_no); }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && actModal) { e.preventDefault(); actModal = null; return; }
    if (e.key === 'Escape' && factModal) { e.preventDefault(); factModal = null; return; }
    if (e.key === 'Escape' && gpanel) { e.preventDefault(); closePanel(); return; }
    // quick-look drawer: Esc closes, ← → flip pages
    if (peek) {
      if (e.key === 'Escape') { e.preventDefault(); closePeek(); return; }
      if (e.key === 'ArrowRight') { e.preventDefault(); peekNext(); return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); peekPrev(); return; }
      return;
    }
    if (sel?.type !== 'doc') return;
    const t = e.target as HTMLElement;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (e.key === 'Escape') { e.preventDefault(); closeReader(); return; }
    // Read view: j/k jump pages, + / − zoom
    if (view === 'read' && dPages.length) {
      if (e.key === 'j' || e.key === 'k') {
        e.preventDefault();
        const i = dPages.findIndex((x) => x.page_no === activePage);
        const ni = e.key === 'j' ? Math.min(dPages.length - 1, i + 1) : Math.max(0, i - 1);
        scrollToReadPage(dPages[ni].page_no);
      } else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomPct = Math.min(200, zoomPct + 10); }
      else if (e.key === '-') { e.preventDefault(); zoomPct = Math.max(50, zoomPct - 10); }
    }
  }

  function escapeHtml(s: string): string { return (s || '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] as string)); }
  function bodyFor(p: any): string {
    if (ftView === 'text') return p.text_layer || '';
    if (ftView === 'vision') return p.vision_text || '';
    return cleanText(((p.text_layer || '') + '\n' + (p.vision_text || '')).trim());
  }
  let totalMatches = $derived.by(() => {
    const nd = ftQuery.trim().toLowerCase();
    if (!nd) return 0;
    let n = 0;
    for (const p of fullText) { const hay = bodyFor(p).toLowerCase(); let idx = hay.indexOf(nd); while (idx >= 0) { n++; idx = hay.indexOf(nd, idx + nd.length); } }
    return n;
  });
  $effect(() => { if (matchIdx > totalMatches) matchIdx = totalMatches; if (totalMatches && matchIdx === 0) matchIdx = 1; if (!totalMatches) matchIdx = 0; });
  let gMatch = 0;
  function renderPage(p: any): string {
    const esc = escapeHtml(bodyFor(p));
    const nl = escapeHtml(ftQuery.trim()).toLowerCase();
    if (!nl) return esc;
    const lower = esc.toLowerCase();
    let out = ''; let from = 0; let idx = lower.indexOf(nl);
    while (idx >= 0) { gMatch++; out += esc.slice(from, idx) + `<mark class="ftm" data-mi="${gMatch}" id="ftm-${gMatch}">` + esc.slice(idx, idx + nl.length) + '</mark>'; from = idx + nl.length; idx = lower.indexOf(nl, from); }
    return out + esc.slice(from);
  }
  let renderedPages = $derived.by(() => { gMatch = 0; return fullText.map((p) => ({ page_no: p.page_no, html: renderPage(p) })); });
  function scrollToMatch(n: number) {
    if (!n) return;
    requestAnimationFrame(() => {
      const el = document.getElementById(`ftm-${n}`) as HTMLElement | null;
      if (el) { document.querySelectorAll('mark.cur').forEach((m) => m.classList.remove('cur')); el.classList.add('cur'); el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    });
  }
  function nextMatch() { if (!totalMatches) return; matchIdx = matchIdx >= totalMatches ? 1 : matchIdx + 1; scrollToMatch(matchIdx); }
  function prevMatch() { if (!totalMatches) return; matchIdx = matchIdx <= 1 ? totalMatches : matchIdx - 1; scrollToMatch(matchIdx); }
  $effect(() => { if (ftQuery.trim() && totalMatches) scrollToMatch(matchIdx || 1); });
  function jumpFt(n: number) { ftActive = n; requestAnimationFrame(() => { document.getElementById(`pg-${n}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }); }); }
  function downloadTxt() {
    const body = fullText.map((p) => `── Page ${p.page_no} ──\n${bodyFor(p)}`).join('\n\n');
    const blob = new Blob([`${dMeta.title}\n\n${body}`], { type: 'text/plain' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${dMeta.title || 'document'}.txt`; a.click(); URL.revokeObjectURL(a.href);
  }
  function askAbout() { goto('/?ask=' + encodeURIComponent(`About "${dMeta.title}": `)); }

  let weakSet = $derived(new Set<number>(dStats?.weak_pages || []));
  let pageCharMap = $derived.by(() => { const m = new Map<number, any>(); for (const c of (dStats?.page_chars || [])) m.set(c.page_no, c); return m; });
  let dFullyExtracted = $derived(dStats && dStats.pages > 0 && dStats.vision_pages === dStats.pages);
  let dVisionPct = $derived(dStats && dStats.pages ? Math.round((dStats.vision_pages / dStats.pages) * 100) : 0);
  let dTextPct = $derived(dStats && dStats.pages ? Math.round((dStats.text_pages / dStats.pages) * 100) : 0);

  // ================= FACT INSPECTOR (popup modal) =================
  let factModal = $state<Fact | null>(null);
  let selFact = $derived(factModal ? facts.find((f) => f.id === factModal!.id) || factModal : null);
  // B1: rich fact detail fetched from /api/facts/{id}
  let factDetail = $state<any>(null);
  let factDetailLoading = $state(false);
  // B1: inline edit state
  let factEditMode = $state(false);
  let factEditKey = $state('');
  let factEditVal = $state('');
  async function fetchFactDetail(id: number) {
    factDetail = null; factDetailLoading = true;
    try {
      const r = await fetch(`${api.base}/facts/${id}`, { headers: authHeaders() });
      if (r.ok) factDetail = await r.json();
    } catch {}
    finally { factDetailLoading = false; }
  }
  function selectFact(f: Fact) {
    factModal = f; factDetail = null; factEditMode = false; factEditKey = f.key || ''; factEditVal = f.value || '';
    fetchFactDetail(f.id);
  }
  function closeFact() { factModal = null; factDetail = null; factEditMode = false; }
  async function saveFactEdit(f: Fact) {
    // Only wire if api exposes an update method; otherwise close edit mode as a no-op
    if (typeof (api as any).updateMemory === 'function') {
      try { await (api as any).updateMemory(f.id, factEditKey.trim(), factEditVal.trim()); await load(); } catch {}
    }
    factEditMode = false;
  }
  async function delFact(f: Fact) {
    if (!confirm('Delete this fact?')) return;
    await api.deleteMemory(f.id);
    if (factModal?.id === f.id) factModal = null;
    await load();
  }
  async function approveFact(f: Fact) { await api.approveFact(f.id); factModal = null; await load(); }
  async function rejectFact(f: Fact) { await api.rejectFact(f.id); factModal = null; await load(); }

  // ── Q&A bank actions ──
  let qaBusy = $state<number | null>(null);
  async function approveQa(p: QAPair) { qaBusy = p.id; try { await api.approveQa(p.id); await load(false); } catch {} finally { qaBusy = null; } }
  async function rejectQa(p: QAPair) { qaBusy = p.id; try { await api.rejectQa(p.id); await load(false); } catch {} finally { qaBusy = null; } }
  async function delQa(p: QAPair) {
    if (!confirm('Delete this Q&A pair?')) return;
    qaBusy = p.id; try { await api.deleteQa(p.id); await load(false); } catch {} finally { qaBusy = null; }
  }
  // inline rewrite — fix a demoted/weak pair instead of only approve/reject
  let qaEditId = $state<number | null>(null);
  let qaEditQ = $state('');
  let qaEditA = $state('');
  function startQaEdit(p: QAPair) { qaEditId = p.id; qaEditQ = p.question; qaEditA = p.answer; }
  function cancelQaEdit() { qaEditId = null; qaEditQ = ''; qaEditA = ''; }
  async function saveQaEdit(p: QAPair) {
    if (!qaEditQ.trim() || !qaEditA.trim()) return;
    qaBusy = p.id;
    try { await api.updateQa(p.id, qaEditQ.trim(), qaEditA.trim()); qaEditId = null; await load(false); }
    catch {} finally { qaBusy = null; }
  }
  let qaShown = $derived(qaFilter === 'all' ? qaPairs : qaPairs.filter((p) => (p.status || 'pending') === qaFilter));
  let pendingFacts = $derived(facts.filter((f) => f.status === 'pending'));
  let activeFacts = $derived(facts.filter((f) => f.status !== 'pending' && f.status !== 'rejected'));

  // ================= COVERAGE AUDIT =================
  let audit = $state<any>(null);
  let auditBusy = $state(false);
  let digest = $state<any>(null);
  let digestBusy = $state(false);
  let activity = $state<any[]>([]);
  let factSort = $state<'used' | 'recent' | 'unused'>('used');
  let actModal = $state<any | null>(null);
  function notifIcon(kind: string) { return ({ ingest: 'IN', upload: 'UP', learn: 'AI', memory: 'FX', digest: 'DG', signup: 'NU', user: 'NU' } as any)[kind] || '•'; }
  function notifLabel(kind: string) { return ({ ingest: 'Document ingested', upload: 'Document queued', learn: 'Learned in chat', memory: 'Fact change', digest: 'Weekly digest', signup: 'New user', user: 'New user' } as any)[kind] || 'Activity'; }
  function notifTint(kind: string) { return ({ ingest: '#eef3ef', upload: '#f0efed', learn: '#f3f3f1', memory: '#f3f3f1', digest: '#f3f3f1', signup: '#f5eef3', user: '#f5eef3' } as any)[kind] || '#f0efed'; }
  function fullTime(iso: string) { if (!iso) return ''; try { const d = new Date(iso); return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); } catch { return iso; } }
  async function loadAudit() {
    auditBusy = true;
    try {
      audit = await api.auditCoverage(30);
      try { digest = (await api.digestLatest()).digest; } catch {}
      try { activity = (await api.notifications('all')).items || []; } catch {}
    } finally { auditBusy = false; }
  }
  async function runDigest() {
    digestBusy = true;
    try { await api.digestRun(); digest = (await api.digestLatest()).digest; }
    catch (e) { alert('Digest needs admin.'); }
    finally { digestBusy = false; }
  }
  $effect(() => { if (railTab === 'audit' && !audit && !auditBusy) loadAudit(); });

  // ---- dislike → train loop (Audit downvotes) ----
  // For a downvote that carries a correction, either approve the backend-created
  // pending fact (reuses approveFact / POST /memory/{id}/approve) or — when no fact
  // is linked — teach the correction as a new fact (reuses api.teach / POST /memory).
  let teachBusyId = $state<number | null>(null);          // row id currently saving
  let dvTaught = $state<Record<string | number, boolean>>({});  // local "✓ taught" flags
  async function teachFromDownvote(d: any) {
    const corr = (d.correction || d.note || '').trim();
    if (!corr) return;
    const rid = d.id ?? -1;
    teachBusyId = rid;
    try {
      await api.teach(corr, d.question ? `correction: ${String(d.question).slice(0, 60)}` : undefined);
      dvTaught = { ...dvTaught, [d.id ?? corr]: true };
      await load(false);
    } catch (e) { alert('Could not teach this correction.'); }
    finally { teachBusyId = null; }
  }

  // teach composer
  let tKey = $state(''); let tVal = $state(''); let tBusy = $state(false);
  let teachOpen = $state(false);
  let folderInput: HTMLInputElement;
  function startTeach() { tKey = ''; tVal = ''; teachOpen = true; }
  // server-folder bulk import ("Import from server")
  let scanInfo = $state<{ exists: boolean; found: number; new: number; skipped: number; dir: string } | null>(null);
  let scanning = $state(false);
  let scanMsg = $state('');
  async function loadScan() { if (!isAdmin) return; try { scanInfo = await api.scanPreview(); } catch { scanInfo = null; } }
  async function doScan() {
    scanning = true; scanMsg = ''; jobsOpen = true;
    try {
      const r = await api.scanImport();
      scanMsg = r.queued ? `Queued ${r.queued} new · ${r.skipped} already present` : `Nothing new — ${r.skipped} already present`;
      await load(false);
      await loadScan();
    } catch (e: any) { scanMsg = 'Import failed: ' + (e.message || ''); }
    scanning = false;
  }
  // S3 bulk import ("Import from S3")
  async function doS3Import() {
    scanning = true; scanMsg = ''; jobsOpen = true;
    try {
      const r = await api.s3Import();
      scanMsg = r.queued ? `Queued ${r.queued} new from S3 · ${r.skipped} already present` : `Nothing new in S3 — ${r.skipped} already present`;
      await load(false);
    } catch (e: any) { scanMsg = 'S3 import failed: ' + (e.message || ''); }
    scanning = false;
  }
  // Workspace top-bar menu bridges in via these signals (skip the initial value)
  let _filesSeen = $state<File[] | null>(null);
  let _scanSeen = $state<number | null>(null);
  let _s3Seen = $state<number | null>(null);
  let _teachSeen = $state<number | null>(null);
  $effect(() => { const v = $brainFilesSignal; if (v && v !== _filesSeen) { _filesSeen = v; handleFiles(v); brainFilesSignal.set(null); } });
  $effect(() => { const v = $brainScanSignal; if (_scanSeen === null) { _scanSeen = v; return; } if (v !== _scanSeen) { _scanSeen = v; doScan(); } });
  $effect(() => { const v = $brainS3Signal; if (_s3Seen === null) { _s3Seen = v; return; } if (v !== _s3Seen) { _s3Seen = v; doS3Import(); } });
  $effect(() => { const v = $brainTeachSignal; if (_teachSeen === null) { _teachSeen = v; return; } if (v !== _teachSeen) { _teachSeen = v; startTeach(); } });
  function pickedFiles(files: FileList | File[] | null) {
    // keep the modal open so progress renders inside it (floating card takes over only when closed)
    if (files && files.length) handleFiles(files);
  }
  async function teach() {
    if (!tVal.trim()) return;
    tBusy = true;
    const r = await api.teach(tVal.trim(), tKey.trim() || undefined);
    tVal = ''; tKey = '';
    await load();
    tBusy = false;
    teachOpen = false;
    const newId = r?.id ?? r?.memory?.id;
    if (newId) { _railTab = 'brain'; loadFeed(); const nf = facts.find((f) => f.id === newId); if (nf) factModal = nf; }
  }

  // ================= KNOWLEDGE GRAPH (Obsidian-style, echarts force) =================
  let graphEl = $state<HTMLDivElement>();
  let graphChart: any = null;                  // echarts instance (untyped — lazy-loaded)
  let graphEcharts: any = null;                // cached echarts module
  let graphLoading = $state(false);
  let graphErr = $state('');
  let graphCounts = $state({ nodes: 0, links: 0 });
  let graphFilters = $state({ docs: true, pages: true, facts: true });
  let graphNodeMap = new Map<string, any>();   // node id → raw node (for click routing)
  let graphInitFlight = false;                 // guard double-init
  let graphData: { nodes: any[]; links: any[] } = { nodes: [], links: [] };  // last loaded (reused on layout switch)

  // ---- chart-type switcher (graph layouts + hierarchical chart types) ----
  // 'force'|'circular'|'cluster' are echarts GRAPH layouts; 'tree'|'sunburst'|
  // 'treemap'|'sankey' are different series types built client-side from graphData.
  type GLayout = 'force' | 'circular' | 'cluster' | 'tree' | 'sunburst' | 'treemap' | 'sankey';
  const GRAPH_LAYOUTS: GLayout[] = ['force', 'circular', 'cluster'];
  let graphLayout = $state<GLayout>('force');
  let labelsExpanded = $state(false);          // zoom-adaptive: reveal page labels when zoomed in
  const ALL_GLAYOUTS: GLayout[] = ['force', 'circular', 'cluster', 'tree', 'sunburst', 'treemap', 'sankey'];
  $effect(() => { try { const v = localStorage.getItem('aria_graph_layout'); if (v && (ALL_GLAYOUTS as string[]).includes(v)) graphLayout = v as GLayout; } catch {} });
  function isGraphType(l: GLayout) { return GRAPH_LAYOUTS.includes(l); }
  function setGraphLayout(l: GLayout) {
    const prev = graphLayout;
    graphLayout = l;
    labelsExpanded = false;
    try { localStorage.setItem('aria_graph_layout', l); } catch {}
    if (graphChart && graphData.nodes.length) {
      // moving between graph<->hierarchy types or any switch = full replace (notMerge)
      // so leftover series config (graph categories, force layout, etc.) never bleeds.
      rebindClick(prev, l);
      graphChart.setOption(buildOption(graphData), { notMerge: true });
    }
  }

  // ---- per-document stable hue (Obsidian-by-folder coloring) ----
  function docHue(doc_id: number) { return ((Math.abs(doc_id) * 47) % 360); }
  function docColor(doc_id: number) { return `hsl(${docHue(doc_id)},58%,58%)`; }      // bright = doc hub
  function pageColor(doc_id: number) { return `hsl(${docHue(doc_id)},42%,72%)`; }      // muted = its pages
  const FACT_COLOR = '#e0569b';
  function nodeColor(n: any) {
    if (n.type === 'fact') return FACT_COLOR;
    const did = n.doc_id ?? (n.type === 'doc' ? +String(n.id).replace(/^d/, '') : null);
    if (did != null && !Number.isNaN(did)) return n.type === 'doc' ? docColor(did) : pageColor(did);
    return n.type === 'doc' ? '#a855c7' : '#9aa0a6';
  }

  // ---- left detail panel (node click → fetch detail, no navigation) ----
  let gpanel = $state<any>(null);              // node detail payload (null = closed)
  let gpanelBusy = $state(false);
  let gpanelErr = $state('');
  let gpanelSeq = 0;
  // relation-kind → plain-English glyph (helps a non-tech user read WHY things connect)
  function relIcon(kind: string) {
    return ({ contains: '·', sequence: '→', cocite: '·', similar: '·', cite: '·' } as Record<string, string>)[kind] || '•';
  }
  // colored dot per item type (reused from the old flat-link list)
  function nbDot(t: string) { return t === 'doc' ? '#a855c7' : t === 'fact' ? '#e0569b' : '#9aa0a6'; }
  async function openNodePanel(nsid: string) {
    if (!nsid) return;
    const seq = ++gpanelSeq;
    gpanelBusy = true; gpanelErr = '';
    try {
      const u = new URL(`${api.base}/brain/node`, location.origin);
      u.searchParams.set('id', nsid);
      const r = await fetch(u, { headers: authHeaders() });
      if (!r.ok) throw new Error('node detail failed');
      const data = await r.json();
      if (seq === gpanelSeq) { gpanel = data; focusGraphNode(nsid); }
    } catch (e: any) {
      if (seq === gpanelSeq) { gpanelErr = e?.message || 'Could not load node'; gpanel = gpanel || { id: nsid, title: 'Error' }; }
    } finally {
      if (seq === gpanelSeq) gpanelBusy = false;
    }
  }
  function closePanel() { gpanel = null; gpanelSeq++; }
  // highlight/focus a node's adjacency in the live chart (best-effort)
  function focusGraphNode(nsid: string) {
    if (!graphChart) return;
    try {
      graphChart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      graphChart.dispatchAction({ type: 'focusNodeAdjacency', seriesIndex: 0, dataIndex: undefined, data: { id: nsid } });
      graphChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataInfo: { id: nsid } });
    } catch {}
  }
  // explicit navigation buttons (user choice only — never on plain node click)
  function panelOpenReader() {
    const p = gpanel; if (!p) return;
    const docId = p.doc_id;
    if (!docId) return;
    const deep = p.page_no && p.page_no > 1;
    closePanel();
    goto(readerHref(deep ? { doc: docId, view: 'read', pg: p.page_no } : { doc: docId, view: null, pg: null }), { keepFocus: true, noScroll: true });
  }
  function panelOpenFact() {
    const p = gpanel; if (!p) return;
    const fid = p.id != null ? +String(p.id).replace(/^f/, '') : NaN;
    const f = facts.find((x) => x.id === fid);
    closePanel();
    if (f) selectFact(f);
    else selectFact({ id: fid, value: p.summary || p.title } as Fact);
  }

  const GTYPES = ['doc', 'page', 'fact'] as const;
  const GCAT = [
    { name: 'doc', itemStyle: { color: '#a855c7' } },
    { name: 'page', itemStyle: { color: '#9aa0a6' } },
    { name: 'fact', itemStyle: { color: '#e0569b' } },
  ];
  function gCatIndex(t: string) { const i = GTYPES.indexOf(t as any); return i < 0 ? 1 : i; }
  function gSymbolSize(val: number) { return Math.max(4, Math.min(40, 6 + Math.sqrt(Math.max(0, val || 1)) * 3)); }

  async function fetchGraph() {
    const u = new URL(`${api.base}/brain/graph`, location.origin);
    u.searchParams.set('docs', graphFilters.docs ? '1' : '0');
    u.searchParams.set('pages', graphFilters.pages ? '1' : '0');
    u.searchParams.set('facts', graphFilters.facts ? '1' : '0');
    u.searchParams.set('limit', '400');
    const r = await fetch(u, { headers: authHeaders() });
    if (!r.ok) throw new Error('graph failed');
    return await r.json();
  }

  function graphOption(data: { nodes: any[]; links: any[] }) {
    graphNodeMap = new Map(data.nodes.map((n) => [n.id, n]));
    const ndl = needle;   // brain search box, lowercased
    // node colors/sizes computed per-node from doc_id (color-by-document)
    let nodes = data.nodes.map((n) => {
      const matchHit = ndl ? String(n.label || '').toLowerCase().includes(ndl) : false;
      const dim = ndl && !matchHit;
      const baseSize = gSymbolSize(n.val);
      return {
        id: n.id,
        name: n.label,
        value: n.val,
        symbolSize: matchHit ? baseSize * 1.5 : baseSize,
        itemStyle: { color: nodeColor(n), opacity: dim ? 0.12 : 1 },
        // labels: docs always; pages only when zoomed in (labelsExpanded); facts off by default.
        // search active → only matches show labels, everything else off.
        label: {
          show: dim ? false : (matchHit || n.type === 'doc' || (n.type === 'page' && labelsExpanded)),
          color: '#ddd', fontSize: 11,
        },
        _type: n.type,
      };
    });

    // Clustered-by-type: pin nodes into 3 type clusters (layout:'none' + computed x/y).
    if (graphLayout === 'cluster') {
      const centers = [ { x: 250, y: 240 }, { x: 700, y: 240 }, { x: 1150, y: 240 } ]; // doc / page / fact
      const counts = [0, 0, 0];
      nodes = nodes.map((nd) => {
        const c = gCatIndex(nd._type);
        const i = counts[c]++;
        const ring = 60 + Math.floor(i / 12) * 70;            // expanding rings per cluster
        const ang = (i % 12) * (Math.PI * 2 / 12) + c;         // spread around the cluster center
        return { ...nd, x: centers[c].x + Math.cos(ang) * ring, y: centers[c].y + Math.sin(ang) * ring };
      });
    }

    const layoutCfg: any =
      graphLayout === 'circular' ? { layout: 'circular', circular: { rotateLabel: true } } :
      graphLayout === 'cluster'  ? { layout: 'none' } :
                                   { layout: 'force', force: { repulsion: 90, edgeLength: [20, 120], gravity: 0.1, friction: 0.6 } };

    // edges: thin/low-opacity. sequence = same-doc hue + slightly opaque; similar = doc-doc, a touch thicker.
    const links = (data.links || []).map((l) => {
      const kind = l.kind || 'contains';
      const srcN = graphNodeMap.get(l.source);
      const did = srcN?.doc_id ?? (srcN?.type === 'doc' ? +String(srcN.id).replace(/^d/, '') : null);
      let ls: any = { color: '#555', opacity: 0.22, width: 0.6, curveness: graphLayout === 'circular' ? 0.18 : 0 };
      if (kind === 'sequence' && did != null && !Number.isNaN(did)) ls = { color: pageColor(did), opacity: 0.4, width: 0.8, curveness: ls.curveness };
      else if (kind === 'similar') ls = { color: '#7a86b8', opacity: 0.35, width: 1.4, curveness: 0.2 };
      else if (kind === 'cocite' || kind === 'cite') ls = { color: '#666', opacity: 0.28, width: 0.7, curveness: ls.curveness };
      return { source: l.source, target: l.target, lineStyle: ls };
    });

    return {
      backgroundColor: 'transparent',
      tooltip: { show: false },
      legend: undefined,
      series: [{
        type: 'graph',
        ...layoutCfg,
        roam: true,
        draggable: true,
        data: nodes,
        links,
        label: { show: false, color: '#ddd', fontSize: 11 },
        emphasis: { focus: 'adjacency', label: { show: true } },
        lineStyle: { color: '#555', opacity: 0.22, width: 0.6 },
      }],
    };
  }

  // ===== hierarchy built CLIENT-SIDE from graphData (nodes + 'contains' page→doc links) =====
  // Returns { docs:[{node, pages:[node]}], byId } honoring the Docs/Pages filter toggles.
  function buildHierarchy() {
    const byId = new Map<string, any>(graphData.nodes.map((n) => [n.id, n]));
    const docNodes = graphData.nodes.filter((n) => n.type === 'doc');
    const pageOf = new Map<string, any[]>();   // doc id → its page nodes
    for (const n of graphData.nodes) {
      if (n.type !== 'page') continue;
      const did = n.doc_id != null ? `d${n.doc_id}` : null;
      if (!did) continue;
      if (!pageOf.has(did)) pageOf.set(did, []);
      pageOf.get(did)!.push(n);
    }
    // contains links can also wire page→doc when doc_id missing
    for (const l of graphData.links || []) {
      if ((l.kind || 'contains') !== 'contains') continue;
      const a = byId.get(l.source), b = byId.get(l.target);
      const doc = a?.type === 'doc' ? a : b?.type === 'doc' ? b : null;
      const pg = a?.type === 'page' ? a : b?.type === 'page' ? b : null;
      if (doc && pg) {
        if (!pageOf.has(doc.id)) pageOf.set(doc.id, []);
        const arr = pageOf.get(doc.id)!;
        if (!arr.some((x) => x.id === pg.id)) arr.push(pg);
      }
    }
    const docs = docNodes.map((d) => {
      const did = +String(d.id).replace(/^d/, '');
      const pages = (graphFilters.pages ? (pageOf.get(d.id) || []) : []).slice().sort((a, b) => (a.page_no ?? 0) - (b.page_no ?? 0));
      return { node: d, did, pages };
    });
    return { docs, byId };
  }

  function treeOption() {
    const { docs } = buildHierarchy();
    const children = docs.map((d) => ({
      name: parseDocName(d.node.label).title || d.node.label,
      nsid: d.node.id,
      value: d.node.val || d.pages.length || 1,
      itemStyle: { color: docColor(d.did) },
      children: d.pages.map((p) => ({
        name: p.label || `p.${p.page_no}`, nsid: p.id, value: p.val || 1,
        itemStyle: { color: pageColor(d.did) },
      })),
    }));
    const tree = { name: 'Brain', nsid: '', itemStyle: { color: '#cfcfcf' }, children };
    const leafCount = docs.reduce((s, d) => s + d.pages.length, 0);
    return {
      backgroundColor: 'transparent', tooltip: { show: true, formatter: (p: any) => p.name },
      series: [{
        type: 'tree', layout: 'radial', data: [tree], roam: true,
        symbol: 'circle', symbolSize: 7, initialTreeDepth: 2,
        label: { show: leafCount < 90, color: '#cfcfcf', fontSize: 10, position: 'right' },
        leaves: { label: { show: leafCount < 60, color: '#9aa0a6', fontSize: 9 } },
        lineStyle: { color: '#444', width: 0.8, curveness: 0.5 },
        emphasis: { focus: 'descendant' }, expandAndCollapse: true,
      }],
    };
  }

  function sunburstOption() {
    const { docs } = buildHierarchy();
    const data = docs.map((d) => ({
      name: parseDocName(d.node.label).title || d.node.label,
      nsid: d.node.id,
      value: d.node.used || d.node.val || d.pages.length || 1,
      itemStyle: { color: docColor(d.did) },
      children: d.pages.map((p) => ({
        name: p.label || `p.${p.page_no}`, nsid: p.id, value: p.val || 1,
        itemStyle: { color: pageColor(d.did) },
      })),
    }));
    return {
      backgroundColor: 'transparent', tooltip: { show: true, formatter: (p: any) => p.name },
      series: [{
        type: 'sunburst', data, radius: ['12%', '94%'], nodeClick: 'rootToNode',
        label: { rotate: 'radial', color: '#1a1a1a', fontSize: 10, minAngle: 6 },
        itemStyle: { borderColor: '#1a1a1a', borderWidth: 1 },
        emphasis: { focus: 'ancestor' },
        levels: [{}, { r0: '12%', r: '58%' }, { r0: '58%', r: '94%' }],
      }],
    };
  }

  function treemapOption() {
    const { docs } = buildHierarchy();
    const data = docs.map((d) => ({
      name: parseDocName(d.node.label).title || d.node.label,
      nsid: d.node.id,
      value: d.node.used || d.pages.length || d.node.val || 1,
      itemStyle: { color: docColor(d.did) },
      children: d.pages.map((p) => ({
        name: p.label || `p.${p.page_no}`, nsid: p.id, value: p.val || 1,
        itemStyle: { color: pageColor(d.did) },
      })),
    }));
    return {
      backgroundColor: 'transparent', tooltip: { show: true, formatter: (p: any) => p.name },
      series: [{
        type: 'treemap', data, roam: true, leafDepth: 1, nodeClick: 'zoomToNode',
        label: { color: '#fff', fontSize: 11, overflow: 'truncate' },
        upperLabel: { show: true, height: 22, color: '#fff', fontSize: 11 },
        itemStyle: { borderColor: '#1a1a1a', borderWidth: 2, gapWidth: 2 },
        breadcrumb: { show: true, itemStyle: { color: '#2d2d2d', textStyle: { color: '#ddd' } } },
      }],
    };
  }

  function sankeyOption() {
    const { docs } = buildHierarchy();
    const nodes: any[] = [];
    const links: any[] = [];
    const seen = new Set<string>();
    let hasCat = false;
    const addNode = (name: string, depth: number) => { if (!seen.has(name)) { seen.add(name); nodes.push({ name, depth }); } };
    for (const d of docs) {
      const docName = parseDocName(d.node.label).title || d.node.label;
      const cat = parseDocName(d.node.label).category || 'Uncategorized';
      if (cat && cat !== 'Uncategorized') hasCat = true;
      addNode(docName, 1);
      addNode(cat, 0);
      links.push({ source: cat, target: docName, value: Math.max(1, d.pages.length), lineStyle: { color: docColor(d.did), opacity: 0.35 } });
      const topPages = d.pages.slice().sort((a, b) => (b.val ?? 0) - (a.val ?? 0)).slice(0, 4);
      for (const p of topPages) {
        const pn = p.label || `${docName} p.${p.page_no}`;
        addNode(pn, 2);
        links.push({ source: docName, target: pn, value: Math.max(1, p.val || 1), lineStyle: { color: pageColor(d.did), opacity: 0.3 } });
      }
    }
    // category mostly empty → drop the category column (shift docs to depth 0)
    if (!hasCat) {
      for (const n of nodes) { if (n.depth === 0) n._drop = true; else n.depth -= 1; }
      const kept = nodes.filter((n) => !n._drop);
      const keptNames = new Set(kept.map((n) => n.name));
      const flatLinks = links.filter((l) => keptNames.has(l.source) && keptNames.has(l.target));
      sankeyNameMap = new Map(docs.flatMap((d) => {
        const docName = parseDocName(d.node.label).title || d.node.label;
        return [[docName, d.node.id] as [string, string], ...d.pages.map((p) => [(p.label || `${docName} p.${p.page_no}`), p.id] as [string, string])];
      }));
      return sankeyBuild(kept, flatLinks);
    }
    sankeyNameMap = new Map(docs.flatMap((d) => {
      const docName = parseDocName(d.node.label).title || d.node.label;
      return [[docName, d.node.id] as [string, string], ...d.pages.map((p) => [(p.label || `${docName} p.${p.page_no}`), p.id] as [string, string])];
    }));
    return sankeyBuild(nodes, links);
  }
  let sankeyNameMap = new Map<string, string>();   // sankey node name → real node id (for click→panel)
  function sankeyBuild(nodes: any[], links: any[]) {
    return {
      backgroundColor: 'transparent', tooltip: { show: true, trigger: 'item' },
      series: [{
        type: 'sankey', data: nodes, links, draggable: false,
        nodeWidth: 12, nodeGap: 8,
        label: { color: '#cfcfcf', fontSize: 10 },
        lineStyle: { curveness: 0.5 },
        itemStyle: { borderColor: '#1a1a1a' },
        emphasis: { focus: 'adjacency' },
      }],
    };
  }

  // dispatcher: pick the right option builder for the active chart type
  function buildOption(data: { nodes: any[]; links: any[] }) {
    if (graphLayout === 'tree') return treeOption();
    if (graphLayout === 'sunburst') return sunburstOption();
    if (graphLayout === 'treemap') return treemapOption();
    if (graphLayout === 'sankey') return sankeyOption();
    return graphOption(data);
  }

  // click routing differs per chart type → rebind the right handler on type switch
  function rebindClick(prev: GLayout, next: GLayout) {
    if (!graphChart) return;
    try { graphChart.off('click'); } catch {}
    try { graphChart.off('graphroam'); } catch {}
    graphChart.on('click', onChartClick);
    if (isGraphType(next)) graphChart.on('graphroam', onGraphRoam);
  }

  // node click → open the LEFT detail panel (do NOT navigate away). Resolves the
  // node id per chart type: graph → p.data.id; tree/sunburst/treemap → data.nsid; sankey → name lookup.
  function onChartClick(p: any) {
    let nsid: string | undefined;
    if (isGraphType(graphLayout)) nsid = p.data?.id;
    else if (graphLayout === 'sankey') nsid = p.dataType === 'node' ? sankeyNameMap.get(p.name) : undefined;
    else nsid = p.data?.nsid;   // tree / sunburst / treemap
    if (!nsid) return;          // synthetic root / category node → no-op
    openNodePanel(nsid);
  }

  // zoom-adaptive labels: when the user roams (zooms) the graph past a threshold,
  // reveal page labels. Throttled so it's cheap.
  let roamThrottle: ReturnType<typeof setTimeout> | null = null;
  function onGraphRoam() {
    if (roamThrottle) return;
    roamThrottle = setTimeout(() => {
      roamThrottle = null;
      if (!graphChart || !isGraphType(graphLayout)) return;
      let z = 1;
      try { z = graphChart.getOption()?.series?.[0]?.zoom ?? 1; } catch {}
      const next = z >= 1.7;
      if (next !== labelsExpanded) {
        labelsExpanded = next;
        try { graphChart.setOption(graphOption(graphData)); } catch {}
      }
    }, 180);
  }

  // wait for the container to have a real size before init (it mounts at 0×0
  // momentarily when the tab first appears) — rAF retry, then ResizeObserver fallback.
  function whenSized(el: HTMLElement, cb: () => void) {
    let tries = 0;
    const tick = () => {
      if (!el.isConnected) return;
      if (el.clientWidth > 0 && el.clientHeight > 0) { cb(); return; }
      if (tries++ < 30) { requestAnimationFrame(tick); return; }
      // give up on rAF → one-shot ResizeObserver
      try {
        const ro = new ResizeObserver(() => {
          if (el.clientWidth > 0 && el.clientHeight > 0) { ro.disconnect(); cb(); }
        });
        ro.observe(el);
      } catch { cb(); }
    };
    requestAnimationFrame(tick);
  }

  async function initGraph() {
    if (graphInitFlight || !graphEl) return;
    // self-heal: if a stale/disposed instance is around, drop it so we re-create cleanly
    if (graphChart) {
      const dead = (() => { try { return graphChart.isDisposed?.(); } catch { return true; } })();
      if (dead) graphChart = null;
      else { try { graphChart.resize(); } catch {} return; }   // alive → just make sure it's sized
    }
    graphInitFlight = true;
    graphLoading = true; graphErr = '';
    try {
      if (!graphEcharts) graphEcharts = await import('echarts');
      const ec = (graphEcharts as any).default ?? graphEcharts;
      const data = await fetchGraph();
      graphData = { nodes: data.nodes || [], links: data.links || [] };
      graphCounts = { nodes: data.nodes?.length || 0, links: data.links?.length || 0 };
      const el = graphEl;
      if (!el) return;
      // create only once the container actually has a size (else the canvas is 0×0)
      whenSized(el, () => {
        if (!el.isConnected) return;
        if (graphChart) { try { if (graphChart.isDisposed?.()) graphChart = null; } catch { graphChart = null; } }
        if (!graphChart) {
          graphChart = ec.init(el, null, { renderer: 'canvas' });
          graphChart.on('click', onChartClick);
          if (isGraphType(graphLayout)) graphChart.on('graphroam', onGraphRoam);
        }
        graphChart.setOption(buildOption(graphData), { notMerge: true });
        try { graphChart.resize(); } catch {}
      });
    } catch (e: any) {
      graphErr = e?.message || 'Could not load graph';
    } finally {
      graphLoading = false; graphInitFlight = false;
    }
  }

  async function refreshGraph() {
    if (!graphChart || (() => { try { return graphChart.isDisposed?.(); } catch { return true; } })()) {
      graphChart = null; await initGraph(); return;
    }
    graphLoading = true; graphErr = '';
    try {
      const data = await fetchGraph();
      graphData = { nodes: data.nodes || [], links: data.links || [] };
      graphCounts = { nodes: data.nodes?.length || 0, links: data.links?.length || 0 };
      graphChart.setOption(buildOption(graphData), { notMerge: true });
    } catch (e: any) {
      graphErr = e?.message || 'Could not load graph';
    } finally { graphLoading = false; }
  }

  // search-to-highlight: recompute the graph option on `q` change (graph types only)
  $effect(() => {
    needle;   // track
    if (graphChart && railTab === 'graph' && isGraphType(graphLayout) && graphData.nodes.length) {
      try { graphChart.setOption(graphOption(graphData)); } catch {}
    }
  });

  function disposeGraph() {
    if (graphChart) { try { graphChart.dispose(); } catch {} }
    graphChart = null;   // always null after dispose so a later tab-entry re-inits
  }

  function toggleGraphFilter(k: 'docs' | 'pages' | 'facts') {
    graphFilters = { ...graphFilters, [k]: !graphFilters[k] };
    refreshGraph();
  }

  // init on entering the Graph tab; dispose when leaving / on unmount.
  // Self-healing: always (re)init on entry — initGraph() drops any disposed instance
  // and re-creates, so returning to the tab ALWAYS renders the graph.
  $effect(() => {
    if (railTab === 'graph' && !sel && graphEl) {
      initGraph();
    } else if (railTab !== 'graph') {
      if (graphChart) disposeGraph();
      gpanel = null;
    }
    return () => { if (railTab !== 'graph') disposeGraph(); };
  });
  // resize handler (active only while the chart lives)
  $effect(() => {
    if (!graphChart) return;
    const onResize = () => { try { graphChart?.resize(); } catch {} };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  });
</script>

<svelte:window onkeydown={onKey} />
<Lightbox bind:src={zoom} caption={zoomCap} />

<div class="h-full flex" style="background:var(--cream)"
     role="region" ondragover={(e) => { e.preventDefault(); dragOver = true; }} ondragleave={() => (dragOver = false)} ondrop={onDrop}>

  <!-- ===== left sub-rail (hidden when embedded in Workspace) ===== -->
  {#if !embedded}
  <aside class="shrink-0 w-[200px] flex flex-col border-r px-2.5 py-4" style="background:var(--sand); border-color:#efefec">
    <div class="brail-grp">Brain</div>
    <nav class="space-y-0.5">
      <button class="brail {railTab === 'brain' && !sel ? 'on' : ''}" onclick={() => { _railTab = 'brain'; sel = null; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2a4.5 4.5 0 0 0-4.5 4.5c-1.2.5-2 1.7-2 3 0 .8.3 1.5.8 2-.5.5-.8 1.2-.8 2 0 1.6 1.3 3 3 3a3 3 0 0 0 3 3 2.5 2.5 0 0 0 2.5-2.5V4.5A2.5 2.5 0 0 0 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v14.5a2.5 2.5 0 0 0 2.5 2.5 3 3 0 0 0 3-3c1.7 0 3-1.4 3-3 0-.8-.3-1.5-.8-2 .5-.5.8-1.2.8-2 0-1.3-.8-2.5-2-3A4.5 4.5 0 0 0 14.5 2z"/></svg>
        Brain
        <span class="bn">{(stats?.docs ?? docs.length)}</span>{#if pending > 0}<span class="tabpill">+{pending}</span>{/if}
      </button>
      <button class="brail {railTab === 'graph' && !sel ? 'on' : ''}" onclick={() => { _railTab = 'graph'; sel = null; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7 7.5 10.5 16M17 7.5 13.5 16M7 6h10"/></svg>
        Graph
      </button>
      <button class="brail {railTab === 'audit' && !sel ? 'on' : ''}" onclick={() => { _railTab = 'audit'; sel = null; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        Audit
        <span class="bn" style={audit ? `color:${band(audit.score)}` : ''}>{audit ? audit.score : '—'}</span>
      </button>
    </nav>
    <div class="my-3 border-t" style="border-color:#efefec"></div>
    <div class="brail-grp">Library</div>
    <nav class="space-y-0.5">
      <button class="brail {railTab === 'brain' && !sel && feedType === 'doc' ? 'on' : ''}" onclick={() => { _railTab = 'brain'; sel = null; _feedType = 'doc'; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>
        Documents
      </button>
      <button class="brail {railTab === 'brain' && !sel && feedType === 'fact' ? 'on' : ''}" onclick={() => { _railTab = 'brain'; sel = null; _feedType = 'fact'; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg>
        Facts
      </button>
      <button class="brail {railTab === 'brain' && !sel && feedType === 'qa' ? 'on' : ''}" onclick={() => { _railTab = 'brain'; sel = null; _feedType = 'qa'; }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        Q&amp;A{#if qaCounts.pending > 0}<span class="tabpill">{qaCounts.pending}</span>{/if}
      </button>
    </nav>
    <div class="mt-auto px-2 pt-3 text-[11.5px]" style="color:var(--muted)">{(stats?.docs ?? docs.length)} documents</div>
  </aside>
  {/if}

  <!-- ===== content column ===== -->
  <div class="flex-1 min-w-0 flex flex-col overflow-hidden">

  <!-- ===== full-width header ===== -->
  <div class="shrink-0 px-7 pt-5 pb-4 border-b" style="border-color:var(--border)">
    <div class="flex items-center justify-between gap-4 mb-3">
      <div>
        <h1 class="serif text-[21px] font-medium leading-tight" style="color:var(--ink)">Agent Brain</h1>
        <p class="text-[13px] mt-0.5" style="color:var(--muted)">Everything Aria learned — uploaded runbooks/SOPs and facts you taught. Ask about any of it in Chat.</p>
      </div>
    </div>

    <!-- combined Brain + Audit: top-tabs when embedded in Workspace (Graph stays its own rail item) -->
    {#if embedded && showTabs && railTab !== 'graph'}
      <div class="bktabs">
        <button class="bktab" class:on={railTab === 'brain' && feedType === 'doc'} onclick={() => { _railTab = 'brain'; _feedType = 'doc'; sel = null; }}>Documents</button>
        <button class="bktab" class:on={railTab === 'brain' && feedType === 'fact'} onclick={() => { _railTab = 'brain'; _feedType = 'fact'; sel = null; }}>Facts</button>
        <button class="bktab" class:on={railTab === 'brain' && feedType === 'qa'} onclick={() => { _railTab = 'brain'; _feedType = 'qa'; sel = null; }}>Q&amp;A{#if qaCounts.pending > 0}<span class="bktab-badge" style="color:var(--clay)">{qaCounts.pending}</span>{/if}</button>
        {#if isAdmin}
          <button class="bktab" class:on={railTab === 'audit'} onclick={() => { _railTab = 'audit'; sel = null; }}>
            Health{#if audit}<span class="bktab-badge" style="color:{band(audit.score)}">{audit.score}</span>{/if}
          </button>
        {/if}
      </div>
    {/if}

    {#if railTab === 'brain' && !sel && feedLoading}
      <div class="flex items-center gap-1.5"><span class="text-[11.5px]" style="color:var(--muted)">searching…</span></div>
    {/if}

    <div class="flex items-center gap-3 flex-wrap">
      <div class="relative flex-1 min-w-[240px] max-w-xl">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
        <input bind:value={q} placeholder="Search the brain — documents and facts…"
          class="w-full h-10 rounded-[10px] border pl-9 pr-3 text-sm outline-none" style="border-color:var(--border); background:var(--paper)" />
      </div>
      {#if stats}
        <div class="flex items-center gap-x-2 gap-y-1 text-[12.5px] flex-wrap" style="color:#52504a">
          <span class="inline-flex items-center gap-1"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg><b style="color:var(--ink)">{stats.docs}</b> documents</span><span style="color:var(--border)">·</span>
          <span><b style="color:var(--ink)">{stats.pages}</b> pages</span><span style="color:var(--border)">·</span>
          <span style="color:#5fa463"><b>{visionPct}%</b> read</span><span style="color:var(--border)">·</span>
          <span class="inline-flex items-center gap-1"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg><b style="color:var(--ink)">{stats.facts ?? facts.length}</b> facts</span>
        </div>
      {/if}
    </div>

    <input bind:this={fileInput} type="file" multiple accept=".pdf,.png,.jpg,.jpeg" class="hidden"
      onchange={(e) => { pickedFiles(e.currentTarget.files); e.currentTarget.value = ''; }} />
    <input bind:this={folderInput} type="file" multiple webkitdirectory class="hidden"
      onchange={(e) => { pickedFiles(e.currentTarget.files); e.currentTarget.value = ''; }} />
    {#if uploads.length}
      <!-- upload ERRORS only — successful uploads show live status on their doc row -->
      <div class="mt-3 space-y-1">
        {#each uploads.slice(0, 4) as u}
          <div class="flex justify-between text-[12.5px] px-3 py-1.5 rounded-lg border"
            style={u.dup ? 'background:#fbf6ec; border-color:#e6d2a8' : 'background:#fdf3ef; border-color:#e6c4b8'}>
            <span class="truncate" style="color:#52504a">{u.name}</span><span class="shrink-0 ml-3" style="color:{u.dup ? '#a9742a' : '#cf6a4c'}">{u.status}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>


  <!-- ===== CONTENT (full width, white) ===== -->
  <section bind:this={rightCol} class="flex-1 min-w-0 overflow-y-auto" style="background:var(--paper)">
      {#if dragOver}
        <div class="m-6 rounded-2xl border-2 border-dashed p-10 text-center" style="border-color:var(--clay); background:#f3f3f1; color:var(--clay)">Drop to upload…</div>

      {:else if railTab === 'audit' && !sel}
        <!-- ===== COVERAGE AUDIT (full width) ===== -->
        <div class="px-7 py-6">
          <div class="flex items-start justify-between gap-4 mb-4">
            <div>
              <h2 class="serif text-[22px] font-medium" style="color:var(--ink)">Coverage audit</h2>
              <p class="text-[13px] mt-0.5" style="color:var(--muted)">How well Aria's knowledge covers what's asked — graded on the data she logs.{audit ? ` · last ${audit.window_days ?? 30} days · scored ${relTime(audit.generated_at)}` : ''}</p>
            </div>
            <button onclick={loadAudit} disabled={auditBusy} class="shrink-0 text-sm px-3.5 py-2 rounded-[9px] border disabled:opacity-50" style="border-color:var(--border); color:var(--clay); background:#fff">{auditBusy ? 'Scanning…' : '↻ Re-scan'}</button>
          </div>

          {#if !audit}
            <div class="panel mt-5 p-8 text-center text-[13px]" style="color:var(--muted)">{auditBusy ? 'Scanning your logs…' : 'No audit yet.'}</div>
          {:else}
            {@const sc = audit.score}
            {@const CIRC = 326.7}
            <!-- HERO: score ring + pillar bars + verdict -->
            <div class="panel p-6 grid gap-6 items-center" style="grid-template-columns:auto 1fr auto;">
              <div class="relative grid place-items-center" style="width:148px;height:148px">
                <svg width="148" height="148" viewBox="0 0 148 148" style="transform:rotate(-90deg)">
                  <circle cx="74" cy="74" r="52" fill="none" stroke="var(--line)" stroke-width="12"/>
                  <circle cx="74" cy="74" r="52" fill="none" stroke={band(sc)} stroke-width="12" stroke-linecap="round"
                    stroke-dasharray={CIRC} stroke-dashoffset={CIRC * (1 - sc / 100)} style="transition:stroke-dashoffset .6s"/>
                </svg>
                <div class="absolute text-center">
                  <div class="serif text-[34px] leading-none" style="color:var(--ink)">{sc}</div>
                  <div class="text-[11px]" style="color:var(--muted)">/ 100</div>
                </div>
              </div>
              <div class="space-y-3 min-w-0">
                {#each [['Context', audit.pillars.context], ['Coverage', audit.pillars.coverage], ['Freshness', audit.pillars.freshness], ['Signal', audit.pillars.signal]] as [label, pts]}
                  <div class="flex items-center gap-3">
                    <span class="text-[12px] w-[68px] shrink-0" style="color:var(--muted)">{label}</span>
                    <div class="flex-1 h-2 rounded-full overflow-hidden" style="background:var(--line)">
                      <div class="h-full rounded-full" style="width:{(pts / 25) * 100}%; background:{band((pts / 25) * 100)}; transition:width .6s"></div>
                    </div>
                    <span class="text-[12px] w-[44px] text-right shrink-0 tabular-nums" style="color:var(--ink)">{pts}<span style="color:var(--muted)">/25</span></span>
                  </div>
                {/each}
              </div>
              <div class="self-center text-right pl-5 border-l" style="border-color:var(--line); min-width:160px">
                <div class="serif text-[19px]" style="color:{band(sc)}">{scoreVerdict(sc)}</div>
                <div class="text-[12px] mt-1.5 leading-relaxed" style="color:var(--muted)">
                  {audit.stats.coverage_pct}% answers sourced<br>
                  {audit.stats.blind_questions} blind spot{audit.stats.blind_questions === 1 ? '' : 's'}<br>
                  {audit.stats.facts_active} active fact{audit.stats.facts_active === 1 ? '' : 's'}
                </div>
              </div>
            </div>

            <!-- KPI chips -->
            <div class="flex flex-wrap gap-2 mt-3">
              {#each [['Answers', audit.stats.answers], ['No source', audit.stats.answers_no_source], ['Coverage', audit.stats.coverage_pct + '%'], ['+ up', audit.stats.upvotes], ['- down', audit.stats.downvotes], ['Pending', audit.stats.facts_pending]] as [k, v]}
                <span class="text-[12px] px-2.5 py-1 rounded-lg" style="background:#fff;border:1px solid var(--border);color:var(--muted)">{k} <b style="color:var(--ink)">{v}</b></span>
              {/each}
            </div>

            <!-- weekly digest banner (cadence) -->
            <div class="panel mt-4 p-4 flex items-start justify-between gap-4" style="background:#f7ede7">
              <div class="min-w-0">
                <div class="text-[11px] font-semibold uppercase tracking-wide mb-1" style="color:var(--clay)">Weekly digest</div>
                {#if digest}
                  <div class="text-[13px]" style="color:var(--ink)">{digest.body}</div>
                  <div class="text-[11px] mt-1" style="color:var(--muted)">{fmtDate(digest.created_at)}</div>
                {:else}
                  <div class="text-[13px]" style="color:var(--muted)">No digest yet — Aria posts one automatically each week.</div>
                {/if}
              </div>
              <button onclick={runDigest} disabled={digestBusy} class="shrink-0 text-[12.5px] px-3 py-1.5 rounded-[8px] text-white disabled:opacity-50" style="background:var(--clay)">{digestBusy ? 'Running…' : 'Run now'}</button>
            </div>

            <!-- top gaps by leverage -->
            {#if audit.gaps.length}
              <h3 class="text-[13px] font-semibold mt-6 mb-2" style="color:var(--ink)">Fix these first <span class="font-normal" style="color:var(--muted)">(highest leverage)</span></h3>
              <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
                {#each audit.gaps as g, i}
                  <div class="panel p-3 flex items-start gap-3">
                    <span class="serif text-[18px] w-6 text-center shrink-0" style="color:var(--clay)">{i + 1}</span>
                    <div class="min-w-0 flex-1">
                      <div class="text-[13.5px] font-medium" style="color:var(--ink)">{g.title}</div>
                      <div class="text-[12.5px]" style="color:var(--muted)">{g.detail}</div>
                    </div>
                    <span class="shrink-0 text-[11px] px-2 py-0.5 rounded self-center text-right leading-tight" style="background:#f3f3f1; color:var(--clay)">lev {g.leverage}{#if g.lost != null}<br><span style="color:#a9742a">−{g.lost} pts</span>{/if}</span>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="panel p-3.5 mt-6 flex items-center gap-2 text-[13px]" style="color:#5a7a52; background:#f2f7f0; border-color:#d9e6d4">✓ No leverage gaps — Aria's knowledge is fully covered.</div>
            {/if}

            <!-- blind spots -->
            <h3 class="text-[13px] font-semibold mt-6 mb-2" style="color:var(--ink)">Questions with no source page <span class="font-normal" style="color:var(--muted)">({audit.stats.blind_questions})</span></h3>
            {#if audit.blind_spots.length}
              <div class="panel divide-y" style="--tw-divide-opacity:1">
                {#each audit.blind_spots as b}
                  <div class="flex items-center justify-between gap-3 px-3.5 py-2.5" style="border-color:var(--line)">
                    <button onclick={() => goto('/?q=' + encodeURIComponent(b.question))} class="text-left text-[13px] truncate hover:underline" style="color:var(--ink)">{b.question}</button>
                    <span class="shrink-0 flex items-center gap-2">
                      {#if b.last_at}<span class="text-[11px]" style="color:var(--muted)">last {relTime(b.last_at)}</span>{/if}
                      <span class="text-[11px] px-1.5 py-0.5 rounded" style="background:#ece9e0; color:var(--muted)">×{b.count}</span>
                    </span>
                  </div>
                {/each}
              </div>
              <p class="text-[11.5px] mt-1.5" style="color:var(--muted)">These need a runbook page (or a taught fact). Click to re-ask.</p>
            {:else}
              <div class="panel p-4 text-[12.5px]" style="color:var(--muted)">Every answer had a source page. </div>
            {/if}

            <!-- stale facts -->
            {#if audit.stale_facts.length}
              <h3 class="text-[13px] font-semibold mt-6 mb-2" style="color:var(--ink)">Stale facts <span class="font-normal" style="color:var(--muted)">(active, never used in 14+ days)</span></h3>
              <div class="panel divide-y">
                {#each audit.stale_facts as s}
                  <div class="flex items-center justify-between gap-3 px-3.5 py-2.5" style="border-color:var(--line)">
                    <button onclick={() => selectFact(facts.find((f) => f.id === s.id) || s)} class="text-left text-[13px] truncate hover:underline" style="color:var(--ink)">{s.value}</button>
                    <button onclick={() => rejectFact(s)} class="shrink-0 text-[11.5px] px-2 py-0.5 rounded border" style="border-color:var(--border); color:var(--muted)">Retire</button>
                  </div>
                {/each}
              </div>
            {/if}

            <!-- recent downvotes — the dislike→train loop, made actionable -->
            {#if audit.downvotes_recent.length}
              <h3 class="text-[13px] font-semibold mt-6 mb-2" style="color:var(--ink)">Recent downvotes <span class="font-normal" style="color:var(--muted)">(turn complaints into facts)</span></h3>
              <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
                {#each audit.downvotes_recent as d}
                  {@const corr = d.correction || d.note}
                  {@const linkedId = d.memory_id ?? d.fact_id}
                  {@const linkedFact = linkedId ? facts.find((f) => f.id === linkedId) : null}
                  <div class="panel p-3.5" style="border-color:var(--line)">
                    <div class="flex items-start justify-between gap-3">
                      <span class="shrink-0 grid place-items-center w-6 h-6 rounded-full text-[12px]" style="background:#fbecec;color:#cf6a4c">-</span>
                      <div class="min-w-0 flex-1">
                        {#if d.question}
                          <div class="text-[10.5px] uppercase tracking-wide mb-0.5" style="color:var(--muted)">Asked</div>
                          <div class="text-[13px] font-medium leading-snug" style="color:var(--ink)">{d.question}</div>
                        {/if}
                        {#if d.answer}
                          <div class="text-[12px] mt-1.5 leading-snug line-clamp-3" style="color:var(--muted)">{d.answer}</div>
                        {/if}
                        {#if d.note && d.note !== d.correction}
                          <div class="text-[10.5px] uppercase tracking-wide mt-2 mb-0.5" style="color:var(--muted)">Complaint</div>
                          <div class="text-[12.5px] leading-snug" style="color:var(--ink)">{d.note}</div>
                        {/if}
                        {#if d.correction}
                          <div class="mt-2 rounded-[8px] px-2.5 py-1.5" style="background:#f3f3f1;border:1px solid #dcdcd8">
                            <div class="text-[10.5px] uppercase tracking-wide mb-0.5 flex items-center gap-1" style="color:var(--clay)"><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z"/></svg> Suggested correction</div>
                            <div class="text-[12.5px] leading-snug" style="color:var(--ink)">{d.correction}</div>
                          </div>
                        {/if}
                        {#if !d.question && !d.answer && !d.note && !d.correction}
                          <div class="text-[12.5px]" style="color:var(--muted)">(no detail)</div>
                        {/if}
                      </div>
                      {#if d.created_at}<span class="shrink-0 text-[11px]" style="color:var(--muted)">{relTime(d.created_at)}</span>{/if}
                    </div>

                    <!-- Teach correction affordance -->
                    {#if corr}
                      <div class="flex items-center flex-wrap gap-2 mt-2.5 pt-2.5 border-t" style="border-color:var(--line)">
                        {#if linkedFact && linkedFact.status === 'pending'}
                          <span class="text-[11px]" style="color:var(--clay)">→ pending in Facts</span>
                          <button onclick={() => approveFact({ id: linkedId } as any)}
                            class="text-[11.5px] px-2.5 py-1 rounded-[7px] text-white" style="background:var(--clay)">✓ Approve fact</button>
                        {:else if linkedFact}
                          <span class="text-[11px]" style="color:#5fa463">✓ Already a taught fact</span>
                        {:else if dvTaught[d.id ?? corr]}
                          <span class="text-[11px]" style="color:#5fa463">✓ Taught as fact</span>
                        {:else}
                          <button onclick={() => teachFromDownvote(d)} disabled={teachBusyId === (d.id ?? -1)}
                            class="text-[11.5px] px-2.5 py-1 rounded-[7px] text-white disabled:opacity-50" style="background:var(--clay)">
                            {teachBusyId === (d.id ?? -1) ? 'Teaching…' : '＋ Teach as fact'}
                          </button>
                          <span class="text-[11px]" style="color:var(--muted)">saves the correction as a taught fact</span>
                        {/if}
                      </div>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}

            <!-- activity timeline (notifications) — consecutive digests folded, click a row for detail -->
            {#if activity.length}
              {@const folded = foldActivity(activity).slice(0, 14)}
              <div class="flex items-center justify-between mt-7 mb-2.5">
                <h3 class="text-[14px] font-semibold" style="color:var(--ink)">Recent activity</h3>
                <span class="text-[11.5px]" style="color:var(--muted)">{activity.length} events</span>
              </div>
              <div class="actlist">
                {#each folded as n}
                  <button class="actrow" onclick={() => (actModal = n)}>
                    <span class="actic" style="background:{notifTint(n.kind)}">{notifIcon(n.kind)}</span>
                    <span class="actbody">
                      <span class="actttl">
                        <span class="acttag">{notifLabel(n.kind)}</span>
                        {#if n._count > 1}<span class="actx">×{n._count}</span>{/if}
                      </span>
                      <span class="acthead">{n.title}</span>
                      {#if n.body}<span class="actsub">{n.body}</span>{/if}
                    </span>
                    <span class="acttime">{relTime(n.created_at)}</span>
                    <svg class="actchev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg>
                  </button>
                {/each}
              </div>
            {/if}
            <div class="h-8"></div>
          {/if}
        </div>

      {:else if railTab === 'graph' && !sel}
        <!-- ===== KNOWLEDGE GRAPH (Obsidian-style, dark focused viz surface) ===== -->
        <div class="graphwrap">
          <div bind:this={graphEl} class="graphcanvas"></div>

          <!-- filter toggles (top-left overlay) + color-by-document note -->
          <div class="goverlay gtl">
            <button class="gtog {graphFilters.docs ? 'on' : ''}" onclick={() => toggleGraphFilter('docs')}><span class="gdot gdoc"></span> Docs</button>
            <button class="gtog {graphFilters.pages ? 'on' : ''}" onclick={() => toggleGraphFilter('pages')}><span class="gdot gpg"></span> Pages</button>
            <button class="gtog {graphFilters.facts ? 'on' : ''}" onclick={() => toggleGraphFilter('facts')}><span class="gdot" style="background:#e0569b"></span> Facts</button>
            <span class="ghue">hue = document</span>
          </div>

          <!-- chart-type switcher (top-right overlay, dark pills, scrollable) -->
          <div class="goverlay gtr">
            {#each [['force', 'Force'], ['circular', 'Circular'], ['cluster', 'Clustered'], ['tree', 'Tree'], ['sunburst', 'Sunburst'], ['treemap', '▦ Treemap'], ['sankey', 'Sankey']] as [k, l]}
              <button class="gtog {graphLayout === k ? 'on' : ''}" onclick={() => setGraphLayout(k as GLayout)}>{l}</button>
            {/each}
          </div>

          <!-- node detail panel (left slide-in; opens on node click, no navigation) -->
          {#if gpanel}
            <div class="gpanel">
              <div class="gpan-head">
                <span class="gpan-type gpan-{gpanel.type}">{(gpanel.type || 'node').toUpperCase()}</span>
                <button class="gpan-x" onclick={closePanel} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
              </div>
              {#if gpanelBusy}
                <div class="gpan-load"><span class="gspin"></span></div>
              {:else}
                <div class="gpan-body">
                  {#if gpanelErr}<div class="gpan-err">{gpanelErr}</div>{/if}
                  <h3 class="gpan-title">{gpanel.title || '—'}</h3>
                  {#if gpanel.subtitle}<div class="gpan-sub">{gpanel.subtitle}</div>{/if}
                  {#if gpanel.image_url && (gpanel.type === 'page' || gpanel.type === 'doc')}
                    <img class="gpan-img" src={gpanel.image_url} alt={gpanel.title || ''} loading="lazy" />
                  {/if}
                  {#if gpanel.stats?.length}
                    <div class="gpan-stats">
                      {#each gpanel.stats as s}
                        <div class="gpan-stat"><span class="gpan-sl">{s.label}</span><span class="gpan-sv">{s.value}</span></div>
                      {/each}
                    </div>
                  {/if}
                  <div class="gpan-actions">
                    {#if (gpanel.type === 'doc' || gpanel.type === 'page') && gpanel.doc_id}
                      <button class="gpan-btn" onclick={panelOpenReader}>Open in reader →</button>
                    {:else if gpanel.type === 'fact'}
                      <button class="gpan-btn" onclick={panelOpenFact}>Open fact →</button>
                    {/if}
                  </div>

                  <!-- HOW THIS CONNECTS — grouped relations w/ plain-English "why" (above the long summary) -->
                  {#if gpanel.relations?.length}
                    <div class="gpan-connhdr">How this connects</div>
                    <div class="gpan-rels">
                      {#each gpanel.relations as rel}
                        <div class="gpan-rel">
                          <div class="gpan-rel-top">
                            <span class="gpan-rel-ico">{relIcon(rel.kind)}</span>
                            <span class="gpan-rel-title">{rel.title}</span>
                          </div>
                          {#if rel.why}<div class="gpan-rel-why">{rel.why}</div>{/if}
                          {#if rel.items?.length}
                            <div class="gpan-chips">
                              {#each rel.items as it}
                                <button class="gpan-chip" onclick={() => openNodePanel(it.id)} title={it.label}>
                                  <span class="gdot" style="background:{nbDot(it.type)}"></span>
                                  <span class="gpan-chip-l">{it.label}</span>
                                </button>
                              {/each}
                            </div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                    <div class="gpan-legend">inside · reading order · answered together · similar topic · fact link</div>
                  {:else if gpanel.neighbors?.length}
                    <!-- back-compat: backend hasn't sent grouped relations → old flat list -->
                    <div class="gpan-linkhdr">Linked · {gpanel.neighbors.length}</div>
                    <div class="gpan-links">
                      {#each gpanel.neighbors as nb}
                        <button class="gpan-nb" onclick={() => openNodePanel(nb.id)}>
                          <span class="gdot" style="background:{nbDot(nb.type)}"></span>
                          <span class="gpan-nb-l">{nb.label}</span>
                        </button>
                      {/each}
                    </div>
                  {:else}
                    <div class="gpan-noconn">No links yet — this knowledge isn't connected to anything else.</div>
                  {/if}

                  {#if gpanel.summary}<p class="gpan-summary gpan-summary-b">{gpanel.summary}</p>{/if}
                </div>
              {/if}
            </div>
          {/if}

          <!-- node/link counter (bottom-right) -->
          {#if !graphLoading && !graphErr && graphCounts.nodes > 0}
            <div class="goverlay gbr">{graphCounts.nodes} nodes · {graphCounts.links} links</div>
          {/if}

          <!-- loading -->
          {#if graphLoading}
            <div class="gcenter"><span class="gspin"></span><span>Building the graph…</span></div>
          {:else if graphErr}
            <div class="gcenter">
              <div class="mb-1"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div>
              <div>{graphErr}</div>
              <button class="gretry" onclick={refreshGraph}>Retry</button>
            </div>
          {:else if graphCounts.nodes === 0}
            <div class="gcenter">
              <div>Graph empty — ingest docs or teach facts.</div>
            </div>
          {/if}
        </div>

      {:else if sel?.type === 'teach'}
        <!-- TEACH COMPOSER -->
        <div class="max-w-2xl mx-auto px-8 pt-4 pb-8">
          <button onclick={() => (sel = null)} class="backlink mb-3">← Back</button>
          <div class="flex items-center gap-2 mb-1"><h2 class="serif text-[20px] font-medium" style="color:var(--ink)">Teach a fact</h2></div>
          <p class="text-[13px] mb-5" style="color:var(--muted)">A rule, correction, or fact that isn't in any document. Aria recalls it in every answer.</p>
          <div class="panel" style="padding:18px">
            <label class="block text-[12px] font-medium mb-1" style="color:#52504a">Label <span style="color:var(--muted)">(optional)</span></label>
            <input bind:value={tKey} placeholder="e.g. helpdesk" class="w-full h-10 rounded-[9px] border px-3 text-sm mb-3 outline-none" style="border-color:var(--border); background:#fff" />
            <label class="block text-[12px] font-medium mb-1" style="color:#52504a">Fact</label>
            <textarea bind:value={tVal} rows="4" placeholder="e.g. Refund window is 14 days from delivery date." class="w-full rounded-[9px] border px-3 py-2 text-sm resize-none outline-none" style="border-color:var(--border); background:#fff"></textarea>
            <div class="flex justify-end gap-2 mt-3">
              <button onclick={() => (sel = null)} class="btn">Cancel</button>
              <button onclick={teach} disabled={tBusy || !tVal.trim()} class="text-sm px-4 py-2 rounded-[9px] text-white disabled:opacity-40" style="background:var(--clay)">{tBusy ? 'Teaching…' : 'Teach'}</button>
            </div>
          </div>
        </div>

      {:else if sel?.type === 'doc'}
        <!-- ===== FULL-SCREEN DOCUMENT READER (own screen, covers all chrome) ===== -->
        <div class="fixed inset-0 z-40 flex flex-col" style="background:var(--paper)">
          <!-- doc header (cream) -->
          <div class="shrink-0 px-7 pt-3 pb-3 border-b" style="background:var(--cream); border-color:var(--border)">
            <button onclick={closeReader} class="backlink mb-2">← Back to Brain</button>
            <div class="flex items-start justify-between gap-4">
              <div class="min-w-0">
                <h1 class="serif text-[20px] font-medium leading-tight" style="color:var(--ink)">{dMeta.title || '…'}</h1>
                <div class="text-[12px] mt-1 flex items-center gap-x-1.5 gap-y-0.5 flex-wrap" style="color:var(--muted)">
                  {#if dMeta.code}<span>{dMeta.code}</span><span>·</span>{/if}
                  {#if dMeta.category}<span>{dMeta.category}</span><span>·</span>{/if}
                  <span class="px-1.5 py-0.5 rounded uppercase" style="background:#ece9e0; font-size:10px">{dDoc?.lang || '—'}</span>
                  <span>·</span><span>{dDoc?.page_count} pages</span>
                  <span>·</span><span>{dStats?.sections ?? 0} sections</span>
                  <span>·</span><span>uploaded {dDoc ? fmtDate(dDoc.created_at) : ''}</span>
                  {#if dStats}
                    <span>·</span>
                    {#if (dStats.used_count ?? 0) > 0}<span style="color:var(--clay)">used {dStats.used_count}×</span>{:else}<span>unused</span>{/if}
                    <span>·</span>
                    <span style="color:{dFullyExtracted ? '#5fa463' : '#a9742a'}">{dFullyExtracted ? '✓ fully extracted' : `◐ ${dVisionPct}% read`}</span>
                  {/if}
                </div>
              </div>
              <div class="shrink-0 flex items-center gap-2">
                <button onclick={downloadTxt} class="btn" title="Download text">⬇ .txt</button>
                <button onclick={askAbout} class="flex items-center gap-1.5 text-sm px-3.5 py-2 rounded-[9px] text-white" style="background:var(--clay)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                  Ask about this doc
                </button>
              </div>
            </div>
          </div>

          <!-- view toolbar (white) -->
          <div class="shrink-0 flex items-center gap-2 px-7 py-2 border-b" style="border-color:var(--border)">
            <div class="seg-group">
              {#each VIEWS as [k, l]}<button onclick={() => setView(k as any)} class="segb {view === k ? 'on' : ''}">{l}</button>{/each}
            </div>
            {#if view === 'read'}
              <div class="ml-auto flex items-center gap-1.5">
                <button onclick={() => (zoomPct = Math.max(50, zoomPct - 10))} class="zbtn" aria-label="Zoom out">−</button>
                <span class="text-[12px] tnum w-11 text-center" style="color:var(--muted)">{zoomPct}%</span>
                <button onclick={() => (zoomPct = Math.min(200, zoomPct + 10))} class="zbtn" aria-label="Zoom in">+</button>
                <button onclick={() => (zoomPct = 100)} class="btn ml-1" style="height:30px; padding:0 11px">⤢ Fit</button>
              </div>
            {/if}
          </div>

          <!-- 3 panes -->
          <div class="flex-1 min-h-0 flex">
            <!-- LEFT THUMBS -->
            {#if dPages.length > 1}
              <div class="hidden xl:block shrink-0 w-[104px] overflow-y-auto border-r py-3 px-2 space-y-2" style="border-color:var(--border); background:#f7f7f5">
                {#each dPages as p}
                  <button onclick={() => scrollToReadPage(p.page_no)} class="block w-full rounded-md overflow-hidden border transition" style="border-color:{activePage === p.page_no ? 'var(--clay)' : 'var(--border)'}; box-shadow:{activePage === p.page_no ? '0 0 0 2px #f0efed' : 'none'}">
                    <img src={api.pageImg(p.page_id)} alt="p{p.page_no}" loading="lazy" class="w-full block" style="background:#fff" />
                    <div class="text-[10px] py-0.5 text-center tnum" style="color:{activePage === p.page_no ? 'var(--clay)' : 'var(--muted)'}">{p.page_no}{#if weakSet.has(p.page_no)}<span style="color:#a9742a"> ⚠</span>{/if}</div>
                  </button>
                {/each}
              </div>
            {/if}

            <!-- CENTER (view-switched) -->
            <div class="flex-1 min-w-0 relative">
              {#if view === 'read'}
                <div bind:this={readEl} onscroll={onReadScroll} class="absolute inset-0 overflow-y-auto" style="background:#ece8df">
                  <div class="py-6">
                    {#if dPages.length}
                      {#each dPages as p}
                        <div id="rp-{p.page_no}" class="mx-auto mb-6 scroll-mt-4" style="max-width:{Math.round(8.6 * zoomPct)}px; width:92%">
                          <button onclick={() => { selPage = p; openZoom(); }} class="block w-full rounded-[4px] overflow-hidden" style="box-shadow:0 2px 12px rgba(60,40,25,.16)">
                            <img src={api.pageImg(p.page_id)} alt="page {p.page_no}" loading="lazy" class="w-full block" style="background:#fff" />
                          </button>
                          <div class="text-center text-[11px] mt-1.5 tnum" style="color:#9a8e7f">page {p.page_no} / {dPages.length}{#if weakSet.has(p.page_no)}<span style="color:#a9742a"> · ⚠ not vision-read</span>{/if}</div>
                        </div>
                      {/each}
                    {:else}
                      <div class="text-center py-16 text-sm" style="color:var(--muted)">Loading pages…</div>
                    {/if}
                  </div>
                  {#if dPages.length}
                    <div class="sticky bottom-3 mx-auto w-fit px-3 py-1 rounded-full text-[11px] tnum" style="background:rgba(43,42,39,.82); color:#fff">p.{activePage} / {dPages.length}</div>
                  {/if}
                </div>

              {:else if view === 'outline'}
                <div class="absolute inset-0 overflow-y-auto px-7 py-6">
                  <div class="max-w-3xl mx-auto">
                    <div class="text-[12.5px] mb-3" style="color:var(--muted)">PageIndex reasoning tree · {dOutline.length} sections · click a section to jump</div>
                    {#if dOutline.length === 0}
                      <div class="panel text-center py-10 text-sm" style="color:var(--muted); padding:24px">No sections (flat document).</div>
                    {:else}
                      <div class="panel" style="padding:0">
                        {#each dOutline as n, i}
                          <button onclick={() => scrollToReadPage(n.page_no)} class="w-full text-left px-4 py-3.5 hover:bg-[#efefec]" style={i ? 'border-top:1px solid var(--line)' : ''}>
                            <div class="flex items-baseline justify-between gap-3"><span class="text-[14px] font-semibold" style="color:var(--ink)">{n.title || '(untitled)'}</span><span class="text-[12px] shrink-0 tnum" style="color:var(--muted)">p.{n.page_no} →</span></div>
                            {#if n.summary}<div class="text-[12.5px] mt-1 leading-relaxed" style="color:var(--muted)">{n.summary}</div>{/if}
                          </button>
                        {/each}
                      </div>
                    {/if}
                  </div>
                </div>

              {:else if view === 'text'}
                <div class="absolute inset-0 overflow-y-auto">
                  {#if fullText.length === 0}
                    <div class="text-center py-16 text-sm" style="color:var(--muted)">Loading…</div>
                  {:else}
                    <div class="px-7 py-5 grid grid-cols-[80px_1fr] gap-5 max-w-5xl mx-auto">
                      <div class="self-start sticky top-2 max-h-[80vh] overflow-y-auto pr-1">
                        {#each fullText as p}
                          <button onclick={() => jumpFt(p.page_no)} class="block w-full text-left px-2.5 py-1.5 rounded-md text-[12px] mb-0.5 tnum"
                            style="background:{ftActive === p.page_no ? '#f0efed' : 'transparent'}; color:{ftActive === p.page_no ? 'var(--clay)' : 'var(--muted)'}; font-weight:{ftActive === p.page_no ? 600 : 400}">P.{p.page_no}</button>
                        {/each}
                      </div>
                      <div class="min-w-0">
                        <div class="flex items-center gap-2 mb-3 flex-wrap sticky top-0 z-10 py-2" style="background:var(--paper)">
                          <div class="relative flex-1 min-w-[180px]">
                            <input bind:value={ftQuery} placeholder="Find in document…" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? prevMatch() : nextMatch(); } }} class="w-full h-9 rounded-[9px] border px-3 text-sm outline-none" style="border-color:var(--border); background:var(--paper)" />
                          </div>
                          {#if ftQuery.trim()}
                            <div class="flex items-center gap-1 text-[12.5px] tnum" style="color:var(--muted)">
                              <button onclick={prevMatch} class="px-1.5 py-1 rounded hover:bg-[#efefec]" aria-label="Previous match">‹</button>
                              <span>{totalMatches ? matchIdx : 0}/{totalMatches}</span>
                              <button onclick={nextMatch} class="px-1.5 py-1 rounded hover:bg-[#efefec]" aria-label="Next match">›</button>
                            </div>
                          {/if}
                          <div class="flex items-center rounded-[9px] border p-0.5" style="border-color:var(--border); background:#fff">
                            {#each [['clean', 'Clean'], ['text', 'Text layer'], ['vision', 'Vision']] as [k, l]}<button onclick={() => (ftView = k as any)} class="seg2 {ftView === k ? 'on' : ''}">{l}</button>{/each}
                          </div>
                          <button onclick={downloadTxt} class="btn">⬇ .txt</button>
                        </div>
                        <div class="panel" style="padding:24px 28px">
                          {#each renderedPages as p}
                            <div id="pg-{p.page_no}" class="mb-8 scroll-mt-20">
                              <div class="text-[12px] font-semibold uppercase tracking-wide mb-3 pb-1.5 border-b" style="color:var(--clay); border-color:var(--line)">Page {p.page_no}</div>
                              <div class="ftprose">{@html p.html || '<span style="color:var(--muted)">(empty)</span>'}</div>
                            </div>
                          {/each}
                        </div>
                      </div>
                    </div>
                  {/if}
                </div>

              {:else}
                <!-- SPLIT: page image ‖ extracted text -->
                <div class="absolute inset-0 overflow-y-auto px-7 py-5">
                  {#if fullText.length === 0}
                    <div class="text-center py-16 text-sm" style="color:var(--muted)">Loading…</div>
                  {:else}
                    <div class="max-w-6xl mx-auto">
                      <div class="flex items-center mb-3">
                        <span class="text-[12.5px]" style="color:var(--muted)">Page image vs extracted text — verify what Aria reads</span>
                        <div class="seg-group ml-auto">{#each [['clean', 'Clean'], ['text', 'Text'], ['vision', 'Vision']] as [k, l]}<button onclick={() => (ftView = k as any)} class="segb {ftView === k ? 'on' : ''}">{l}</button>{/each}</div>
                      </div>
                      <div class="space-y-4">
                        {#each fullText as p}
                          {@const img = dPages.find((x) => x.page_no === p.page_no)}
                          {@const pc = pageCharMap.get(p.page_no)}
                          <div class="panel grid grid-cols-2 gap-0 overflow-hidden" style="padding:0">
                            <div class="border-r" style="border-color:var(--line); background:#f7f7f5">
                              {#if img}<button onclick={() => { selPage = img; openZoom(); }} class="block w-full"><img src={api.pageImg(img.page_id)} alt="page {p.page_no}" loading="lazy" class="w-full block" /></button>{/if}
                            </div>
                            <div class="p-4 min-w-0">
                              <div class="flex items-center gap-2 mb-2">
                                <span class="text-[11px] uppercase tracking-wide tnum" style="color:var(--clay)">Page {p.page_no}</span>
                                {#if pc}<span class="text-[10.5px] tnum" style="color:var(--muted)">text {pc.text} · vision {pc.vision} chars</span>{/if}
                                {#if weakSet.has(p.page_no)}<span class="text-[10.5px]" style="color:#a9742a">⚠ no vision</span>{/if}
                              </div>
                              <pre class="ftprose text-[12.5px]" style="white-space:pre-wrap">{bodyFor(p) || '(empty)'}</pre>
                            </div>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}
                </div>
              {/if}
            </div>

            <!-- RIGHT PANEL (insights + outline + ask) -->
            <div class="hidden lg:flex shrink-0 w-[320px] flex-col border-l" style="border-color:var(--border)">
              <div class="flex-1 overflow-y-auto p-4">
                <div class="text-[10.5px] font-semibold uppercase tracking-wide mb-2" style="color:var(--muted)">Insights</div>
                {#if dStats}
                  {@const uc = dStats.used_count ?? 0}
                  <div class="ptiles" style="margin-top:0">
                    <div class="ptile"><b>{dStats.pages}</b><span>pages</span></div>
                    <div class="ptile"><b>{dStats.sections}</b><span>sections</span></div>
                    <div class="ptile"><b style="color:{uc > 0 ? 'var(--clay)' : 'var(--muted)'}">{uc}×</b><span>{uc > 0 ? 'used' : 'unused'}</span></div>
                    <div class="ptile"><b style="color:{dVisionPct >= 100 ? '#5fa463' : '#a9742a'}">{dVisionPct}%</b><span>read</span></div>
                  </div>
                  <div class="prows">
                    <div class="prow"><span class="pk2">Uploaded</span><span>{dDoc ? fmtDate(dDoc.created_at) : ''} · {dDoc ? relTime(dDoc.created_at) : ''}</span></div>
                    <div class="prow"><span class="pk2">Answered</span><span>{dStats.last_used_at ? `${relTime(dStats.last_used_at)} · ${uc} Q${uc === 1 ? '' : 's'}` : 'never yet'}</span></div>
                    {#if dStats.votes && (dStats.votes.up || dStats.votes.down)}<div class="prow"><span class="pk2">Feedback</span><span>{dStats.votes.up} up · {dStats.votes.down} down</span></div>{/if}
                    <div class="prow col">
                      <span class="pk2">✓ Extraction</span>
                      <div class="pex">
                        <div class="pexrow"><span class="pexl">vision</span><span class="pbar"><i style="width:{dVisionPct}%; background:#5fa463"></i></span><span class="pexn">{dVisionPct}%</span></div>
                        <div class="pexrow"><span class="pexl">text</span><span class="pbar"><i style="width:{dTextPct}%; background:var(--clay)"></i></span><span class="pexn">{dTextPct}%</span></div>
                      </div>
                      {#if dStats.weak_pages && dStats.weak_pages.length}<div class="text-[11px] mt-1.5 flex flex-wrap gap-1 items-center" style="color:#a9742a">⚠ weak: {#each dStats.weak_pages as wp}<button onclick={() => scrollToReadPage(wp)} class="underline">p.{wp}</button>{/each}</div>{/if}
                    </div>
                    <div class="prow"><span class="pk2">Structure</span><span>{dStats.sections ? `${dStats.sections} sections` : 'flat'}{dStats.has_tree ? ' · tree ✓' : ''}</span></div>
                  </div>
                {/if}
                {#if dOutline.length}
                  <div class="text-[10.5px] font-semibold uppercase tracking-wide mt-5 mb-1.5" style="color:var(--muted)">Outline · {dOutline.length}</div>
                  <div>
                    {#each dOutline as n}
                      <button onclick={() => scrollToReadPage(n.page_no)} class="peeko" style="font-weight:{activePage === n.page_no ? 600 : 400}"><span class="truncate">{n.title || '(untitled)'}</span><b>p.{n.page_no}</b></button>
                    {/each}
                  </div>
                {/if}
              </div>
            </div>
          </div>
        </div>

      {:else if !$brainData.loaded && docs.length === 0 && feedItems.length === 0}
        <!-- SKELETON — only while NOTHING has loaded yet. Never gate already-loaded
             content behind the `loading` flag: if the flag ever sticks true (stale
             remount / superseded call), real docs would be hidden forever. Once docs
             or feed items exist, fall through to the feed branch. -->
        <div class="px-7 py-6">
          <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3">
            {#each Array(10) as _, i (i)}
              <div class="card"><div class="sk sk-ic"></div><div class="sk sk-l1"></div><div class="sk sk-l2"></div></div>
            {/each}
          </div>
        </div>

      {:else if railTab === 'brain'}
        <!-- ===== UNIFIED BRAIN FEED — one knowledge stream over docs + pages + facts ===== -->
        <div class="px-7 py-6">
          <div class="mb-5">
            <p class="text-[13px] max-w-xl" style="color:var(--muted)">One knowledge feed — uploaded documents, their pages, and the facts you taught. Search any of it; click to open.</p>
          </div>

          <!-- pending review strip (chat-learned facts awaiting approval) -->
          {#if pendingFacts.length > 0 && feedType !== 'qa'}
            <div class="flex items-center justify-between mb-2">
              <div class="text-[10.5px] font-semibold uppercase tracking-wide" style="color:var(--clay)">Awaiting review · {pendingFacts.length}</div>
              <div class="text-[11px]" style="color:var(--muted)">Chat-learned facts don't affect answers until approved</div>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-2 mb-7">
              {#each pendingFacts as f (f.id)}
                <div class="panel p-3.5 flex items-start justify-between gap-4" style="border-left:3px solid #c98a2e; background:#fdfaf3">
                  <button onclick={() => selectFact(f)} class="text-left min-w-0 flex-1">
                    <div class="flex items-center gap-1.5 mb-1">
                      {@render originBadge(f.source)}
                      {#if factScore(f) != null}<span class="ftag" style="background:{factScore(f)! >= 90 ? '#e8f3ec' : factScore(f)! >= 70 ? '#fbf1df' : '#fbe9e6'}; color:{factScore(f)! >= 90 ? '#3f8f5f' : factScore(f)! >= 70 ? '#a9742a' : '#c0492f'}">score {factScore(f)}%</span>{/if}
                      {#if f.key}<span class="text-[10.5px] font-semibold uppercase tracking-wide" style="color:var(--clay)">{f.key}</span>{/if}
                    </div>
                    <div class="text-[13.5px]" style="color:var(--ink)">{f.value}</div>
                    <div class="text-[11px] mt-1" style="color:var(--muted)">{f.source === 'doc' ? 'From a document' : f.source === 'feedback' ? 'From a correction' : 'Learned in chat'}{f.source !== 'doc' && f.created_by ? ' · from ' + f.created_by : ''}{f.created_at ? ' · ' + relTime(f.created_at) : ''}</div>
                  </button>
                  <div class="flex gap-1.5 shrink-0">
                    <button onclick={() => approveFact(f)} class="text-[12px] px-3 py-1.5 rounded-[8px] text-white" style="background:var(--clay)">✓ Approve</button>
                    <button onclick={() => rejectFact(f)} class="text-[12px] px-3 py-1.5 rounded-[8px] border" style="border-color:var(--border); color:var(--muted); background:#fff">Reject</button>
                  </div>
                </div>
              {/each}
            </div>
          {/if}

          <!-- feed or doc browser depending on feedType -->
          {#if feedType === 'doc'}
          <!-- ===== A3 DOCUMENT BROWSER (newest→old, category + date filters) ===== -->
          {#snippet docCard(d: Doc)}
            {@const p = parseDocName(d.name)}
            {@const st = d.status || 'ready'}
            {@const notReady = st !== 'ready'}
            {@const lc = langColor(d.lang)}
            <div class="fcard group {notReady ? 'op' : ''}" role="button" tabindex="0"
                 onclick={() => { if (!notReady) openPeek(d); }}
                 onkeydown={(e) => { if (e.key === 'Enter' && !notReady) openPeek(d); }}>
              <div class="fhead">
                <span class="ftype" style="background:{typeTint('doc')}; color:{typeInk('doc')}">DOCUMENT</span>
                <span class="fflag" style="background:{lc.bg}; color:{lc.fg}">{(d.lang || '—').toUpperCase()}</span>
              </div>
              <div class="ftitle">{p.title}</div>
              {#if st === 'ready'}
                {@const rd = d.page_count ? Math.round(((d.vision_pages ?? 0) / d.page_count) * 100) : 0}
                {@const uc = d.used_count ?? 0}
                <div class="fmeta">
                  <span class="fdot" style="background:{statusDot(st)}"></span>
                  <span>Ready</span><span class="fsep">·</span>
                  <span>{d.page_count}p</span>
                </div>
                <div class="fsub">{uc > 0 ? `used ${uc}×` : 'Unused'}{#if rd < 100} · {rd}% read{/if}</div>
              {:else if st === 'processing'}
                <div class="fmeta"><span class="fdot" style="background:{statusDot(st)}"></span>Processing… {d.pages_done ?? 0}/{d.page_count || '…'}</div>
                <span class="ds-track mt-1"><span class="ds-fill" style="width:{Math.max(0, Math.min(100, d.progress ?? 0))}%"></span></span>
              {:else if st === 'failed'}
                <div class="fmeta" style="color:#9a5a4c"><span class="fdot" style="background:{statusDot(st)}"></span>Failed{#if d.error} · {d.error}{/if}</div>
              {:else if st === 'queued'}
                <div class="fmeta"><span class="fdot" style="background:{statusDot(st)}"></span>Queued</div>
              {/if}
              {#if st === 'failed'}
                <button onclick={(e) => { e.stopPropagation(); retryDoc(d); }} class="ddel" style="right:36px; color:var(--clay)" aria-label="Retry">↻</button>
              {/if}
              <button onclick={(e) => { e.stopPropagation(); delDoc(d); }} class="ddel" aria-label="Delete document">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
              </button>
            </div>
          {/snippet}

          {#snippet docRow(d: Doc)}
            {@const p = parseDocName(d.name)}
            {@const st = d.status || 'ready'}
            {@const notReady = st !== 'ready'}
            {@const uc = d.used_count ?? 0}
            {@const rd = d.page_count ? Math.round(((d.vision_pages ?? 0) / d.page_count) * 100) : 0}
            {@const lc = langColor(d.lang)}
            <div class="dlrow {notReady ? 'proc' : ''}" role="button" tabindex="0"
                 onclick={() => { if (notReady) { flowOpen = d.id; jobsOpen = true; } else openPeek(d); }}
                 onkeydown={(e) => { if (e.key === 'Enter') { if (notReady) { flowOpen = d.id; jobsOpen = true; } else openPeek(d); } }}>
              <!-- Col 1: Title + sub -->
              <div class="dl-c-title">
                <span class="dl-tile" style="background:{lc.bg}; color:{lc.fg}">
                  {#if fileGlyph(d.name) === 'IMG'}
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                  {:else}
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M9 13h6M9 17h4"/></svg>
                  {/if}
                </span>
                <span class="min-w-0">
                  <span class="dl-ttl">{p.title}</span>
                  <span class="dl-sub">
                    <span style="color:{statusDot(st)}">●</span>
                    {p.category ? p.category + ' · ' : ''}{st === 'ready' ? 'Ready' : st}
                  </span>
                </span>
              </div>
              <!-- Col 2: Lang -->
              <span class="dl-c-lang">
                <span class="dlang" style="background:{lc.bg}; color:{lc.fg}; font-size:10px; padding:2px 6px">{(d.lang || '—').toUpperCase()}</span>
              </span>
              <!-- Col 3: Pages -->
              <span class="dl-c-pages">{d.page_count ?? '—'}</span>
              <!-- Col 4: Used -->
              <span class="dl-c-used" style="color:{uc > 0 ? 'var(--clay)' : 'var(--muted)'}">{uc > 0 ? uc : '—'}</span>
              <!-- Col 5: Accuracy (vision read %) — show state for non-ready -->
              {#if st === 'ready'}
                <span class="dl-c-acc" style="color:{rd >= 80 ? '#5fa463' : '#a9742a'}">{rd}%</span>
              {:else if st === 'processing'}
                <span class="dl-c-acc" style="color:#d3a13e">{d.progress ?? 0}%</span>
              {:else if st === 'failed'}
                <span class="dl-c-acc" style="color:#cf6a4c">Failed</span>
              {:else}
                <span class="dl-c-acc" style="color:var(--muted)">—</span>
              {/if}
              <!-- Col 6: Uploaded date -->
              <span class="dl-c-date" title={d.created_at || ''}>{fmtDay(d.created_at)}</span>
              <!-- Col 7: Updated date (last ingest/change) -->
              <span class="dl-c-date" title={d.updated_at || d.ready_at || ''}>{fmtDay(d.updated_at || d.ready_at)}</span>
              <!-- Col 8: Uploaded by -->
              <span class="dl-c-by" title={d.uploaded_by || ''}>{shortUser(d.uploaded_by)}</span>
              <!-- Col 9: Actions -->
              <div class="dl-c-act">
                {#if st === 'failed'}
                  <button class="dl-icon" title="Retry" onclick={(e) => { e.stopPropagation(); retryDoc(d); }} aria-label="Retry">↻</button>
                {:else if st === 'ready'}
                  <button class="dl-open" onclick={(e) => { e.stopPropagation(); selectDoc(d); }} aria-label="Open document">Open →</button>
                {/if}
                <button class="dl-icon" title="Delete" onclick={(e) => { e.stopPropagation(); delDoc(d); }} aria-label="Delete document">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                </button>
              </div>
            </div>
          {/snippet}

          <!-- category chips -->
          <div class="catbar">
            <button class="chip {isCatAll ? 'on' : ''}" onclick={() => (docCat = 'all')}>All <b>{docMatches.length}</b></button>
            {#each docCatCounts.slice(0, CAT_CHIP_MAX) as [cat, n] (cat)}
              <button class="chip {docCat === cat ? 'on' : ''}" onclick={() => (docCat = cat)}>{cat} <b>{n}</b></button>
            {/each}
            {#if docCatCounts.length > CAT_CHIP_MAX}
              {@const extra = docCatCounts.slice(CAT_CHIP_MAX)}
              {@const sel = extra.find(([c]) => c === docCat)}
              <div class="catmore">
                <button class="chip {sel ? 'on' : ''}" onclick={(e) => { e.stopPropagation(); catMoreOpen = !catMoreOpen; }}>
                  {sel ? `${sel[0]}` : `More`} <b>{sel ? sel[1] : '+' + extra.length}</b> ▾
                </button>
                {#if catMoreOpen}
                  <button class="catmore-scrim" onclick={() => (catMoreOpen = false)} aria-label="Close"></button>
                  <div class="catmore-menu">
                    {#each extra as [cat, n] (cat)}
                      <button class="catmore-item {docCat === cat ? 'on' : ''}" onclick={() => { docCat = cat; catMoreOpen = false; }}>
                        <span>{cat}</span><b>{n}</b>
                      </button>
                    {/each}
                  </div>
                {/if}
              </div>
            {/if}
            {#if isAdmin}
              <button class="chip" style="margin-left:auto; color:var(--clay)" title="Re-run auto-categorize on all documents" onclick={retagAll} disabled={retagging}>{retagging ? 'Tagging…' : '↻ Re-tag'}</button>
            {/if}
          </div>
          <!-- date pills + sort + view -->
          <div class="dbar">
            <span class="dbar-lbl">Date</span>
            {#each DOC_DATE_OPTS as [k, l] (k)}
              <button class="pill {docDate === k ? 'on' : ''}" onclick={() => (docDate = k as any)}>{l}</button>
            {/each}
            <button class="jobspill" onclick={() => (jobsOpen = true)} title="Show ingest jobs">
              <span class="jp-spin" class:still={remaining === 0}>⟳</span> Jobs{#if remaining > 0} <b>{remaining}</b> · {overallPct}%{/if}
            </button>
            <div class="ml-auto flex items-center gap-2.5">
              <span class="dbar-lbl">Group</span>
              <div class="seg-group">
                <button onclick={() => setGroupBy('date')} class="segb {docGroupBy === 'date' ? 'on' : ''}" title="Group by upload date">Date</button>
                <button onclick={() => setGroupBy('category')} class="segb {docGroupBy === 'category' ? 'on' : ''}" title="Group by category">Category</button>
              </div>
              <span class="text-[12px]" style="color:var(--muted)">{docMatches.length} shown</span>
              <div class="seg-group">
                <button onclick={() => setDocView('cards')} class="segb {isViewCards ? 'on' : ''}" title="Card view">▦ Cards</button>
                <button onclick={() => setDocView('list')} class="segb {!isViewCards ? 'on' : ''}" title="List view">≣ List</button>
              </div>
              <select bind:value={fSort} class="fsort">
                <option value="recent">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="pages">Most pages</option>
                <option value="name">Name A–Z</option>
                <option value="used">Most used</option>
              </select>
            </div>
          </div>
          <!-- stage stepper (shared by strip + expand row) -->
          {#snippet originBadge(src)}
            {@const ai = factIsAI(src)}
            <span class="ftag origin" style="background:{ai ? '#eceaf4' : '#eaf0ee'}; color:{ai ? '#5a4f86' : '#41705e'}">
              {#if ai}
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="6" height="6"/><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>AI
              {:else}
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>Manual
              {/if}
            </span>
          {/snippet}

          {#snippet stepper(d: Doc)}
            {@const cur = stageOf(d)}
            <div class="stepper">
              {#each STAGES as s, i (s)}
                <span class="stp {cur.failed ? 'fail' : i < cur.idx ? 'done' : i === cur.idx ? 'now' : ''}">
                  <span class="stp-dot">{cur.failed ? '✗' : i < cur.idx ? '✓' : i === cur.idx ? '●' : '○'}</span>
                  <span class="stp-lbl">{s}{i === cur.idx && cur.sub ? ` ${cur.sub}` : ''}</span>
                </span>
                {#if i < STAGES.length - 1}<span class="stp-sep"></span>{/if}
              {/each}
            </div>
          {/snippet}

          <!-- grid or list -->
          {#if docMatches.length === 0}
            <div class="panel p-12 text-center" style="color:var(--muted)">
              <div class="text-3xl mb-2">—</div>
              <div class="text-[13.5px]">{needle ? `No documents match "${needle}".` : docDate !== 'all' ? 'No documents in this date range.' : 'No documents yet.'}</div>
              <button onclick={() => fileInput.click()} class="mt-4 text-sm px-4 py-2 rounded-[9px] text-white" style="background:var(--clay)">Upload a document</button>
            </div>
          {:else if isViewCards}
            {#if isCatAll}
              {#each docGroupsFiltered as g (g.cat)}
                <button onclick={() => (collapsed[g.cat] = !collapsed[g.cat])} class="flex items-center gap-1.5 mt-5 mb-2.5 text-[11.5px] font-medium" style="color:#6f6c65">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="transform:rotate({collapsed[g.cat] ? -90 : 0}deg); transition:transform .12s"><path d="M6 9l6 6 6-6"/></svg>
                  <span class="uppercase tracking-wide">{g.cat}</span><span style="color:var(--muted)">· {g.list.length}</span>
                </button>
                {#if !collapsed[g.cat]}
                  <div class="dgrid">
                    {#each g.list as d (d.id)}{@render docCard(d)}{/each}
                  </div>
                {/if}
              {/each}
            {:else}
              <div class="dgrid">
                {#each docCatFlat as d (d.id)}{@render docCard(d)}{/each}
              </div>
            {/if}
          {:else}
            {#if isCatAll}
              {#each docGroupsFiltered as g (g.cat)}
                <button onclick={() => (collapsed[g.cat] = !collapsed[g.cat])} class="flex items-center gap-1.5 mt-5 mb-2.5 text-[11.5px] font-medium" style="color:#6f6c65">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="transform:rotate({collapsed[g.cat] ? -90 : 0}deg); transition:transform .12s"><path d="M6 9l6 6 6-6"/></svg>
                  <span class="uppercase tracking-wide">{g.cat}</span><span style="color:var(--muted)">· {g.list.length}</span>
                </button>
                {#if !collapsed[g.cat]}
                  <div class="dlist">
                    <div class="dlist-head">
                      <span>Title</span><span>Lang</span><span>Pages</span><span>Used</span><span>Accuracy</span><span>Uploaded</span><span>Updated</span><span>By</span><span></span>
                    </div>
                    {#each g.list as d (d.id)}{@render docRow(d)}{/each}
                  </div>
                {/if}
              {/each}
            {:else}
              <div class="dlist">
                <div class="dlist-head">
                  <span>Title</span><span>Lang</span><span>Pages</span><span>Used</span><span>Accuracy</span><span></span>
                </div>
                {#each docCatFlat as d (d.id)}{@render docRow(d)}{/each}
              </div>
            {/if}
          {/if}
          {:else if feedType === 'qa'}
          <!-- ===== Q&A BANK (auto-mined from docs + harvested from upvoted chat) ===== -->
          <div class="flex items-center justify-between mb-2.5">
            <div class="text-[10.5px] font-semibold uppercase tracking-wide" style="color:var(--muted)">
              Q&amp;A pairs · {qaPairs.length}
            </div>
            <div class="seg-group">
              <button onclick={() => (qaFilter = 'all')} class="segb {qaFilter === 'all' ? 'on' : ''}">All</button>
              <button onclick={() => (qaFilter = 'active')} class="segb {qaFilter === 'active' ? 'on' : ''}">Active{#if qaCounts.active}<span class="ml-1 opacity-60">{qaCounts.active}</span>{/if}</button>
              <button onclick={() => (qaFilter = 'pending')} class="segb {qaFilter === 'pending' ? 'on' : ''}">Pending{#if qaCounts.pending}<span class="ml-1 opacity-60">{qaCounts.pending}</span>{/if}</button>
              <button onclick={() => (qaFilter = 'rejected')} class="segb {qaFilter === 'rejected' ? 'on' : ''}">Rejected</button>
            </div>
          </div>
          <p class="text-[12px] mb-4 max-w-2xl" style="color:var(--muted)">Questions and grounded answers Aria built from your documents and from chat answers you upvoted. Approve the good ones — pending pairs don't serve answers yet.</p>
          {#if qaShown.length === 0}
            <div class="panel p-12 text-center" style="color:var(--muted)">
              <div class="mb-2 flex justify-center"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
              <div class="text-[13.5px]">{qaFilter === 'all' ? 'No Q&A yet — they appear after you ingest a document or upvote a sourced chat answer.' : `No ${qaFilter} Q&A pairs.`}</div>
            </div>
          {:else}
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-2.5">
              {#each qaShown as p (p.id)}
                {@const st = p.status || 'pending'}
                <div class="panel p-4 flex flex-col gap-2" style="border-left:3px solid {st === 'active' ? '#5fa463' : st === 'rejected' ? '#cf6a4c' : '#c98a2e'}">
                  {#if qaEditId === p.id}
                    <input bind:value={qaEditQ} class="w-full rounded-[8px] border px-2.5 py-1.5 text-[13.5px] font-semibold outline-none" style="border-color:var(--border); background:var(--paper); color:var(--ink)" placeholder="Question" />
                    <textarea bind:value={qaEditA} rows="4" class="w-full rounded-[8px] border px-2.5 py-1.5 text-[12.5px] leading-relaxed outline-none resize-y" style="border-color:var(--border); background:var(--paper); color:var(--ink)" placeholder="Answer"></textarea>
                    <div class="flex gap-1.5 mt-1">
                      <button disabled={qaBusy === p.id || !qaEditQ.trim() || !qaEditA.trim()} onclick={() => saveQaEdit(p)} class="text-[12px] px-3 py-1.5 rounded-[8px] text-white disabled:opacity-50" style="background:var(--clay)">Save</button>
                      <button disabled={qaBusy === p.id} onclick={cancelQaEdit} class="text-[12px] px-3 py-1.5 rounded-[8px] border disabled:opacity-50" style="border-color:var(--border); color:var(--muted); background:#fff">Cancel</button>
                    </div>
                  {:else}
                  <div class="flex items-start justify-between gap-3">
                    <div class="text-[13.5px] font-semibold leading-snug" style="color:var(--ink)">{p.question}</div>
                    <span class="ftag shrink-0" style="background:{st === 'active' ? '#eef3ef' : st === 'rejected' ? '#fbe9e6' : '#fbf1df'}; color:{st === 'active' ? '#3f8f5f' : st === 'rejected' ? '#c0492f' : '#a9742a'}">{st.toUpperCase()}</span>
                  </div>
                  <div class="text-[12.5px] leading-relaxed" style="color:var(--muted)">{p.answer}</div>
                  <div class="flex items-center flex-wrap gap-x-2 gap-y-1 text-[11px]" style="color:var(--muted)">
                    <span>{p.source === 'chat' ? 'From upvoted chat' : 'From a document'}</span>
                    {#if p.page_ids && p.page_ids.length}<span>·</span><span>{p.page_ids.length} page{p.page_ids.length > 1 ? 's' : ''} cited</span>{/if}
                    {#if p.confidence != null}<span>·</span><span>conf {Math.round((p.confidence || 0) * 100)}%</span>{/if}
                    {#if p.created_at}<span>·</span><span>{relTime(p.created_at)}</span>{/if}
                  </div>
                  <div class="flex gap-1.5 mt-1">
                    {#if st !== 'active'}<button disabled={qaBusy === p.id} onclick={() => approveQa(p)} class="text-[12px] px-3 py-1.5 rounded-[8px] text-white disabled:opacity-50" style="background:var(--clay)">✓ Approve</button>{/if}
                    {#if st !== 'rejected'}<button disabled={qaBusy === p.id} onclick={() => rejectQa(p)} class="text-[12px] px-3 py-1.5 rounded-[8px] border disabled:opacity-50" style="border-color:var(--border); color:var(--muted); background:#fff">Reject</button>{/if}
                    <button disabled={qaBusy === p.id} onclick={() => startQaEdit(p)} class="text-[12px] px-3 py-1.5 rounded-[8px] border disabled:opacity-50" style="border-color:var(--border); color:var(--muted); background:#fff">Edit</button>
                    <button disabled={qaBusy === p.id} onclick={() => delQa(p)} class="text-[12px] px-2.5 py-1.5 rounded-[8px] border disabled:opacity-50 ml-auto" style="border-color:var(--border); color:var(--muted); background:#fff" title="Delete">Delete</button>
                  </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
          {:else}
          <!-- ===== KNOWLEDGE FEED (feedType === 'all' or 'fact') ===== -->
          <div class="flex items-center justify-between mb-2.5">
            <div class="text-[10.5px] font-semibold uppercase tracking-wide" style="color:var(--muted)">
              {q.trim() ? 'Results' : 'Recent knowledge'}{feedItems.length ? ` · ${feedItems.length}` : ''}
            </div>
            <div class="flex items-center gap-2.5">
              {#if feedType === 'fact'}
                <div class="seg-group">
                  <button onclick={() => (factFilter = 'all')} class="segb {factFilter === 'all' ? 'on' : ''}">All</button>
                  <button onclick={() => (factFilter = 'ai')} class="segb {factFilter === 'ai' ? 'on' : ''}">AI</button>
                  <button onclick={() => (factFilter = 'manual')} class="segb {factFilter === 'manual' ? 'on' : ''}">Manual</button>
                  <button onclick={() => (factFilter = 'pending')} class="segb {factFilter === 'pending' ? 'on' : ''}">Pending</button>
                </div>
              {/if}
              <div class="seg-group">
                <button onclick={() => setDocView('list')} class="segb {isViewList ? 'on' : ''}" title="List view">≣ List</button>
                <button onclick={() => setDocView('cards')} class="segb {isViewCards ? 'on' : ''}" title="Card view">▦ Cards</button>
              </div>
            </div>
          </div>
          {#if feedLoading && feedItems.length === 0}
            <div class="dgrid">
              {#each Array(8) as _, i (i)}
                <div class="dcard"><div class="dcover"></div><div class="dmeta2"><div class="sk sk-l1"></div><div class="sk sk-l2"></div></div></div>
              {/each}
            </div>
          {:else if feedErr}
            <div class="panel p-12 text-center" style="color:var(--muted)">
              <div class="mb-2 flex justify-center"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div>
              <div class="text-[13.5px]">Couldn't search the brain.</div>
              <button onclick={loadFeed} class="mt-4 text-sm px-4 py-2 rounded-[9px] text-white" style="background:var(--clay)">Retry</button>
            </div>
          {:else if feedItems.length === 0}
            <div class="panel p-12 text-center" style="color:var(--muted)">
              <div class="mb-2 flex justify-center"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg></div>
              <div class="text-[13.5px]">{q.trim() ? `Nothing matches "${q.trim()}".` : feedType === 'fact' ? 'No facts yet — teach a rule or correction Aria should always remember.' : 'Nothing here yet — upload a document or teach a fact.'}</div>
              {#if feedType !== 'fact'}<div class="text-[11.5px] mt-1">Facts also appear when you teach Aria in Chat ("remember…", "the correct value is…").</div>{:else}<div class="text-[11.5px] mt-1">Or just tell Aria in Chat: "remember that…" — it learns automatically (pending your review).</div>{/if}
              <div class="flex items-center justify-center gap-2 mt-4">
                {#if feedType === 'fact'}
                  <button onclick={startTeach} class="text-sm px-4 py-2 rounded-[9px] text-white" style="background:var(--clay)">Teach a fact</button>
                {:else}
                  <button onclick={() => fileInput.click()} class="text-sm px-4 py-2 rounded-[9px] text-white" style="background:var(--clay)">Upload a document</button>
                  <button onclick={startTeach} class="text-sm px-4 py-2 rounded-[9px] border" style="border-color:var(--border); color:var(--clay); background:#fff">Teach a fact</button>
                {/if}
              </div>
            </div>
          {:else if isViewList}
            <div class="flist">
              {#each feedItems.filter(factMatch) as it (it.type + '-' + it.id)}
                <div class="frow group" role="button" tabindex="0"
                     onclick={() => openFeedItem(it)}
                     onkeydown={(e) => { if (e.key === 'Enter') openFeedItem(it); }}>
                  <span class="fbadge" style="background:{typeTint(it.type)}; color:{typeInk(it.type)}">{typeIcon(it.type)}</span>
                  <div class="min-w-0 flex-1">
                    <div class="ftitle">{it.title}</div>
                    {#if cleanSnippet(it.snippet, it.title)}<div class="fsnip">{cleanSnippet(it.snippet, it.title)}</div>{/if}
                  </div>
                  {#if it.type === 'fact'}
                    {@render originBadge(it.source)}
                    {#if factScore(it) != null}<span class="ftag" style="background:{factScore(it)! >= 90 ? '#e8f3ec' : factScore(it)! >= 70 ? '#fbf1df' : '#fbe9e6'}; color:{factScore(it)! >= 90 ? '#3f8f5f' : factScore(it)! >= 70 ? '#a9742a' : '#c0492f'}">{factScore(it)}%</span>{/if}
                    {#if it.status === 'pending'}<span class="ftag" style="background:#fbf1df; color:#a9742a">PENDING</span>{:else}<span class="ftag" style="background:#eef3ef; color:#5a7a52">ACTIVE</span>{/if}
                  {:else if it.status === 'pending'}<span class="ftag" style="background:#fbf1df; color:#a9742a">PENDING</span>{/if}
                  <span class="fsrc">{it.type === 'fact' ? (it.source === 'chat' ? 'Learned in chat' : it.source === 'doc' ? 'From a document' : it.source === 'feedback' ? 'From a correction' : 'Taught fact') : it.type === 'page' ? `Page${it.page_no != null ? ' ' + it.page_no : ''}` : 'Document'}</span>
                </div>
              {/each}
            </div>
          {:else}
            <div class="dgrid">
              {#each feedItems.filter(factMatch) as it (it.type + '-' + it.id)}
                <div class="fcard group" role="button" tabindex="0"
                     onclick={() => openFeedItem(it)}
                     onkeydown={(e) => { if (e.key === 'Enter') openFeedItem(it); }}>
                  <div class="fhead">
                    <span class="ftype" style="background:{typeTint(it.type)}; color:{typeInk(it.type)}">{typeIcon(it.type)} {typeLabel(it.type)}</span>
                    {#if it.type === 'page' && it.page_no != null}<span class="ftag">p.{it.page_no}</span>{/if}
                    {#if it.type === 'fact'}
                      {@render originBadge(it.source)}
                      {#if factScore(it) != null}<span class="ftag" style="background:{factScore(it)! >= 90 ? '#e8f3ec' : factScore(it)! >= 70 ? '#fbf1df' : '#fbe9e6'}; color:{factScore(it)! >= 90 ? '#3f8f5f' : factScore(it)! >= 70 ? '#a9742a' : '#c0492f'}">{factScore(it)}%</span>{/if}
                    {/if}
                    {#if it.status === 'pending'}<span class="ftag" style="background:#fbf1df; color:#a9742a">PENDING</span>{/if}
                  </div>
                  <div class="ftitle">{it.title}</div>
                  {#if cleanSnippet(it.snippet, it.title)}<div class="fdesc">{cleanSnippet(it.snippet, it.title)}</div>{/if}
                  <div class="fmeta">
                    <span class="fdot" style="background:{typeInk(it.type)}"></span>
                    <span>{it.type === 'fact' ? (it.source === 'chat' ? 'Learned in chat' : it.source === 'doc' ? 'From a document' : it.source === 'feedback' ? 'From a correction' : 'Taught fact') : it.type === 'page' ? 'Page' : 'Document'}</span>
                    {#if it.source && it.type !== 'fact'}<span class="truncate">· {it.source}</span>{/if}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
          {/if}
        </div>
      {/if}
    </section>

  <!-- ===== QUICK-LOOK DRAWER (right slide-over) ===== -->
  {#if peek}
    <button class="peekscrim" onclick={closePeek} aria-label="Close preview"></button>
    <aside class="peekdrawer">
      <button class="peekx" onclick={closePeek} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      <div class="pr-8">
        <h3 class="serif text-[18px] font-medium leading-snug" style="color:var(--ink)">{peekMeta.title || '…'}</h3>
        <p class="text-[12.5px] mt-1" style="color:var(--muted)">
          {catOf(peek) ? catOf(peek) + ' · ' : ''}{(peek.lang || '—').toUpperCase()} · {peek.page_count} pages{peekDetail?.stats ? ` · ${peekDetail.stats.sections} sections` : ''}
        </p>
        <button class="peekopen mt-3" style="width:100%" onclick={openFull}>Open full document →</button>
      </div>

      {#if peekDetail?.stats}
        {@const s = peekDetail.stats}
        {@const rd = s.pages ? Math.round((s.vision_pages / s.pages) * 100) : 0}
        {@const td = s.pages ? Math.round((s.text_pages / s.pages) * 100) : 0}
        <div class="ptiles">
          <div class="ptile"><b>{s.pages}</b><span>pages</span></div>
          <div class="ptile"><b>{s.sections}</b><span>sections</span></div>
          <div class="ptile"><b style="color:{s.used_count > 0 ? 'var(--clay)' : 'var(--muted)'}">{s.used_count}×</b><span>{s.used_count > 0 ? 'used' : 'unused'}</span></div>
          <div class="ptile"><b style="color:{rd >= 100 ? '#5fa463' : '#a9742a'}">{rd}%</b><span>read</span></div>
        </div>
        <div class="prows">
          <div class="prow"><span class="pk2">Uploaded</span><span>{fmtDate(peek.created_at)} · {relTime(peek.created_at)}</span></div>
          {#if peek.ready_at}<div class="prow"><span class="pk2">✓ Ingested</span><span>{relTime(peek.ready_at)}</span></div>{/if}
          <div class="prow"><span class="pk2">Last answered</span><span>{s.last_used_at ? `${relTime(s.last_used_at)} · ${s.used_count} question${s.used_count === 1 ? '' : 's'}` : 'never cited yet'}</span></div>
          {#if s.votes && (s.votes.up || s.votes.down)}<div class="prow"><span class="pk2">Answer feedback</span><span>{s.votes.up} up · {s.votes.down} down</span></div>{/if}
          <div class="prow col">
            <span class="pk2">✓ Extraction</span>
            <div class="pex">
              <div class="pexrow"><span class="pexl">vision</span><span class="pbar"><i style="width:{rd}%; background:#5fa463"></i></span><span class="pexn">{rd}%</span></div>
              <div class="pexrow"><span class="pexl">text</span><span class="pbar"><i style="width:{td}%; background:var(--clay)"></i></span><span class="pexn">{td}%</span></div>
            </div>
            {#if s.weak_pages && s.weak_pages.length}<div class="text-[11px] mt-1.5" style="color:#a9742a">⚠ weak pages (no vision): {s.weak_pages.join(', ')}</div>{/if}
          </div>
          <div class="prow"><span class="pk2">Structure</span><span>{s.sections ? `${s.sections} sections` : 'flat — no sections'}{s.has_tree ? ' · tree ✓' : ''}</span></div>
        </div>
      {/if}

      <button onclick={peekZoom} class="peekprev mt-4" aria-label="Zoom page">
        {#if peekPages.length}
          <img src={api.pageImg(peekPages[peekIdx].page_id)} alt="page {peekPages[peekIdx].page_no}" />
        {:else}
          <span class="peekprev-ph">{peekBusy ? 'Loading…' : 'No pages'}</span>
        {/if}
      </button>

      {#if peekPages.length}
        <div class="peekflip">
          <button onclick={peekPrev} disabled={peekIdx === 0} aria-label="Previous page">‹</button>
          <span class="tnum">page {peekPages[peekIdx].page_no} / {peekPages.length}</span>
          <button onclick={peekNext} disabled={peekIdx >= peekPages.length - 1} aria-label="Next page">›</button>
        </div>
      {/if}

      {#if peekOutline.length}
        <div class="text-[11px] font-semibold uppercase tracking-wide mt-5 mb-1" style="color:var(--muted)">In this document</div>
        <div>
          {#each peekOutline.slice(0, 8) as n}
            <button onclick={() => openFullAt(n.page_no)} class="peeko"><span class="truncate">{n.title || '(untitled)'}</span><b>p.{n.page_no}</b></button>
          {/each}
        </div>
      {/if}

      <div class="peekfooter">
        <button class="peekask" onclick={peekAsk} style="flex:1">Ask doc in chat</button>
      </div>
    </aside>
  {/if}

  <!-- ===== FACT POPUP MODAL (B1 enriched) ===== -->
  {#if selFact}
    {@const f = selFact}
    {@const isChat = f.source === 'chat' || f.source === 'feedback'}
    {@const fd = factDetail}
    {@const used = fd?.cited_count ?? f.cited_count ?? 0}
    {@const lastCited = fd?.last_cited_at ?? f.last_cited_at}
    {@const origin = fd?.origin ?? null}
    {@const citedIn = fd?.cited_in ?? []}
    {@const related = fd?.related ?? []}
    <button class="mscrim" onclick={closeFact} aria-label="Close"></button>
    <div class="mcard" role="dialog" aria-modal="true">
      <button class="mx" onclick={closeFact} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      <!-- 1. Tinted header: source chip + key + pending badge -->
      <div class="mhead" style="background:{factCover(isChat)}">
        <div class="flex items-center gap-2">
          <span class="mchip" style="color:{factInk(isChat)}">{isChat ? 'Learned in chat' : 'Taught fact'}</span>
          {#if f.status === 'pending'}<span class="mpend">Awaiting review</span>{/if}
        </div>
        {#if f.key && !factEditMode}<div class="mkey" style="color:{factInk(isChat)}">{f.key}</div>{/if}
      </div>
      <div class="mbody">
        <!-- 2. Big value -->
        {#if factEditMode}
          <div class="flex flex-col gap-2 mb-3">
            <input bind:value={factEditKey} placeholder="Label (optional)" class="w-full h-9 rounded-[9px] border px-3 text-sm outline-none" style="border-color:var(--border); background:#fff" />
            <textarea bind:value={factEditVal} rows="3" placeholder="Fact value" class="w-full rounded-[9px] border px-3 py-2 text-sm resize-none outline-none" style="border-color:var(--border); background:#fff"></textarea>
          </div>
        {:else}
          <p class="text-[16.5px] leading-relaxed" style="color:var(--ink)">{f.value}</p>
        {/if}
        <!-- 3. Stats grid -->
        <div class="mstats">
          <div class="mstat"><span class="mstat-k">Used</span><span class="mstat-v" style="color:{used > 0 ? factInk(isChat) : 'var(--muted)'}">{used > 0 ? `${used}×` : 'Unused'}</span></div>
          <div class="mstat"><span class="mstat-k">Last cited</span><span class="mstat-v">{lastCited ? relTime(lastCited) : '—'}</span></div>
          <div class="mstat"><span class="mstat-k">Taught</span><span class="mstat-v">{(fd?.created_at ?? f.created_at) ? fmtDateShort(fd?.created_at ?? f.created_at) : '—'}</span></div>
          <div class="mstat"><span class="mstat-k">By</span><span class="mstat-v truncate">{fd?.created_by ?? f.created_by ?? '—'}</span></div>
        </div>
        <!-- 4. Precedence note -->
        <div class="mnote">
          {isChat ? 'Learned automatically from a chat message' : 'Entered by hand in Brain'}{(fd?.created_at ?? f.created_at) ? ' · ' + fmtDate(fd?.created_at ?? f.created_at) : ''}.
          {#if f.status === 'pending'}<span><b>Not used until approved.</b></span>{:else}<span><b>Overrides documents on conflict.</b></span>{/if}
        </div>
        <!-- 5. WHERE IT CAME FROM (B1 new) -->
        {#if origin}
          <div class="msec">
            <div class="msec-h">Where it came from</div>
            {#if origin.kind === 'human'}
              <div class="mcite"> Entered by hand{origin.by ? ` by ${origin.by}` : ''}</div>
            {:else}
              <div class="mcite">&ldquo;{origin.question}&rdquo; — {origin.kind === 'feedback' ? 'correction' : 'chat'}, {fmtDateShort(origin.at)}</div>
            {/if}
          </div>
        {/if}
        <!-- 6. CITED IN ANSWERS (B1 new) -->
        {#if citedIn.length > 0}
          <div class="msec">
            <div class="msec-h">Cited in answers</div>
            {#each citedIn.slice(0, 5) as ci}
              <div class="mcite">• &ldquo;{ci.question}&rdquo; <span style="color:var(--muted)">{relTime(ci.at)}</span></div>
            {/each}
            {#if citedIn.length > 5}<div class="mcite" style="color:var(--muted)">+{citedIn.length - 5} more</div>{/if}
          </div>
        {/if}
        <!-- 7. RELATED (B1 new) -->
        {#if related.length > 0}
          <div class="msec">
            <div class="msec-h">Related</div>
            <div class="flex flex-wrap gap-1.5 mt-1">
              {#each related as rel}
                <button class="mrel-chip" onclick={() => {
                  if (rel.type === 'fact') {
                    const fid = +String(rel.id).replace(/^f/, '');
                    const rf = facts.find((x) => x.id === fid);
                    selectFact(rf || { id: fid, value: rel.label } as Fact);
                  } else {
                    const did = rel.doc_id ?? +String(rel.id).replace(/^[dp]/, '');
                    if (did) goto(readerHref({ doc: did, view: null, pg: null }), { keepFocus: true, noScroll: true });
                    closeFact();
                  }
                }}>
                  <b style="opacity:.6">{typeIcon(rel.type)}</b> {rel.label}
                </button>
              {/each}
            </div>
          </div>
        {/if}
        <!-- Loading indicator for detail fetch -->
        {#if factDetailLoading}
          <div class="text-[11.5px] mt-3" style="color:var(--muted)">Loading details…</div>
        {/if}
        <!-- 8. Footer actions: approve/reject/delete + Edit -->
        <div class="flex justify-end gap-2 mt-5 flex-wrap">
          {#if factEditMode}
            <button onclick={() => (factEditMode = false)} class="mbtn-ghost">Cancel</button>
            <button onclick={() => saveFactEdit(f)} class="mbtn" style="background:var(--clay)">Save</button>
          {:else}
            <button onclick={() => { factEditKey = f.key || ''; factEditVal = f.value || ''; factEditMode = true; }} class="mbtn-ghost">Edit</button>
            {#if f.status === 'pending'}
              <button onclick={() => rejectFact(f)} class="mbtn-ghost">Reject</button>
              <button onclick={() => approveFact(f)} class="mbtn" style="background:var(--clay)">✓ Approve</button>
            {:else}
              <button onclick={() => delFact(f)} class="mbtn-ghost" style="color:#cf6a4c; border-color:#e6c4b8">Delete fact</button>
            {/if}
          {/if}
        </div>
      </div>
    </div>
  {/if}

  <!-- ===== ACTIVITY DETAIL MODAL ===== -->
  {#if actModal}
    {@const n = actModal}
    <button class="mscrim" onclick={() => (actModal = null)} aria-label="Close"></button>
    <div class="mcard sm" role="dialog" aria-modal="true">
      <button class="mx" onclick={() => (actModal = null)} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      <div class="mhead" style="background:{notifTint(n.kind)}">
        <div class="flex items-center gap-2.5">
          <span class="agic">{notifIcon(n.kind)}</span>
          <div>
            <div class="text-[11px] font-semibold uppercase tracking-wide" style="color:var(--muted)">{notifLabel(n.kind)}{#if n._count > 1} · {n._count} times{/if}</div>
            <div class="text-[15px] font-semibold" style="color:var(--ink)">{n.title}</div>
          </div>
        </div>
      </div>
      <div class="mbody">
        {#if n.body}<p class="text-[14px] leading-relaxed" style="color:var(--ink)">{n.body}</p>{/if}
        <div class="mstats" style="grid-template-columns:1fr 1fr">
          <div class="mstat"><span class="mstat-k">When</span><span class="mstat-v">{relTime(n.created_at)}</span></div>
          <div class="mstat"><span class="mstat-k">Exact</span><span class="mstat-v" style="font-size:11.5px">{fullTime(n.created_at)}</span></div>
        </div>
        {#if n._count > 1}<div class="mnote">This row groups <b>{n._count}</b> consecutive {notifLabel(n.kind).toLowerCase()} events.</div>{/if}
      </div>
    </div>
  {/if}

  <!-- ===== SYNC PROGRESS (folder / multi-file upload loop) ===== -->
  {#if sync}
    {@const pct = sync.total ? Math.round((sync.done / sync.total) * 100) : 0}
    <div class="synccard" class:done={sync.finished}>
      <div class="sync-top">
        {#if sync.finished}
          <span class="sync-ic ok">✓</span>
          <span class="sync-ttl">{sync.total} {sync.folder ? 'files queued' : 'queued'} · indexing now</span>
        {:else}
          <span class="sync-ic spin">⟳</span>
          <span class="sync-ttl">{sync.folder ? 'Syncing folder…' : 'Uploading…'}</span>
          <span class="sync-cnt">{sync.done} / {sync.total}</span>
        {/if}
      </div>
      <div class="sync-track"><span class="sync-fill" style="width:{sync.finished ? 100 : pct}%"></span></div>
      {#if !sync.finished && sync.cur}<div class="sync-cur">uploading <b>{sync.cur}</b></div>{/if}
    </div>
  {/if}

  <!-- ===== JOBS SLIDE-OVER (right drawer, all ingest activity) ===== -->
  {#if jobsOpen}
    <button class="jobs-scrim" onclick={() => (jobsOpen = false)} aria-label="Close jobs"></button>
    <aside class="jobs-panel" role="dialog" aria-modal="true">
      <div class="jobs-head">
        <h3 class="jobs-ttl">Jobs</h3>
        <button class="jobs-x" onclick={() => (jobsOpen = false)} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
      <!-- summary -->
      <div class="jobs-sum">
        <div class="jobs-sum-top">
          <span class="jp-spin" class:still={remaining === 0}>⟳</span>
          <span class="jobs-sum-ttl">{remaining > 0 ? `Indexing · ${remaining} remaining` : 'All caught up'}</span>
          <span class="jobs-sum-pct">{overallPct}%</span>
        </div>
        <div class="proc-overall"><span class="proc-overall-fill" style="width:{overallPct}%"></span></div>
        <div class="proc-lanes">
          <span class="lane">Render <b>{lanes.Render}</b></span>
          <span class="lane">Read <b>{lanes.Read}</b></span>
          <span class="lane">Structure <b>{lanes.Structure}</b></span>
          <span class="lane">Compile <b>{lanes.Compile}</b></span>
          <span class="lane">Tag <b>{lanes.Tag}</b></span>
          <span class="lane q">Queued <b>{queuedN}</b></span>
          <span class="lane ok">✓ {readyN}</span>
          {#if failedN}<span class="lane fail">✗ {failedN}</span>{/if}
        </div>
      </div>

      <div class="jobs-body">
        <!-- ACTIVE -->
        {#if procDocs.length}
          <div class="jobs-grp-h">Active · {procDocs.length}</div>
          {#each procDocs as d (d.id)}
            {@const cur = stageOf(d)}
            <div class="jrow" role="button" tabindex="0" onclick={() => toggleFlow(d)} onkeydown={(e) => { if (e.key === 'Enter') toggleFlow(d); }}>
              <span class="jdot live"></span>
              <span class="jname" title={parseDocName(d.name).title}>{parseDocName(d.name).title}</span>
              <span class="jstage">{cur.label}{cur.sub ? ` ${cur.sub}` : ''}</span>
              <span class="jpct">{d.progress ?? 0}%</span>
              <button class="proc-x" title="Cancel" onclick={(e) => { e.stopPropagation(); cancelDoc(d); }} aria-label="Cancel">⊘</button>
            </div>
            <div class="jbar"><span class="jbar-fill" style="width:{d.progress ?? 0}%"></span></div>
            {#if flowOpen === d.id}
              <div class="flowpanel inline">
                {@render stepper(d)}
                {#if flowLog.length}
                  <div class="flowlog">
                    {#each flowLog as l (l.ts + l.msg)}
                      <div class="flowlog-row"><span class="flowlog-ts">{new Date(l.ts).toLocaleTimeString()}</span><span class="flowlog-msg">{l.msg}</span></div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/if}
          {/each}
        {/if}

        <!-- QUEUED -->
        {#if queuedDocs.length}
          <div class="jobs-grp-h">
            Queued · {queuedDocs.length}
            {#if queuedDocs.length > 6}<input class="jsearch" placeholder="search…" bind:value={jobQ} />{/if}
          </div>
          {#each queuedShown.slice(0, 60) as d, i (d.id)}
            <div class="jrow q">
              <span class="jpos">#{i + 1}</span>
              <span class="jname" title={parseDocName(d.name).title}>{parseDocName(d.name).title}</span>
              <button class="proc-x" title="Cancel" onclick={() => cancelDoc(d)} aria-label="Cancel">⊘</button>
            </div>
          {/each}
          {#if queuedShown.length > 60}<div class="jmore">+ {queuedShown.length - 60} more queued</div>{/if}
        {/if}

        <!-- FAILED -->
        {#if failedDocs.length}
          <div class="jobs-grp-h fail">✗ Failed · {failedDocs.length}</div>
          {#each failedDocs as d (d.id)}
            <div class="jrow">
              <span class="jdot bad"></span>
              <span class="jname" title={parseDocName(d.name).title}>{parseDocName(d.name).title}</span>
              <button class="jretry" onclick={() => retryDoc(d)}>↻ Retry</button>
            </div>
          {/each}
        {/if}

        <!-- DONE -->
        {#if readyRecent.length}
          <div class="jobs-grp-h">Done recently</div>
          {#each readyRecent as d (d.id)}
            <div class="jrow done">
              <span class="jdot ok"></span>
              <span class="jname" title={parseDocName(d.name).title}>{parseDocName(d.name).title}</span>
              <span class="jago">{fmtDay(d.ready_at || d.updated_at)}</span>
            </div>
          {/each}
        {/if}

        {#if !procDocs.length && !queuedDocs.length && !failedDocs.length && !readyRecent.length}
          <div class="jobs-empty">No ingest activity yet. Upload documents to see jobs here.</div>
        {/if}
      </div>
    </aside>
  {/if}

  <!-- ===== TEACH-A-FACT MODAL ===== -->
  {#if teachOpen}
    <button class="mscrim" onclick={() => (teachOpen = false)} aria-label="Close"></button>
    <div class="teachcard" role="dialog" aria-modal="true">
      <button class="teach-x" onclick={() => (teachOpen = false)} aria-label="Close">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
      <div class="teach-head">
        <div class="teach-ic">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg>
        </div>
        <div>
          <h3 class="teach-ttl">Teach a fact</h3>
          <p class="teach-sub">A rule or correction that isn't in any document — Aria recalls it in every answer.</p>
        </div>
      </div>

      <label class="teach-lbl" for="tf-key">Label <span class="teach-opt">optional</span></label>
      <input id="tf-key" bind:value={tKey} placeholder="e.g. helpdesk" class="teach-inp" />

      <label class="teach-lbl" for="tf-val">Fact</label>
      <textarea id="tf-val" bind:value={tVal} rows="4" placeholder="e.g. Refund window is 14 days from delivery date." class="teach-inp teach-area"></textarea>
      <div class="teach-tip">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
        Keep it short and specific — one fact per entry. Approved facts override the documents on conflict.
      </div>

      <div class="teach-foot">
        <button onclick={() => (teachOpen = false)} class="teach-cancel">Cancel</button>
        <button onclick={teach} disabled={tBusy || !tVal.trim()} class="teach-save">
          {#if tBusy}Teaching…{:else}Teach Aria{/if}
        </button>
      </div>
    </div>
  {/if}
  </div><!-- /content column -->
</div>

<style>
  .panel{background:#fff; border:1px solid var(--border); border-radius:14px;}
  /* ===== knowledge graph (dark focused viz surface) ===== */
  .graphwrap{position:relative; width:100%; height:100%; min-height:520px; background:#1a1a1a;}
  .graphcanvas{position:absolute; inset:0; width:100%; height:100%;}
  .goverlay{position:absolute; z-index:5; background:rgba(28,28,28,.78); border:1px solid #333; backdrop-filter:blur(6px);}
  .gtl{top:14px; left:14px; display:flex; gap:6px; padding:7px; border-radius:11px;}
  .gbr{bottom:14px; right:16px; padding:5px 10px; border-radius:8px; font-size:11.5px; color:#9aa0a6; font-variant-numeric:tabular-nums;}
  .gtog{display:flex; align-items:center; gap:6px; padding:5px 10px; border-radius:8px; font-size:12px; color:#8a8f95; background:transparent; cursor:pointer; transition:all .12s; opacity:.45;}
  .gtog:hover{color:#ddd;}
  .gtog.on{color:#eee; background:#2d2d2d; opacity:1;}
  .gdot{width:8px; height:8px; border-radius:999px; display:inline-block; flex-shrink:0;}
  .gtr{top:14px; right:16px; display:flex; flex-wrap:wrap; justify-content:flex-end; gap:4px; padding:5px; border-radius:11px; max-width:min(64%,520px);}
  .ghue{font-size:10.5px; color:#6f747a; padding:5px 6px; letter-spacing:.02em; align-self:center;}
  .gdot.gdoc{background:hsl(265,58%,58%);}
  .gdot.gpg{background:hsl(265,42%,72%);}
  /* ---- left node-detail panel (slide-in over the dark canvas) ---- */
  .gpanel{position:absolute; z-index:7; top:0; left:0; bottom:0; width:340px; max-width:80%; background:rgba(26,26,26,.97); border-right:1px solid #333; backdrop-filter:blur(8px); display:flex; flex-direction:column; color:#d6d6d6; box-shadow:6px 0 24px rgba(0,0,0,.4); animation:gslide .16s ease;}
  @keyframes gslide{from{transform:translateX(-12px); opacity:.4;} to{transform:none; opacity:1;}}
  .gpan-head{display:flex; align-items:center; justify-content:space-between; padding:12px 14px 8px;}
  .gpan-type{font-size:10px; letter-spacing:.06em; font-weight:600; padding:3px 8px; border-radius:6px; background:#2d2d2d; color:#9aa0a6;}
  .gpan-doc{color:#cf99e0;} .gpan-page{color:#aeb3b8;} .gpan-fact{color:#ec8cc0;}
  .gpan-x{font-size:14px; color:#8a8f95; padding:2px 6px; border-radius:6px; cursor:pointer; background:transparent;}
  .gpan-x:hover{color:#fff; background:#2d2d2d;}
  .gpan-load{flex:1; display:flex; align-items:center; justify-content:center;}
  .gpan-body{flex:1; overflow-y:auto; padding:0 14px 16px;}
  .gpan-err{font-size:12px; color:#e0826a; margin-bottom:8px;}
  .gpan-title{font-size:15px; font-weight:600; color:#f0f0f0; line-height:1.3; margin-bottom:3px;}
  .gpan-sub{font-size:12px; color:#9aa0a6; margin-bottom:10px;}
  .gpan-img{width:100%; border-radius:8px; border:1px solid #333; margin:6px 0 12px; background:#222;}
  .gpan-summary{font-size:12.5px; line-height:1.55; color:#c0c0c0; margin-bottom:12px; white-space:pre-wrap;}
  .gpan-stats{display:flex; flex-direction:column; gap:1px; background:#333; border:1px solid #333; border-radius:8px; overflow:hidden; margin-bottom:12px;}
  .gpan-stat{display:flex; align-items:center; justify-content:space-between; padding:7px 10px; background:#222; font-size:12px;}
  .gpan-sl{color:#9aa0a6;} .gpan-sv{color:#e6e6e6; font-weight:600; font-variant-numeric:tabular-nums;}
  .gpan-actions{margin-bottom:14px;}
  .gpan-btn{width:100%; font-size:12.5px; padding:8px 12px; border-radius:8px; color:#fff; background:#1a1a18; cursor:pointer; text-align:center;}
  .gpan-btn:hover{background:#4f76a6;}
  .gpan-linkhdr{font-size:10.5px; letter-spacing:.05em; color:#8a8f95; text-transform:uppercase; margin-bottom:6px;}
  .gpan-links{display:flex; flex-direction:column; gap:3px;}
  .gpan-nb{display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:7px; background:transparent; cursor:pointer; text-align:left; transition:background .1s;}
  .gpan-nb:hover{background:#2d2d2d;}
  .gpan-nb-l{font-size:12px; color:#cfcfcf; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  /* ---- "How this connects": grouped relations with plain-English explanations ---- */
  .gpan-connhdr{font-size:11px; letter-spacing:.04em; font-weight:700; color:#e6e6e6; margin:2px 0 10px;}
  .gpan-rels{display:flex; flex-direction:column; gap:12px; margin-bottom:14px;}
  .gpan-rel{border-left:2px solid #3a3a3a; padding-left:10px;}
  .gpan-rel-top{display:flex; align-items:center; gap:7px; margin-bottom:2px;}
  .gpan-rel-ico{font-size:13px; line-height:1;}
  .gpan-rel-title{font-size:12.5px; font-weight:600; color:#ededed;}
  .gpan-rel-why{font-size:11px; line-height:1.45; color:#8f9499; margin-bottom:7px;}
  .gpan-chips{display:flex; flex-wrap:wrap; gap:5px;}
  .gpan-chip{display:inline-flex; align-items:center; gap:6px; max-width:100%; padding:4px 9px; border-radius:7px; background:#2a2a2a; cursor:pointer; transition:background .1s;}
  .gpan-chip:hover{background:#373737;}
  .gpan-chip-l{font-size:11.5px; color:#d2d2d2; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:200px;}
  .gpan-legend{font-size:10px; line-height:1.5; color:#6f7479; border-top:1px solid #2d2d2d; padding-top:8px; margin-bottom:12px;}
  .gpan-noconn{font-size:12px; color:#8a8f95; line-height:1.5; margin-bottom:12px;}
  .gpan-summary-b{border-top:1px solid #2d2d2d; padding-top:11px;}
  .gcenter{position:absolute; inset:0; z-index:4; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; color:#9aa0a6; font-size:13px; pointer-events:none;}
  .gcenter .gretry{pointer-events:auto;}
  .gretry{margin-top:6px; font-size:12.5px; padding:6px 14px; border-radius:8px; color:#fff; background:#1a1a18; cursor:pointer;}
  .gspin{width:26px; height:26px; border-radius:999px; border:3px solid #333; border-top-color:#9aa0a6; animation:gspin .8s linear infinite;}
  @keyframes gspin{to{transform:rotate(360deg);}}
  .btn{height:36px; padding:0 14px; border-radius:9px; font-size:13px; border:1px solid var(--border); background:#fff; cursor:pointer; color:#46443f; white-space:nowrap;}
  .btn:hover{background:#efefec;}
  .seg2{padding:4px 9px; font-size:12px; border-radius:6px; color:var(--muted); background:transparent; transition:all .12s; white-space:nowrap;}
  .seg2.on{background:var(--navpill); color:var(--ink); font-weight:600;}
  /* ===== left sub-rail (Brain / Graph / Audit + Library) ===== */
  .brail{display:flex; align-items:center; gap:10px; width:100%; height:36px; padding:0 12px; border-radius:9px; font-size:14px; color:#46443f; transition:background .12s; text-align:left; background:none; border:none; cursor:pointer;}
  .brail:hover{background:var(--hover);}
  .brail.on{background:var(--navpill); color:var(--ink); font-weight:600;}
  .brail .bn{margin-left:auto; font-size:11px; color:var(--muted);}
  .brail-grp{padding:0 8px 6px; font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);}
  /* Design-1 FLAT feed card (no cover, no ghost title) */
  .fcard{position:relative; display:flex; flex-direction:column; gap:0; background:#fff; border:1px solid var(--border); border-radius:13px; padding:15px 16px; cursor:pointer; transition:border-color .14s, background .14s; min-width:0; min-height:122px;}
  .fcard.op{opacity:.6;}
  .fdate{margin-left:auto; font-size:10.5px; color:var(--muted); white-space:nowrap;}
  .dacts2{display:flex; gap:7px; margin-top:11px;}
  .fcard:hover{border-color:var(--muted); background:#fcfcfb;}
  .fhead{display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:9px;}
  .ftype{font-size:9.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; padding:2px 8px; border-radius:6px; white-space:nowrap;}
  .ftitle{font-family:var(--serif); font-size:15.5px; font-weight:600; color:var(--ink); line-height:1.32; overflow-wrap:anywhere;}
  .fflag{font-size:10px; font-weight:700; letter-spacing:.03em; padding:2px 7px; border-radius:6px; white-space:nowrap;}
  .fdesc{font-size:12px; color:var(--muted); line-height:1.45; margin-top:5px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;}
  .fmeta{display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--muted); margin-top:auto; padding-top:11px; min-width:0;}
  .fsep{color:var(--border);}
  .fsub{font-size:11.5px; color:var(--muted); margin-top:4px;}
  .fdot{width:7px; height:7px; border-radius:50%; flex:none;}
  .fmeta .truncate{white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}

  /* combined Brain+Audit top-tabs (embedded) */
  .bktabs{display:flex; gap:4px; margin:2px 0 12px;}
  .bktab{display:inline-flex; align-items:center; gap:6px; padding:8px 14px; border-radius:9px; font-size:14px; font-weight:500; color:#46443f; background:transparent; border:none; cursor:pointer; transition:background .14s, color .14s;}
  .bktab:hover{background:var(--hover, #efefec); color:var(--ink);}
  .bktab.on{background:var(--navpill, #f0efed); color:var(--ink); font-weight:700;}
  .bktab-badge{font-size:11px; font-weight:700; color:var(--muted); background:var(--sand); border-radius:6px; padding:0 6px;}
  /* ===== text tab strip (underline, like Settings) ===== */
  .tabstrip{display:flex; gap:26px; border-bottom:1px solid var(--border); margin-bottom:14px;}
  .tab{position:relative; padding:7px 1px 11px; font-size:14.5px; font-weight:500; color:var(--muted); cursor:pointer; background:none; border:none; display:flex; align-items:center; gap:7px; transition:color .12s;}
  .tab:hover{color:var(--ink);}
  .tab.on{color:var(--clay); font-weight:600;}
  .tab.on::after{content:""; position:absolute; left:0; right:0; bottom:-1px; height:2px; background:var(--clay); border-radius:2px;}
  .tabn{font-size:12.5px; font-weight:600; color:var(--muted); font-variant-numeric:tabular-nums;}
  .tab.on .tabn{color:var(--clay);}
  .tabpill{font-size:10.5px; font-weight:700; color:#fff; background:#5fa463; border-radius:999px; padding:0 5px;}
  /* ===== filter panel ===== */
  .filterbar{display:flex; align-items:center; gap:10px; flex-wrap:wrap; background:#fff; border:1px solid var(--border); border-radius:12px; padding:10px 14px; box-shadow:0 4px 20px rgba(0,0,0,.03);}
  .fbar-div{width:1px; height:22px; background:var(--line);}
  .ds-pill{display:inline-block; font-size:10.5px; line-height:1.4; padding:1px 7px; border-radius:999px; white-space:nowrap;}
  .ds-muted{background:#f0efed; color:#8a6a59;}
  .ds-track{display:block; height:3px; border-radius:999px; background:#f0efed; overflow:hidden; max-width:150px;}
  .ds-fill{display:block; height:100%; border-radius:999px; background:var(--clay); transition:width .3s ease;}
  .ftprose{font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; font-size:14px; line-height:1.7; max-width:72ch; white-space:pre-wrap; word-break:break-word; color:#2b2a27;}
  :global(.ftprose mark.ftm){ background:#f0efed; color:var(--ink); border-radius:3px; padding:0 1px; }
  :global(.ftprose mark.cur){ background:#dcdcd8; outline:1px solid var(--clay); }
  .scroll-mt-20{ scroll-margin-top:5rem; }
  /* cards (docs + facts grid) */
  .card{position:relative; display:block; text-align:left; background:#fff; border:1px solid var(--border); border-radius:12px; padding:14px; transition:transform .14s, box-shadow .14s, border-color .14s;}
  .card:hover{transform:translateY(-2px); box-shadow:0 6px 18px rgba(0,0,0,.08); border-color:var(--border);}
  .fstat{font-size:12.5px; color:var(--muted); background:#fff; border:1px solid var(--border); border-radius:9px; padding:6px 11px;}
  .fstat b{font-weight:700;}
  /* feed chip filter (All / Docs / Facts) */
  .chip{font-size:12.5px; padding:5px 13px; border-radius:999px; color:#6f6c65; background:#fff; border:1px solid var(--border); cursor:pointer; transition:all .12s; white-space:nowrap;}
  .chip:hover{border-color:var(--border); color:var(--ink); background:var(--hover);}
  .chip.on{background:#f3f3f1; border-color:#dcdcd8; color:var(--clay); font-weight:600;}
  /* segmented control */
  .seg-group{display:inline-flex; padding:2px; border-radius:9px; background:#f0efed; gap:2px;}
  .segb{font-size:11.5px; padding:3px 11px; border-radius:7px; color:#6f6c65; background:transparent; transition:all .12s;}
  .segb:hover{color:var(--ink);}
  .segb.on{background:#fff; color:var(--clay); font-weight:600; box-shadow:0 1px 2px rgba(0,0,0,.05);}
  .backlink{font-size:12.5px; color:var(--clay); display:inline-flex; align-items:center; gap:4px;}
  .backlink:hover{text-decoration:underline;}
  /* skeleton */
  .sk{background:linear-gradient(90deg,#ece8df,#f4f0e9,#ece8df); background-size:200% 100%; animation:shimmer 1.2s infinite; border-radius:6px;}
  @keyframes shimmer{0%{background-position:200% 0} 100%{background-position:-200% 0}}
  .sk-ic{width:36px; height:36px; border-radius:9px; margin-bottom:12px;}
  .sk-l1{height:12px; width:90%; margin-bottom:8px;}
  .sk-l2{height:10px; width:50%;}
  .tnum{font-variant-numeric:tabular-nums;}

  /* ===== cover-card grid (Option A) ===== */
  .dgrid{display:grid; grid-template-columns:repeat(auto-fill,minmax(158px,1fr)); gap:12px;}
  /* compact feed list view */
  .flist{display:flex; flex-direction:column; gap:6px;}
  .frow{display:flex; align-items:center; gap:11px; background:#fff; border:1px solid var(--border); border-radius:10px; padding:9px 13px; cursor:pointer; transition:border-color .12s, box-shadow .12s, background .12s;}
  .frow:hover{border-color:var(--border); box-shadow:0 2px 8px rgba(0,0,0,.05); background:var(--hover);}
  .fbadge{flex-shrink:0; width:26px; height:26px; border-radius:7px; display:grid; place-items:center; font-size:12px; font-weight:700; letter-spacing:.02em;}
  .ftitle{font-size:13.5px; font-weight:600; color:var(--ink); line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .fsnip{font-size:11.5px; color:var(--muted); line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:1px;}
  .ftag{flex-shrink:0; font-size:9.5px; font-weight:700; letter-spacing:.03em; padding:3px 7px; border-radius:6px; white-space:nowrap;}
  .ftag.origin{display:inline-flex; align-items:center; gap:3px;}
  .ftag.origin svg{width:10px; height:10px;}
  .fsrc{flex-shrink:0; font-size:11.5px; color:var(--muted); white-space:nowrap; min-width:78px; text-align:right;}
  .dcard{position:relative; background:#fff; border:1px solid var(--border); border-radius:14px; overflow:hidden; cursor:pointer; transition:transform .14s, box-shadow .14s, border-color .14s;}
  .dcard:hover{transform:translateY(-3px); box-shadow:0 10px 26px rgba(0,0,0,.1); border-color:var(--border);}
  .dcard.op{opacity:.72; cursor:default;}
  .dcard.op:hover{transform:none; box-shadow:none; border-color:var(--border);}
  .dcover{position:relative; height:78px; background:linear-gradient(160deg,#fafafa,#f0efed); border-bottom:1px solid var(--line); overflow:hidden; display:grid; place-items:center;}
  .dcode{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; font-size:22px; font-weight:600; letter-spacing:.03em;}
  .dcover-ttl{font-family:Georgia,'Times New Roman',serif; font-size:13px; font-weight:500; line-height:1.22; text-align:center; padding:0 12px; max-width:100%; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;}
  .dglyph{position:absolute; right:11px; bottom:8px; font-size:14px; opacity:.45; z-index:2;}
  .dcover-ph{font-size:12px; color:#9a8e7f; display:flex; flex-direction:column; align-items:center; gap:2px;}
  .dcover-ph.fail{color:var(--err); font-weight:600;}
  .dcover-top{position:absolute; top:10px; left:11px; right:10px; z-index:3; display:flex; align-items:flex-start; justify-content:space-between; gap:8px;}
  .ddate{font-size:9.5px; font-weight:700; letter-spacing:.02em; padding:3px 7px; border-radius:5px; background:rgba(255,255,255,.82); -webkit-backdrop-filter:blur(3px); backdrop-filter:blur(3px); color:#6f6c65; font-variant-numeric:tabular-nums; white-space:nowrap;}
  .dfor{position:absolute; left:11px; bottom:9px; z-index:3; font-size:9.5px; letter-spacing:.04em; color:#7d7264; background:rgba(255,255,255,.72); padding:1px 6px; border-radius:4px;}
  .dlang{font-size:9.5px; font-weight:700; letter-spacing:.03em; padding:3px 7px; border-radius:5px; background:#e3edf7; color:#3a6b9c;}
  .dlang.my{background:#fbecd4; color:#a9742a;}
  .dacts{position:absolute; inset:0; display:flex; align-items:center; justify-content:center; gap:8px; opacity:0; background:rgba(43,42,39,.18); transition:opacity .14s; z-index:3;}
  .dcard:hover .dacts{opacity:1;}
  .dacts button{font-size:12px; padding:7px 12px; border-radius:9px; border:none; cursor:pointer; font-weight:600;}
  .peekbtn{background:#fff; color:var(--ink);}
  .openbtn{background:#3a3833; color:#fff;}
  .openbtn:hover{background:var(--ink);}
  .dmeta2{padding:11px 13px 13px;}
  .dttl{font-size:13px; font-weight:600; line-height:1.32; min-height:34px; color:var(--ink); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;}
  .dsub{display:flex; align-items:center; gap:5px; margin-top:7px; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
  .dpp{margin-left:auto;}
  .dstats{display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-top:11px; padding-top:10px; border-top:1px solid var(--line);}
  .dstat{display:flex; flex-direction:column; gap:2px; min-width:0;}
  .dstat-k{font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#a89b8c;}
  .dstat-v{font-size:12px; font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .ddel{position:absolute; top:9px; right:9px; z-index:4; width:24px; height:24px; display:grid; place-items:center; border:none; border-radius:7px; background:rgba(255,255,255,.92); color:var(--muted); cursor:pointer; opacity:0; transition:opacity .12s; box-shadow:0 1px 3px rgba(0,0,0,.12);}
  .dcard:hover .ddel{opacity:1;}
  .ddel:hover{background:#fff; color:var(--err);}

  /* ===== list view ===== */
  .dlist{width:100%; background:#fff; border:1px solid var(--border); border-radius:12px; overflow:hidden;}
  .dlist-head,.dlrow{display:grid; grid-template-columns:minmax(180px,1fr) 52px 50px 50px 76px 78px 78px 96px 84px; align-items:center; gap:10px; padding:0 14px;}
  .dlist-head{height:34px; background:#f7f7f5; border-bottom:1px solid var(--line); font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#a89b8c;}
  .dlrow{min-height:54px; padding-top:8px; padding-bottom:8px; border-top:1px solid var(--line); cursor:pointer; transition:background .12s;}
  .dlrow:first-of-type{border-top:none;}
  .dlrow:hover{background:#efefec;}
  .dlrow.op{opacity:.6; cursor:default;}
  .dlrow.op:hover{background:transparent;}
  .dl-c-title{display:flex; align-items:center; gap:11px; min-width:0;}
  .dl-glyph{font-size:16px; opacity:.6; flex-shrink:0;}
  .dl-tile{display:flex; align-items:center; justify-content:center; width:34px; height:34px; flex:none; border-radius:9px;}
  .dl-ttl{display:block; font-size:13.5px; font-weight:600; color:var(--ink); line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .dl-sub{display:block; font-size:11px; color:var(--muted); margin-top:1px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .dl-c-lang{justify-self:start;}
  .dl-c-pages,.dl-c-used,.dl-c-acc{font-size:12.5px; font-weight:600; color:var(--ink); justify-self:start;}
  .dl-c-date{font-size:12px; font-weight:500; color:var(--muted); justify-self:start; white-space:nowrap;}
  .dl-c-by{font-size:12px; font-weight:500; color:var(--muted); justify-self:start; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .dl-c-act{display:flex; align-items:center; justify-content:flex-end; gap:4px;}
  .dl-open{font-size:12px; font-weight:600; color:#fff; background:#3a3833; border:none; border-radius:7px; padding:5px 10px; cursor:pointer; white-space:nowrap; opacity:0; transition:opacity .12s, background .12s;}
  .dlrow:hover .dl-open{opacity:1;}
  .dl-open:hover{background:var(--ink);}
  .dl-icon{width:26px; height:26px; display:grid; place-items:center; border:none; border-radius:7px; background:transparent; color:var(--muted); cursor:pointer; opacity:0; transition:opacity .12s;}
  .dlrow:hover .dl-icon{opacity:1;}
  .dl-icon:hover{background:#f0efed; color:var(--err);}

  /* ===== quick-look drawer ===== */
  .peekscrim{position:fixed; inset:0; z-index:45; background:rgba(40,35,30,.34); backdrop-filter:blur(2px); border:none; cursor:default;}
  .peekdrawer{position:fixed; z-index:46; top:0; right:0; bottom:0; width:480px; max-width:92vw; background:#fff; box-shadow:-14px 0 50px rgba(40,30,20,.18); padding:24px; overflow-y:auto; animation:slidein .22s ease;}
  @keyframes slidein{from{transform:translateX(40px); opacity:.4} to{transform:translateX(0); opacity:1}}
  .peekx{position:absolute; top:16px; right:16px; width:30px; height:30px; border-radius:8px; border:none; background:#f0efed; cursor:pointer; font-size:14px; color:#46443f;}
  .peekx:hover{background:#e6e6e3;}
  .peekprev{display:block; width:100%; padding:0; border:1px solid var(--border); border-radius:10px; height:320px; background:#f7f7f5; overflow:hidden; cursor:zoom-in;}
  .peekprev img{width:100%; height:100%; object-fit:contain; background:#f7f7f5;}
  .peekprev-ph{display:grid; place-items:center; height:100%; color:var(--muted); font-size:13px;}
  .peekflip{display:flex; align-items:center; justify-content:center; gap:14px; margin-top:12px; color:var(--muted); font-size:12.5px;}
  .peekflip button{width:30px; height:30px; border-radius:8px; border:1px solid var(--border); background:#fff; cursor:pointer; font-size:15px; color:var(--ink);}
  .peekflip button:disabled{opacity:.4; cursor:default;}
  .peeko{width:100%; display:flex; align-items:baseline; justify-content:space-between; gap:12px; padding:8px 10px; border-radius:8px; font-size:13px; color:var(--ink); background:none; border:none; cursor:pointer; text-align:left;}
  .peeko:hover{background:#efefec;}
  .peeko b{color:var(--muted); font-weight:500; flex-shrink:0; font-variant-numeric:tabular-nums;}
  .peekfooter{display:flex; gap:8px; margin-top:20px;}
  .peekopen{flex:1; background:var(--clay); color:#fff; border:none; border-radius:9px; padding:11px; font-size:13px; font-weight:600; cursor:pointer;}
  .peekask{background:#fff; border:1px solid var(--border); border-radius:9px; padding:11px 16px; font-size:13px; cursor:pointer; color:#46443f;}
  /* drawer insights */
  .ptiles{display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:16px;}
  .ptile{background:#f7f7f5; border:1px solid var(--border); border-radius:10px; padding:10px 6px; text-align:center;}
  .ptile b{display:block; font-family:Georgia,'Times New Roman',serif; font-size:21px; line-height:1; color:var(--ink); font-variant-numeric:tabular-nums;}
  .ptile span{display:block; font-size:9.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-top:4px;}
  .prows{margin-top:14px; display:flex; flex-direction:column; gap:11px;}
  .prow{display:flex; align-items:baseline; justify-content:space-between; gap:14px; font-size:12.5px; color:var(--ink);}
  .prow.col{flex-direction:column; align-items:stretch; gap:7px;}
  .pk2{color:var(--muted); flex-shrink:0;}
  .pex{display:flex; flex-direction:column; gap:6px;}
  .pexrow{display:flex; align-items:center; gap:8px; font-size:11.5px; color:var(--muted);}
  .pexl{width:42px;}
  .pexn{width:36px; text-align:right; font-variant-numeric:tabular-nums;}
  .pbar{flex:1; height:6px; border-radius:999px; background:#ece9e0; overflow:hidden;}
  .pbar i{display:block; height:100%; border-radius:999px;}
  .zbtn{width:28px; height:28px; border-radius:7px; border:1px solid var(--border); background:#fff; color:var(--ink); font-size:16px; line-height:1; cursor:pointer;}
  .zbtn:hover{background:#efefec;}
  .fsort{font-size:12px; padding:4px 9px; border-radius:8px; color:#6f6c65; background:#fff; border:1px solid var(--border); outline:none; cursor:pointer;}

  /* ===== activity feed (clickable rows) ===== */
  .actlist{display:flex; flex-direction:column; gap:7px;}
  .actrow{display:flex; align-items:center; gap:12px; width:100%; text-align:left; background:#fff; border:1px solid var(--border); border-radius:11px; padding:11px 13px; cursor:pointer; transition:transform .12s, box-shadow .12s, border-color .12s;}
  .actrow:hover{transform:translateY(-1px); box-shadow:0 6px 16px rgba(0,0,0,.1); border-color:var(--border);}
  .actic{display:grid; place-items:center; width:34px; height:34px; border-radius:9px; flex-shrink:0; font-size:15px;}
  .actbody{display:flex; flex-direction:column; min-width:0; flex:1; gap:2px;}
  .actttl{display:flex; align-items:center; gap:7px;}
  .acttag{font-size:9.5px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);}
  .actx{font-size:10px; font-weight:700; color:#fff; background:#9aa7b5; border-radius:999px; padding:0 6px;}
  .acthead{font-size:13.5px; font-weight:600; color:var(--ink); line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .actsub{font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .acttime{flex-shrink:0; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;}
  .actchev{flex-shrink:0; color:#c2bcb1; transition:transform .12s, color .12s;}
  .actrow:hover .actchev{color:var(--clay); transform:translateX(2px);}

  /* ===== popup modal (facts + activity) ===== */
  .mscrim{position:fixed; inset:0; z-index:55; background:rgba(40,35,30,.4); -webkit-backdrop-filter:blur(2px); backdrop-filter:blur(2px); border:none; cursor:default;}
  /* Teach-a-fact modal (redesign) */
  .teachcard{position:fixed; z-index:56; top:50%; left:50%; transform:translate(-50%,-50%); width:440px; max-width:93vw; background:#fff; border:1px solid var(--border); border-radius:16px; box-shadow:0 18px 50px rgba(40,35,30,.22); padding:22px 24px;}
  .teach-x{position:absolute; top:14px; right:14px; width:30px; height:30px; display:grid; place-items:center; border-radius:8px; border:none; background:transparent; color:var(--muted); cursor:pointer; transition:background .14s, color .14s;}
  .teach-x:hover{background:var(--hover,#efefec); color:var(--ink);}
  .teach-head{display:flex; align-items:flex-start; gap:13px; margin-bottom:18px; padding-right:24px;}
  .teach-ic{flex:none; width:40px; height:40px; border-radius:11px; display:grid; place-items:center; background:#fbeee7; color:var(--clay);}
  .teach-ttl{font-family:var(--serif); font-size:19px; font-weight:600; color:var(--ink); line-height:1.2;}
  .teach-sub{font-size:12.5px; color:var(--muted); margin-top:3px; line-height:1.5;}
  .teach-lbl{display:block; font-size:11.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; color:#6f6c65; margin:0 0 6px;}
  .teach-opt{text-transform:none; letter-spacing:0; font-weight:500; color:var(--muted);}
  .teach-inp{width:100%; border:1px solid var(--border); border-radius:10px; padding:10px 12px; font-size:13.5px; background:#fff; color:var(--ink); outline:none; margin-bottom:14px; transition:border-color .14s, box-shadow .14s;}
  .teach-inp:focus{border-color:var(--clay); box-shadow:0 0 0 3px rgba(194,104,63,.12);}
  .teach-area{resize:none; line-height:1.5;}
  .teach-tip{display:flex; align-items:flex-start; gap:7px; font-size:11.5px; color:var(--muted); line-height:1.5; background:var(--cream,#faf9f5); border:1px solid var(--border); border-radius:9px; padding:8px 11px; margin-bottom:18px;}
  .teach-tip svg{flex:none; margin-top:1px; color:var(--clay);}
  .teach-foot{display:flex; justify-content:flex-end; gap:9px;}
  .teach-cancel{font-size:13.5px; font-weight:500; padding:9px 16px; border-radius:10px; border:1px solid var(--border); background:#fff; color:var(--ink); cursor:pointer;}
  .teach-cancel:hover{background:var(--hover,#efefec);}
  .teach-save{font-size:13.5px; font-weight:600; padding:9px 18px; border-radius:10px; border:none; background:var(--clay); color:#fff; cursor:pointer; transition:opacity .14s;}
  .teach-save:disabled{opacity:.4; cursor:default;}
  .updrop{display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px; width:100%; padding:26px 14px; border:1.5px dashed var(--border); border-radius:12px; background:var(--cream,#faf9f5); cursor:pointer; transition:border-color .14s, background .14s;}
  .updrop:hover, .updrop.on{border-color:var(--clay); background:#fff;}
  /* sync progress card — bottom-right, above the activity robot */
  .synccard{position:fixed; right:18px; bottom:86px; z-index:50; width:288px; background:#fff; border:1px solid var(--border); border-radius:13px; box-shadow:0 8px 28px rgba(40,35,30,.16); padding:13px 15px; animation:syncin .22s ease-out;}
  .synccard.done{border-color:#bfe0c6;}
  @keyframes syncin{from{opacity:0; transform:translateY(8px);} to{opacity:1; transform:translateY(0);}}
  .sync-top{display:flex; align-items:center; gap:8px;}
  .sync-ic{font-size:14px; flex:none;}
  .sync-ic.ok{color:#3f8f5f; font-weight:700;}
  .sync-ic.spin{color:var(--clay); display:inline-block; animation:syncspin 1s linear infinite;}
  @keyframes syncspin{to{transform:rotate(360deg);}}
  .sync-ttl{font-size:13px; font-weight:600; color:var(--ink); min-width:0; flex:1;}
  .sync-cnt{font-size:12px; font-weight:700; color:var(--muted); font-variant-numeric:tabular-nums; flex:none;}
  .sync-track{height:6px; border-radius:99px; background:#efece6; margin-top:9px; overflow:hidden;}
  .sync-fill{display:block; height:100%; border-radius:99px; background:var(--clay); transition:width .35s ease;}
  .synccard.done .sync-fill{background:#3f8f5f;}
  /* in-modal upload progress + quiet folder link */
  .upprog{background:#faf9f7; border:1px solid var(--border); border-radius:11px; padding:13px 14px;}
  .upprog-top{display:flex; align-items:center; gap:8px; margin-bottom:9px;}
  .upprog-ttl{font-size:13px; font-weight:600; color:var(--ink);}
  .upprog-cnt{margin-left:auto; font-size:12px; font-weight:600; color:var(--muted);}
  .sync-fill.okfill{background:#3f8f5f;}
  .upbulk-lbl{font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#a89b8c; margin:16px 0 8px;}
  .upbulk{display:flex; align-items:center; gap:11px; width:100%; padding:10px 12px; margin-top:7px; text-align:left; background:#fff; border:1px solid var(--border); border-radius:11px; cursor:pointer; transition:border-color .12s, background .12s;}
  .upbulk:hover:not(:disabled){border-color:var(--clay); background:#faf6f3;}
  .upbulk:disabled{opacity:.6; cursor:default;}
  .upbulk-ic{display:flex; align-items:center; justify-content:center; width:32px; height:32px; flex:none; border-radius:8px; background:#f4f3f0; color:var(--clay);}
  .upbulk-tx{display:flex; flex-direction:column; min-width:0; flex:1;}
  .upbulk-tx b{font-size:13px; font-weight:600; color:var(--ink);}
  .upbulk-tx i{font-size:11.5px; font-style:normal; color:var(--muted);}
  .upbulk-go{flex:none; font-size:12.5px; font-weight:600; color:var(--clay);}
  .upscan-msg{font-size:12px; color:var(--muted); margin-top:9px; text-align:center;}
  /* ===== process flow: strip + stepper + expand ===== */
  .dlrow.proc{cursor:pointer; background:#fffdf8;}
  .dlrow.proc:hover{background:#fdf6e8;}
  .dlrow.exp{background:#fdf6e8;}
  .procstrip{background:#fffaf0; border:1px solid #f0e3c8; border-radius:12px; padding:11px 13px; margin-bottom:14px;}
  .procstrip-head{display:flex; align-items:center; gap:7px; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#a9742a; margin-bottom:9px;}
  .proc-pulse{width:8px; height:8px; border-radius:50%; background:#d3a13e; animation:procpulse 1.6s infinite;}
  @keyframes procpulse{0%{box-shadow:0 0 0 0 rgba(211,161,62,.5);}70%{box-shadow:0 0 0 7px rgba(211,161,62,0);}100%{box-shadow:0 0 0 0 rgba(211,161,62,0);}}
  .procrow{display:grid; grid-template-columns:minmax(120px,1.3fr) minmax(110px,1fr) 2fr 42px 24px 14px; align-items:center; gap:10px; padding:6px 6px; border-radius:8px; cursor:pointer;}
  .procrow:hover{background:#fdf3df;}
  .proc-chev{font-size:15px; color:var(--muted); transition:transform .14s; line-height:1;}
  .proc-batch-head{display:flex; align-items:center; gap:8px; margin-bottom:8px;}
  .proc-batch-ttl{font-size:12.5px; font-weight:700; color:#a9742a; text-transform:uppercase; letter-spacing:.04em;}
  .proc-batch-pct{margin-left:auto; font-size:13px; font-weight:700; color:#a9742a;}
  .proc-overall{height:7px; border-radius:99px; background:#efe6d2; overflow:hidden; margin-bottom:10px;}
  .proc-overall-fill{display:block; height:100%; background:linear-gradient(90deg,#e0b24e,#d3a13e); border-radius:99px; transition:width .5s ease;}
  .proc-lanes{display:flex; flex-wrap:wrap; gap:6px; margin-bottom:4px;}
  .lane{font-size:11px; font-weight:600; color:#8a7d68; background:#fff; border:1px solid #f0e3c8; border-radius:99px; padding:3px 9px;}
  .lane b{color:#a9742a; margin-left:3px;}
  .lane.q{background:#fbf4e6;}
  .lane.ok{color:#3f8f5f; border-color:#cfe6d3;} .lane.ok b{color:#3f8f5f;}
  .lane.fail{color:#cf6a4c; border-color:#e9cabf;} .lane.fail b{color:#cf6a4c;}
  .proc-active{margin-top:8px; border-top:1px solid #f0e3c8; padding-top:6px;}
  .flowpanel.inline{background:transparent; border:none; padding:8px 6px 4px;}
  .proc-foot{font-size:11.5px; color:#a89b8c; margin-top:8px; padding-top:7px; border-top:1px dashed #ead9b8;}
  .proc-name{font-size:12.5px; font-weight:600; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .proc-stage{font-size:12px; color:#a9742a; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .proc-track{height:6px; border-radius:99px; background:#efe6d2; overflow:hidden;}
  .proc-fill{display:block; height:100%; background:linear-gradient(90deg,#e0b24e,#d3a13e); border-radius:99px; transition:width .4s ease;}
  .proc-pct{font-size:11.5px; font-weight:600; color:var(--muted); text-align:right;}
  .proc-x{border:none; background:transparent; color:var(--muted); font-size:15px; cursor:pointer; line-height:1;}
  .proc-x:hover{color:#cf6a4c;}
  .stepper{display:flex; align-items:center; flex-wrap:wrap; gap:2px; margin-bottom:10px;}
  .stp{display:inline-flex; align-items:center; gap:4px; font-size:11px; color:var(--muted); white-space:nowrap;}
  .stp.done, .stp.done .stp-dot{color:#3f8f5f;}
  .stp.now{color:#a9742a; font-weight:700;}
  .stp.now .stp-dot{color:#d3a13e;}
  .stp.fail, .stp.fail .stp-dot{color:#cf6a4c;}
  .stp-sep{width:14px; height:1px; background:var(--border); margin:0 2px;}
  .flowpanel{padding:12px 14px 13px; background:#fffaf0; border-top:1px solid #f0e3c8;}
  .flow-bar{height:6px; border-radius:99px; background:#efe6d2; overflow:hidden; margin-bottom:10px;}
  .flow-fill{display:block; height:100%; background:linear-gradient(90deg,#e0b24e,#d3a13e); border-radius:99px; transition:width .4s ease;}
  .flow-fill.fail{background:#cf6a4c;}
  .flow-err{font-size:12px; color:#cf6a4c; margin-bottom:8px;}
  .flowlog{background:#2a2722; border-radius:8px; padding:8px 10px; margin-bottom:10px; font-family:ui-monospace,monospace; max-height:150px; overflow-y:auto;}
  .flowlog-row{display:flex; gap:8px; font-size:11px; line-height:1.7;}
  .flowlog-ts{color:#8f897d; flex:none;}
  .flowlog-msg{color:#e8e2d6;}
  .flow-act{display:flex; justify-content:flex-end;}
  .flow-btn{font-size:12px; font-weight:600; padding:6px 13px; border-radius:8px; border:1px solid var(--clay); background:var(--clay); color:#fff; cursor:pointer;}
  .flow-btn.ghost{background:#fff; color:#cf6a4c; border-color:#e3b3a4;}
  .flow-btn.ghost:hover{background:#fdf0ec;}
  /* ===== Jobs pill + right slide-over ===== */
  .jobspill{display:inline-flex; align-items:center; gap:5px; font-size:11.5px; font-weight:600; color:#a9742a; background:#fff8ec; border:1px solid #f0e3c8; border-radius:99px; padding:4px 11px; cursor:pointer;}
  .jobspill:hover{background:#fdf3df;}
  .jobspill b{color:#a9742a;}
  .jp-spin{display:inline-block; animation:jspin 1.1s linear infinite;}
  .jp-spin.still{animation:none;}
  @keyframes jspin{to{transform:rotate(360deg);}}
  .jobs-scrim{position:fixed; inset:0; z-index:70; background:rgba(30,28,25,.28); border:none; cursor:default; animation:jfade .15s ease-out;}
  @keyframes jfade{from{opacity:0;}to{opacity:1;}}
  .jobs-panel{position:fixed; top:0; right:0; z-index:71; width:420px; max-width:92vw; height:100vh; background:#fff; border-left:1px solid var(--border); box-shadow:-12px 0 36px rgba(40,35,30,.16); display:flex; flex-direction:column; animation:jslide .2s cubic-bezier(.2,.7,.3,1);}
  @keyframes jslide{from{transform:translateX(100%);}to{transform:none;}}
  .jobs-head{display:flex; align-items:center; justify-content:space-between; padding:15px 18px; border-bottom:1px solid var(--line);}
  .jobs-ttl{font-family:var(--serif,serif); font-size:19px; font-weight:500; color:var(--ink);}
  .jobs-x{border:none; background:transparent; font-size:16px; color:var(--muted); cursor:pointer;}
  .jobs-x:hover{color:var(--ink);}
  .jobs-sum{padding:14px 18px; border-bottom:1px solid var(--line); background:#fffaf0;}
  .jobs-sum-top{display:flex; align-items:center; gap:8px; margin-bottom:9px;}
  .jobs-sum-ttl{font-size:13px; font-weight:700; color:#a9742a;}
  .jobs-sum-pct{margin-left:auto; font-size:14px; font-weight:700; color:#a9742a;}
  .jobs-body{flex:1; overflow-y:auto; padding:6px 14px 22px;}
  .jobs-grp-h{display:flex; align-items:center; gap:8px; font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#a89b8c; margin:16px 4px 7px;}
  .jobs-grp-h.fail{color:#cf6a4c;}
  .jsearch{margin-left:auto; font-size:11.5px; padding:3px 8px; border:1px solid var(--border); border-radius:7px; background:#fff; width:120px;}
  .jrow{display:flex; align-items:center; gap:8px; padding:7px 6px; border-radius:8px;}
  .jrow[role="button"]{cursor:pointer;}
  .jrow[role="button"]:hover{background:#fdf3df;}
  .jrow.q:hover, .jrow.done:hover{background:#f6f5f2;}
  .jdot{width:7px; height:7px; border-radius:50%; flex:none; background:var(--muted);}
  .jdot.live{background:#d3a13e; animation:procpulse 1.6s infinite;}
  .jdot.ok{background:#3f8f5f;}
  .jdot.bad{background:#cf6a4c;}
  .jname{flex:1; min-width:0; font-size:12.5px; font-weight:600; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .jstage{font-size:11.5px; color:#a9742a; font-weight:600; flex:none;}
  .jpct{font-size:11.5px; font-weight:600; color:var(--muted); flex:none;}
  .jpos{font-size:11px; font-weight:700; color:#a89b8c; flex:none; width:26px;}
  .jbar{height:4px; border-radius:99px; background:#efe6d2; overflow:hidden; margin:0 6px 4px;}
  .jbar-fill{display:block; height:100%; background:linear-gradient(90deg,#e0b24e,#d3a13e); border-radius:99px; transition:width .4s ease;}
  .jretry{font-size:11.5px; font-weight:600; color:#cf6a4c; background:#fff; border:1px solid #e3b3a4; border-radius:7px; padding:3px 9px; cursor:pointer; flex:none;}
  .jretry:hover{background:#fdf0ec;}
  .jago{font-size:11px; color:var(--muted); flex:none;}
  .jmore{font-size:11.5px; color:var(--muted); padding:6px 8px;}
  .jobs-empty{text-align:center; color:var(--muted); font-size:13px; padding:40px 12px;}
  .sync-cur{font-size:11px; color:var(--muted); margin-top:7px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .sync-cur b{font-weight:600; color:#52504a;}
  @media (prefers-reduced-motion:reduce){ .sync-ic.spin{animation:none;} }
  .mcard{position:fixed; z-index:56; top:50%; left:50%; transform:translate(-50%,-50%); width:560px; max-width:94vw; max-height:88vh; overflow-y:auto; background:#fff; border-radius:18px; box-shadow:0 24px 70px rgba(40,30,20,.32); animation:mpop .18s ease;}
  .mcard.sm{width:460px;}
  @keyframes mpop{from{transform:translate(-50%,-46%); opacity:.5} to{transform:translate(-50%,-50%); opacity:1}}
  .mx{position:absolute; top:14px; right:14px; z-index:2; width:30px; height:30px; border-radius:8px; border:none; background:rgba(255,255,255,.72); cursor:pointer; font-size:14px; color:#46443f;}
  .mx:hover{background:#fff;}
  .mhead{padding:22px 24px 18px; border-bottom:1px solid var(--line);}
  .mchip{font-size:11.5px; font-weight:700;}
  .mpend{font-size:10.5px; font-weight:700; color:#a9742a; background:rgba(255,255,255,.7); padding:2px 8px; border-radius:6px;}
  .mkey{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; font-size:20px; font-weight:700; letter-spacing:.02em; margin-top:8px;}
  .agic{display:grid; place-items:center; width:38px; height:38px; border-radius:10px; background:rgba(255,255,255,.7); font-size:18px; flex-shrink:0;}
  .mbody{padding:20px 24px 24px;}
  .mstats{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:18px; padding-top:16px; border-top:1px solid var(--line);}
  .mstat{display:flex; flex-direction:column; gap:3px; min-width:0;}
  .mstat-k{font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#a89b8c;}
  .mstat-v{font-size:13px; font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums;}
  .mnote{margin-top:16px; font-size:12.5px; line-height:1.55; color:var(--muted); background:#f7f7f5; border-radius:10px; padding:11px 13px;}
  .mbtn{font-size:13px; font-weight:600; padding:9px 18px; border-radius:9px; border:none; color:#fff; cursor:pointer;}
  .mbtn-ghost{font-size:13px; padding:9px 16px; border-radius:9px; border:1px solid var(--border); background:#fff; color:var(--muted); cursor:pointer;}
  .mbtn-ghost:hover{background:#efefec;}
  /* B1: rich fact modal new sections */
  .msec{margin-top:16px; padding-top:14px; border-top:1px solid var(--line);}
  .msec-h{font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#a89b8c; margin-bottom:7px;}
  .mcite{font-size:12.5px; line-height:1.55; color:var(--ink); padding:3px 0;}
  .mrel-chip{display:inline-flex; align-items:center; gap:5px; font-size:12px; padding:4px 10px; border-radius:7px; background:#f3f3f1; border:1px solid #dcdcd8; color:var(--clay); cursor:pointer; transition:background .12s;}
  .mrel-chip:hover{background:#e3edff;}
  /* A3: doc browser wrapper */
  .dbrowser{padding-bottom:24px;}
  .catbar{display:flex; flex-wrap:wrap; gap:7px; margin-bottom:11px;}
  .catbar .chip b{font-weight:700; opacity:.7; margin-left:1px;}
  .catmore{position:relative;}
  .catmore-scrim{position:fixed; inset:0; z-index:20; background:transparent; border:none; cursor:default;}
  .catmore-menu{position:absolute; top:calc(100% + 5px); left:0; z-index:21; min-width:200px; max-height:300px; overflow-y:auto; background:#fff; border:1px solid var(--border); border-radius:11px; box-shadow:0 8px 26px rgba(40,35,30,.15); padding:6px;}
  .catmore-item{display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; padding:7px 11px; border-radius:8px; border:none; background:transparent; cursor:pointer; font-size:13px; color:var(--ink); text-align:left;}
  .catmore-item:hover{background:var(--hover,#efefec);}
  .catmore-item.on{background:var(--navpill); font-weight:600;}
  .catmore-item b{font-weight:700; color:var(--muted); font-size:11.5px;}
  .dbar{display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding:9px 12px; background:#fff; border:1px solid var(--border); border-radius:11px; margin-bottom:16px; box-shadow:0 3px 16px rgba(0,0,0,.03);}
  .dbar-lbl{font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; color:#a89b8c; margin-right:2px;}
  .pill{font-size:12px; padding:4px 11px; border-radius:999px; color:var(--muted); background:#efefec; border:1px solid transparent; cursor:pointer; transition:all .12s; white-space:nowrap;}
  .pill:hover{color:var(--ink);}
  .pill.on{background:#f3f3f1; color:var(--clay); border-color:#dcdcd8; font-weight:600;}
</style>
