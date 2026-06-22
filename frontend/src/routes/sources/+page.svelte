<script lang="ts">
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import { parseDocName } from '$lib/docname';
  import { onDestroy } from 'svelte';

  // ── reactive admin/me gate (cachedUser is non-reactive → revalidate via me()) ──
  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  type Folder = {
    id: number; name: string; sector_id?: number | null; sector_label?: string | null; doc_count: number;
    access_mode?: 'sector' | 'specific' | 'org'; access_n?: number;
  };
  type Principal = { type: 'user' | 'group'; id: number; label: string };
  type Doc = {
    id: number; name: string; page_count?: number; status?: string;
    uploaded_by?: string | null; created_at?: string; updated_at?: string | null; folder_id?: number | null;
    progress?: number; pages_done?: number;
    lang?: string | null; category?: string | null; doc_type?: string | null;
    sections?: number; vision_pages?: number; text_pages?: number;
    cover_page_id?: number | null; used_count?: number; last_used_at?: string | null;
    has_playbook?: number;
  };

  // ── state (Svelte5 $state: arrays mutate by INDEX, never raw ref) ──
  let folders = $state<Folder[]>([]);
  let unfiled = $state(0);
  let docs = $state<Doc[]>([]);
  let total = $state(0);
  let selected = $state<number | 'all' | 'unfiled'>('all');   // selected folder id, or virtual rows
  let loadingDocs = $state(false);

  // ── center list: category filter / group / view / sort ──
  let catFilter = $state<string>('all');           // 'all' or a category label
  let groupBy = $state<'date' | 'category'>('date');
  let viewMode = $state<'list' | 'cards'>('list');
  let sortBy = $state<'newest' | 'oldest' | 'used'>('newest');
  let retagging = $state(false);

  // ── new-folder MODAL ──
  let showFolderModal = $state(false);
  let newName = $state('');
  let creating = $state(false);
  let accessMode = $state<'sector' | 'specific' | 'org'>('sector');
  let picked = $state<Principal[]>([]);            // chosen users/groups for 'specific'
  let allPrincipals = $state<Principal[]>([]);     // from api.principals()
  let principalsLoaded = $state(false);
  let pickerOpen = $state(false);                  // the "add person/group" dropdown
  let pickerSearch = $state('');
  let nameInput: HTMLInputElement | null = null;

  // not-yet-picked principals, filtered by the search box
  let pickerOptions = $derived.by(() => {
    const chosen = new Set(picked.map((p) => `${p.type}:${p.id}`));
    const q = pickerSearch.trim().toLowerCase();
    return allPrincipals.filter(
      (p) => !chosen.has(`${p.type}:${p.id}`) && (!q || p.label.toLowerCase().includes(q))
    );
  });

  // Create button enabled only when valid
  let canCreate = $derived(
    !!newName.trim() && !creating && (accessMode !== 'specific' || picked.length > 0)
  );

  async function ensurePrincipals() {
    if (principalsLoaded) return;
    try {
      const r = await api.principals();
      const users: Principal[] = (r.users || []).map((u: any) => ({ type: 'user' as const, id: u.id, label: u.email }));
      const groups: Principal[] = (r.groups || []).map((g: any) => ({ type: 'group' as const, id: g.id, label: `${g.name} (group)` }));
      allPrincipals = [...users, ...groups];
    } catch {} finally { principalsLoaded = true; }
  }

  function openFolderModal() {
    newName = ''; accessMode = 'sector'; picked = [];
    pickerOpen = false; pickerSearch = '';
    showFolderModal = true;
    ensurePrincipals();
    setTimeout(() => nameInput?.focus(), 0);
  }
  function closeFolderModal() {
    if (creating) return;
    showFolderModal = false;
    pickerOpen = false;
  }
  function addPrincipal(p: Principal) {
    picked = [...picked, p];           // reassign — Svelte5 $state
    pickerSearch = '';
    pickerOpen = false;
  }
  function removePrincipal(p: Principal) {
    picked = picked.filter((x) => !(x.type === p.type && x.id === p.id));
  }

  // ── SHARE / manage-access MODAL ──
  let showShareModal = $state(false);
  let shareFolder = $state<Folder | null>(null);
  let shareLoading = $state(false);
  let saving = $state(false);
  let sAccessMode = $state<'sector' | 'specific' | 'org'>('sector');
  let sPicked = $state<Principal[]>([]);
  let sPickerOpen = $state(false);
  let sPickerSearch = $state('');

  let sPickerOptions = $derived.by(() => {
    const chosen = new Set(sPicked.map((p) => `${p.type}:${p.id}`));
    const q = sPickerSearch.trim().toLowerCase();
    return allPrincipals.filter(
      (p) => !chosen.has(`${p.type}:${p.id}`) && (!q || p.label.toLowerCase().includes(q))
    );
  });

  let canSave = $derived(
    !!shareFolder && !saving && !shareLoading &&
    (sAccessMode !== 'specific' || sPicked.length > 0)
  );

  async function openShareModal(f: Folder, e?: Event) {
    e?.stopPropagation();          // don't also select the folder
    shareFolder = f;
    sAccessMode = 'sector'; sPicked = []; sPickerOpen = false; sPickerSearch = '';
    showShareModal = true;
    ensurePrincipals();
    shareLoading = true;
    try {
      const r = await api.folderAccess(f.id);
      sAccessMode = r.access_mode ?? 'sector';
      sPicked = (r.principals || []).map((p: any) => ({
        type: p.type, id: p.id,
        label: p.label ?? (p.type === 'group' ? `${p.id}` : `${p.id}`)
      }));
    } catch {} finally { shareLoading = false; }
  }
  function closeShareModal() {
    if (saving) return;
    showShareModal = false;
    sPickerOpen = false;
    shareFolder = null;
  }
  function sAddPrincipal(p: Principal) {
    sPicked = [...sPicked, p];
    sPickerSearch = '';
    sPickerOpen = false;
  }
  function sRemovePrincipal(p: Principal) {
    sPicked = sPicked.filter((x) => !(x.type === p.type && x.id === p.id));
  }
  async function saveShare() {
    if (!canSave || !shareFolder) return;
    saving = true;
    try {
      const principals =
        sAccessMode === 'specific' ? sPicked.map((p) => ({ type: p.type, id: p.id })) : [];
      await api.setFolderAccess(shareFolder.id, { access_mode: sAccessMode, principals });
      showShareModal = false;
      shareFolder = null;
      await loadFolders();
    } catch {} finally { saving = false; }
  }

  // upload
  let uploading = $state(false);
  let fileInput: HTMLInputElement | null = null;

  // ── derived: which folder_id query to send for the selected source ──
  let selFolderId = $derived(typeof selected === 'number' ? selected : null);
  let breadcrumb = $derived.by(() => {
    if (selected === 'all') return 'All documents';
    if (selected === 'unfiled') return 'Unfiled';
    return folders.find((f) => f.id === selected)?.name ?? 'Folder';
  });

  async function loadFolders() {
    try {
      const r = await api.folders();
      folders = r.folders || [];
      unfiled = r.unfiled ?? 0;
    } catch {}
  }

  async function loadDocs() {
    loadingDocs = true;
    try {
      const r = await api.documents(selFolderId);
      let list: Doc[] = r.documents || r.docs || [];
      docs = list;
      total = r.total ?? list.length;
    } catch {
      docs = []; total = 0;
    } finally {
      loadingDocs = false;
    }
  }

  // reload the doc table whenever the selected source changes
  $effect(() => { selected; loadDocs(); });

  // ── poll the LIST while any doc is queued/processing ──
  let inFlight = $derived(docs.some((d) => d.status === 'queued' || d.status === 'processing'));
  let pollTimer = $state<ReturnType<typeof setInterval> | null>(null);
  $effect(() => {
    if (inFlight && !pollTimer) {
      pollTimer = setInterval(() => loadDocs(), 3000);
    } else if (!inFlight && pollTimer) {
      clearInterval(pollTimer); pollTimer = null;
    }
    return () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };
  });
  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });

  // initial load
  $effect(() => { if (me && isAdmin) { loadFolders(); } });

  // ── jobs (queued/processing) count ──
  let jobsCount = $derived(docs.filter((d) => {
    const s = (d.status || 'ready').toLowerCase();
    return s === 'queued' || s === 'processing';
  }).length);

  // ── category chips (client-derived from docs[].category) ──
  function catOf(d: Doc): string { return (d.category && d.category.trim()) || 'Uncategorized'; }
  let categories = $derived.by(() => {
    const m = new Map<string, number>();
    for (const d of docs) { const c = catOf(d); m.set(c, (m.get(c) ?? 0) + 1); }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  });

  // ── filtered + sorted docs ──
  let visibleDocs = $derived.by(() => {
    let list = catFilter === 'all' ? docs : docs.filter((d) => catOf(d) === catFilter);
    const t = (d: Doc) => (d.created_at ? new Date(d.created_at).getTime() : 0);
    list = [...list].sort((a, b) => {
      if (sortBy === 'used') return (b.used_count ?? 0) - (a.used_count ?? 0);
      if (sortBy === 'oldest') return t(a) - t(b);
      return t(b) - t(a); // newest
    });
    return list;
  });

  // ── group the visible docs by date-bucket or category ──
  function dateBucket(d: Doc): string {
    if (!d.created_at) return 'Older';
    const dt = new Date(d.created_at); const now = new Date();
    const sameDay = dt.toDateString() === now.toDateString();
    if (sameDay) return 'Today';
    if (dt.getFullYear() === now.getFullYear() && dt.getMonth() === now.getMonth()) return 'Earlier this month';
    return 'Older';
  }
  let groups = $derived.by(() => {
    const order = groupBy === 'date'
      ? ['Today', 'Earlier this month', 'Older']
      : null;
    const m = new Map<string, Doc[]>();
    for (const d of visibleDocs) {
      const key = groupBy === 'date' ? dateBucket(d) : catOf(d);
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(d);
    }
    let keys = [...m.keys()];
    if (order) keys = order.filter((k) => m.has(k));
    else keys.sort();
    return keys.map((k) => ({ label: k, docs: m.get(k)! }));
  });

  async function retagAll() {
    if (retagging) return;
    retagging = true;
    try { await api.categorizeAll(false); await loadDocs(); } catch {} finally { retagging = false; }
  }

  // ── row display helpers ──
  function langOf(d: Doc): string { return (d.lang || '').toLowerCase().startsWith('my') ? 'MY' : 'EN'; }
  function accuracyOf(d: Doc): number | null {
    const pc = d.page_count ?? 0; if (!pc) return null;
    return Math.round(((d.vision_pages ?? 0) / pc) * 100);
  }
  function subLine(d: Doc): string {
    const parts: string[] = [];
    const c = catOf(d); if (c && c !== 'Uncategorized') parts.push(c);
    return parts.join(' · ');
  }
  function relDays(iso?: string | null): string {
    if (!iso) return '—';
    const ms = Date.now() - new Date(iso).getTime();
    const d = Math.floor(ms / 86400000);
    if (d <= 0) return 'today';
    if (d === 1) return '1d ago';
    return `${d}d ago`;
  }
  function shortDate(iso?: string | null): string {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); } catch { return '—'; }
  }

  async function makeFolder() {
    const n = newName.trim();
    if (!canCreate) return;
    creating = true;
    try {
      const principals =
        accessMode === 'specific' ? picked.map((p) => ({ type: p.type, id: p.id })) : [];
      await api.createFolder(n, { access_mode: accessMode, principals });
      showFolderModal = false;
      await loadFolders();
    } catch {} finally { creating = false; }
  }

  function pickFiles() { fileInput?.click(); }
  async function onFilesPicked(e: Event) {
    const input = e.target as HTMLInputElement;
    const files = input.files;
    if (!files || !files.length) return;
    uploading = true;
    try {
      const fid = typeof selected === 'number' ? selected : null;
      for (const f of Array.from(files)) {
        try { await api.uploadTo(f, fid); } catch {}
      }
      await Promise.all([loadFolders(), loadDocs()]);
    } finally {
      uploading = false;
      input.value = '';
    }
  }

  function title(d: Doc) {
    let raw = d.name || '';
    try { if (/%[0-9A-Fa-f]{2}/.test(raw)) raw = decodeURIComponent(raw); } catch {}
    raw = raw.replace(/\.(pdf|png|jpe?g)$/i, '');
    return parseDocName(raw).title || raw;
  }
  function statusOf(d: Doc) { return (d.status || 'ready').toLowerCase(); }
  function pillClass(s: string): string {
    if (s === 'ready') return 'p-ready';
    if (s === 'failed' || s === 'cancelled') return 'p-fail';
    if (s === 'processing') return 'p-proc';
    return 'p-queued';
  }
  function pillLabel(s: string, d: Doc): string {
    const cap = s.charAt(0).toUpperCase() + s.slice(1);
    if (s === 'processing' && typeof d.progress === 'number') return `Processing ${d.progress}%`;
    return cap;
  }

  // ══════════════════════════════════════════════════════════════════
  //  RIGHT PROCESS / DETAIL PANEL
  // ══════════════════════════════════════════════════════════════════
  type Proc = {
    doc: any;
    stage: number;
    stages: string[];
    pages: { page_no: number; read: boolean }[];
    enrichers: Record<string, number>;
    log: { id: number; step: string; msg: string; level?: string; ts?: string }[];
  };

  let panelOpen = $state(false);
  let panelDocId = $state<number | null>(null);
  let proc = $state<Proc | null>(null);
  let procTimer = $state<ReturnType<typeof setInterval> | null>(null);
  let elapsed = $state(0);           // seconds since panel opened (display only)
  let elapsedTimer = $state<ReturnType<typeof setInterval> | null>(null);
  let actionBusy = $state(false);

  // move-to-folder dropdown
  let moveOpen = $state(false);

  let procStatus = $derived((proc?.doc?.status || '').toLowerCase());
  let procLive = $derived(procStatus === 'queued' || procStatus === 'processing');

  function fmtElapsed(s: number) {
    const m = Math.floor(s / 60);
    const ss = (s % 60).toString().padStart(2, '0');
    return `${m}:${ss}`;
  }

  async function openPanel(d: Doc) {
    panelDocId = d.id;
    panelOpen = true;
    moveOpen = false;
    elapsed = 0;
    proc = null;
    readerTab = null;
    readerData = null;
    detail = null;
    detailLoading = false;
    pageIds = [];
    coverIdx = 0;
    stopElapsed();
    elapsedTimer = setInterval(() => (elapsed += 1), 1000);
    await loadProc();
  }

  function stopProcPoll() { if (procTimer) { clearInterval(procTimer); procTimer = null; } }
  function stopElapsed() { if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; } }

  function closePanel() {
    panelOpen = false;
    panelDocId = null;
    proc = null;
    moveOpen = false;
    readerTab = null;
    readerData = null;
    detail = null;
    detailLoading = false;
    pageIds = [];
    coverIdx = 0;
    stopProcPoll();
    stopElapsed();
  }

  async function loadProc() {
    if (panelDocId == null) return;
    try {
      const r = await api.docProcessing(panelDocId);
      proc = r as Proc;
    } catch {
      proc = null;
    }
    // manage the 2s poll based on live status
    const live = proc && ['queued', 'processing'].includes((proc.doc?.status || '').toLowerCase());
    if (live && !procTimer) {
      procTimer = setInterval(loadProc, 2000);
    } else if (!live) {
      stopProcPoll();
      if (!live) stopElapsed();
    }
  }

  onDestroy(() => { stopProcPoll(); stopElapsed(); });

  // ── stage timeline helpers ──
  function stageState(i: number): 'done' | 'cur' | 'pending' {
    if (!proc) return 'pending';
    if (i < proc.stage) return 'done';
    if (i === proc.stage) return procLive ? 'cur' : 'done';
    return 'pending';
  }
  function stageSub(name: string): string {
    if (!proc) return '';
    const d = proc.doc || {};
    const n = name.toLowerCase();
    if (n === 'queued') return 'picked up';
    if (n === 'render') return d.page_count ? `${d.page_count} pages` : 'rasterising';
    if (n === 'read') return d.page_count ? `reading page ${d.pages_done ?? 0} / ${d.page_count}` : 'vision';
    if (n === 'structure') return 'PageIndex outline / tree';
    if (n === 'compile') return 'per-page wiki markdown';
    if (n === 'enrich') return 'knowledge passes';
    if (n === 'ready') return 'answerable + cited';
    return '';
  }

  // first not-read page while processing → current chip
  let curPageIdx = $derived.by(() => {
    if (!proc || !procLive) return -1;
    return proc.pages.findIndex((p) => !p.read);
  });

  // ── enricher checklist ──
  const ENRICHERS: { key: string; label: string }[] = [
    { key: 'compiled', label: 'Compile wiki' },
    { key: 'playbook', label: 'Playbook (steps / verify)' },
    { key: 'entities', label: 'Entities' },
    { key: 'lookup', label: 'Lookup' },
    { key: 'dependencies', label: 'Dependencies' },
    { key: 'tree', label: 'Troubleshoot tree' },
    { key: 'qa', label: 'Q&A mining' },
  ];
  // heuristic running enricher: first zero-count after compiled>0, while processing
  let runningEnricher = $derived.by(() => {
    if (!proc || !procLive) return '';
    const e = proc.enrichers || {};
    if (!(e.compiled > 0)) return 'compiled';
    for (const { key } of ENRICHERS) {
      if (key === 'compiled') continue;
      if (!(e[key] > 0)) return key;
    }
    return '';
  });
  function enrState(key: string): 'ok' | 'run' | 'wait' {
    const c = proc?.enrichers?.[key] ?? 0;
    if (c > 0) return 'ok';
    if (key === runningEnricher) return 'run';
    return 'wait';
  }

  function logClass(step: string): string {
    const s = (step || '').toLowerCase();
    if (s.includes('render') || s.includes('raster')) return 'c-render';
    if (s.includes('vision') || s.includes('read')) return 'c-vision';
    if (s.includes('compile')) return 'c-compile';
    if (s.includes('enrich') || s.includes('playbook') || s.includes('entit') || s.includes('tree')) return 'c-enrich';
    return '';
  }

  // ── footer actions ──
  async function afterAction() {
    await Promise.all([loadFolders(), loadDocs()]);
    if (panelDocId != null) await loadProc();
  }
  async function doCancel() {
    if (panelDocId == null) return;
    actionBusy = true;
    try { await api.cancelDoc(panelDocId); await afterAction(); } catch {} finally { actionBusy = false; }
  }
  async function doRetry() {
    if (panelDocId == null) return;
    actionBusy = true;
    try {
      await api.retryDoc(panelDocId);
      elapsed = 0;
      stopElapsed();
      elapsedTimer = setInterval(() => (elapsed += 1), 1000);
      await afterAction();
    } catch {} finally { actionBusy = false; }
  }
  async function doDelete() {
    if (panelDocId == null) return;
    if (!confirm('Delete this document? This cannot be undone.')) return;
    actionBusy = true;
    try {
      await api.deleteDoc(panelDocId);
      closePanel();
      await Promise.all([loadFolders(), loadDocs()]);
    } catch {} finally { actionBusy = false; }
  }
  async function doMove(folderId: number | null) {
    if (panelDocId == null) return;
    moveOpen = false;
    actionBusy = true;
    try { await api.moveDoc(panelDocId, folderId); await afterAction(); } catch {} finally { actionBusy = false; }
  }

  // ══════════════════════════════════════════════════════════════════
  //  READER TABS (status === ready)
  // ══════════════════════════════════════════════════════════════════
  type ReaderTab = 'pages' | 'text' | 'playbook' | 'entities' | 'lookup' | 'dependencies' | 'tree';
  const READER_TABS: { key: ReaderTab; label: string }[] = [
    { key: 'pages', label: 'Pages' },
    { key: 'text', label: 'Text' },
    { key: 'playbook', label: 'Playbook' },
    { key: 'entities', label: 'Entities' },
    { key: 'lookup', label: 'Lookup' },
    { key: 'dependencies', label: 'Dependencies' },
    { key: 'tree', label: 'Troubleshoot' },
  ];
  let readerTab = $state<ReaderTab | null>(null);
  let readerData = $state<any>(null);
  let readerLoading = $state(false);

  async function openReaderTab(tab: ReaderTab) {
    if (panelDocId == null) return;
    readerTab = tab;
    readerData = null;
    readerLoading = true;
    const id = panelDocId;
    try {
      let d: any = null;
      if (tab === 'pages') d = await api.docDetail(id);
      else if (tab === 'text') d = await api.docText(id);
      else if (tab === 'playbook') d = await api.getPlaybook(id);
      else if (tab === 'entities') d = await api.docDetail(id);   // entities ride doc detail
      else if (tab === 'lookup') d = await api.getLookup(id);
      else if (tab === 'dependencies') d = await api.getDependencies(id);
      else if (tab === 'tree') d = await api.getTree(id);
      readerData = d;
    } catch {
      readerData = null;
    } finally {
      readerLoading = false;
    }
  }

  // when a doc becomes ready while the panel is open, default to the Pages tab
  $effect(() => {
    if (panelOpen && procStatus === 'ready' && readerTab === null) {
      openReaderTab('pages');
    }
  });

  // ══════════════════════════════════════════════════════════════════
  //  RICH INSPECTOR (status === ready) — docDetail + page preview
  // ══════════════════════════════════════════════════════════════════
  let detail = $state<any>(null);          // { doc, stats, outline }
  let detailLoading = $state(false);
  let pageIds = $state<{ page_id: number; page_no: number }[]>([]);
  let coverIdx = $state(0);                 // index into pageIds for the preview

  let curPageId = $derived.by(() => {
    if (pageIds.length) return pageIds[Math.min(coverIdx, pageIds.length - 1)]?.page_id ?? null;
    return proc?.doc?.cover_page_id ?? null;
  });
  let curPageNo = $derived.by(() => {
    if (pageIds.length) return pageIds[Math.min(coverIdx, pageIds.length - 1)]?.page_no ?? 1;
    return coverIdx + 1;
  });
  let totalPages = $derived(detail?.doc?.page_count ?? proc?.doc?.page_count ?? pageIds.length ?? 0);

  async function loadDetail() {
    if (panelDocId == null) return;
    detail = null; pageIds = []; coverIdx = 0;
    detailLoading = true;
    const id = panelDocId;
    try {
      const [d, pg] = await Promise.allSettled([api.docDetail(id), api.docPages(id)]);
      if (id !== panelDocId) return;           // panel changed mid-flight
      if (d.status === 'fulfilled') detail = d.value;
      if (pg.status === 'fulfilled') pageIds = (pg.value?.pages || []) as any[];
    } catch {} finally {
      if (id === panelDocId) detailLoading = false;
    }
  }
  function prevPage() { if (coverIdx > 0) coverIdx -= 1; }
  function nextPage() {
    const max = (pageIds.length || totalPages) - 1;
    if (coverIdx < max) coverIdx += 1;
  }

  // load the rich inspector once a doc is ready + panel open
  $effect(() => {
    if (panelOpen && procStatus === 'ready' && panelDocId != null && detail === null && !detailLoading) {
      loadDetail();
    }
  });

  function openFullDoc() {
    if (panelDocId != null) window.location.href = `/brain?doc=${panelDocId}`;
  }

  function uses(d: any): number { return d?.stats?.used_count ?? d?.usage?.used_count ?? d?.doc?.used_count ?? 0; }

  function onScrim(e: KeyboardEvent) { if (e.key === 'Escape') closePanel(); }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && panelOpen) closePanel(); }} />

{#if !me}
  <div class="src-shell"><div class="empty">Loading…</div></div>
{:else if !isAdmin}
  <div class="src-shell"><div class="empty">Admin only.</div></div>
{:else}
<div class="src-shell">
  <!-- ===== LEFT RAIL — folders (persistent) ===== -->
  <aside class="rail">
    <div class="grp">Folders</div>
    <button class="fitem" class:on={selected === 'all'} onclick={() => (selected = 'all')}>
      <span class="ic">▦</span> All documents <span class="n">{total}</span>
    </button>
    <button class="fitem" class:on={selected === 'unfiled'} onclick={() => (selected = 'unfiled')}>
      <span class="ic">○</span> Unfiled <span class="n">{unfiled}</span>
    </button>

    <div class="grp">Shared folders</div>
    {#if folders.length === 0}
      <div class="rail-empty">No folders yet.</div>
    {:else}
      {#each folders as f (f.id)}
        <button class="fitem" class:on={selected === f.id} onclick={() => (selected = f.id)}>
          <span class="ic">📁</span>
          <span class="fname">{f.name}</span>
          <span
            class="sh"
            role="button"
            tabindex="0"
            title="Share / manage access"
            aria-label="Share folder {f.name}"
            onclick={(e) => openShareModal(f, e)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openShareModal(f); } }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle>
              <line x1="8.6" y1="13.5" x2="15.4" y2="17.5"></line><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"></line>
            </svg>
          </span>
          {#if f.access_mode === 'org'}
            <span class="lk" title="Whole organisation">🌐</span>
          {:else if f.access_mode === 'specific'}
            <span class="lk" title="Specific people / groups">🔒{f.access_n ?? 0}</span>
          {/if}
          <span class="n">{f.doc_count}</span>
        </button>
      {/each}
    {/if}

    <div class="newf" role="button" tabindex="0" onclick={openFolderModal}
      onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFolderModal(); } }}>
      ＋ New folder
    </div>
    <div class="spacer"></div>
    <div class="railfoot">{total} {total === 1 ? 'doc' : 'docs'}{inFlight ? ' · processing…' : ''}</div>
  </aside>

  <!-- ===== CENTER — doc list ===== -->
  <main class="center">
    <div class="chead">
      <h1>{breadcrumb}</h1>
      <small>{total} {total === 1 ? 'document' : 'documents'}</small>
      <button class="up" onclick={pickFiles} disabled={uploading}>
        {uploading ? 'Uploading…' : '⬆ Upload here'}
      </button>
      <input bind:this={fileInput} type="file" multiple class="hidden-file"
        accept=".pdf,.png,.jpg,.jpeg" onchange={onFilesPicked} />
    </div>

    {#if loadingDocs && docs.length === 0}
      <div class="empty">Loading documents…</div>
    {:else if docs.length === 0}
      <div class="empty">No documents in this folder — upload one.</div>
    {:else}
      <!-- ===== category chips ===== -->
      <div class="chips-row">
        <button class="chip" class:on={catFilter === 'all'} onclick={() => (catFilter = 'all')}>
          All <span class="c">{docs.length}</span>
        </button>
        {#each categories as cat (cat.name)}
          <button class="chip" class:on={catFilter === cat.name} onclick={() => (catFilter = cat.name)}>
            {cat.name} <span class="c">{cat.count}</span>
          </button>
        {/each}
        <button class="retag" onclick={retagAll} disabled={retagging}>
          {retagging ? '↻ Re-tagging…' : '↻ Re-tag'}
        </button>
      </div>

      <!-- ===== toolbar ===== -->
      <div class="tb">
        <span class="tb-lab">Group</span>
        <span class="seg">
          <button class:on={groupBy === 'date'} onclick={() => (groupBy = 'date')}>Date</button>
          <button class:on={groupBy === 'category'} onclick={() => (groupBy = 'category')}>Category</button>
        </span>
        <span class="tb-lab">View</span>
        <span class="seg">
          <button class:on={viewMode === 'cards'} onclick={() => (viewMode = 'cards')}>Cards</button>
          <button class:on={viewMode === 'list'} onclick={() => (viewMode = 'list')}>List</button>
        </span>
        <span class="jobs">
          {#if jobsCount > 0}<span class="spin"></span>{/if}
          Jobs · {jobsCount} active
        </span>
        <span class="tb-sort">
          Sort:
          <select bind:value={sortBy}>
            <option value="newest">Newest</option>
            <option value="oldest">Oldest</option>
            <option value="used">Most-used</option>
          </select>
        </span>
      </div>

      {#if visibleDocs.length === 0}
        <div class="empty">No documents in this category.</div>
      {:else if viewMode === 'list'}
        <!-- ===== LIST view ===== -->
        <div class="list">
          {#each groups as g (g.label)}
            <div class="glbl">{g.label} · {g.docs.length}</div>
            <div class="lh">
              <div>Title</div><div>Lang</div><div>Pages</div><div>Used</div><div>Accuracy</div><div>By</div>
            </div>
            {#each g.docs as d (d.id)}
              {@const st = statusOf(d)}
              {@const acc = accuracyOf(d)}
              <div class="lr" class:sel={panelDocId === d.id} role="button" tabindex="0"
                onclick={() => openPanel(d)}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPanel(d); } }}>
                <div class="ti">
                  <div class="ic" class:my={langOf(d) === 'MY'}></div>
                  <div class="tt">
                    <div class="nm">{title(d)}</div>
                    {#if st === 'processing' || st === 'queued'}
                      <div class="s">
                        <span class="dot b"></span>
                        {#if d.page_count}Processing · reading {d.pages_done ?? 0}/{d.page_count}{:else}Queued…{/if}
                      </div>
                      <div class="pb"><i style="width:{d.progress ?? 0}%"></i></div>
                    {:else}
                      <div class="s">
                        <span class="badge">{(d.doc_type || 'SOP').toUpperCase()}</span>
                        {#if subLine(d)}{subLine(d)} · {/if}
                        {#if st === 'failed' || st === 'cancelled'}
                          <span class="dot f"></span> {st.charAt(0).toUpperCase() + st.slice(1)}
                        {:else}
                          <span class="dot"></span> Ready
                        {/if}
                      </div>
                    {/if}
                  </div>
                </div>
                <span class="lang {langOf(d) === 'MY' ? 'my' : 'en'}">{langOf(d)}</span>
                <div class="muted">{d.page_count ?? '—'}</div>
                <div>{#if (d.used_count ?? 0) > 0}<span class="used">{d.used_count}×</span>{:else}<span class="muted">—</span>{/if}</div>
                <div>{#if acc !== null}<span class="acc">{acc}%</span>{:else}<span class="muted">—</span>{/if}</div>
                <div class="muted">{d.uploaded_by ?? '—'}</div>
              </div>
            {/each}
          {/each}
        </div>
      {:else}
        <!-- ===== CARDS view ===== -->
        {#each groups as g (g.label)}
          <div class="glbl pad">{g.label} · {g.docs.length}</div>
          <div class="cards">
            {#each g.docs as d (d.id)}
              {@const st = statusOf(d)}
              {@const acc = accuracyOf(d)}
              <div class="card" class:sel={panelDocId === d.id} role="button" tabindex="0"
                onclick={() => openPanel(d)}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPanel(d); } }}>
                <div class="card-cover">
                  {#if d.cover_page_id}
                    <img src="/api/pages/{d.cover_page_id}" alt="" loading="lazy" />
                  {:else}
                    <div class="card-noimg">{(d.doc_type || 'SOP').toUpperCase()}</div>
                  {/if}
                  <span class="lang {langOf(d) === 'MY' ? 'my' : 'en'}">{langOf(d)}</span>
                </div>
                <div class="card-body">
                  <div class="card-ttl">{title(d)}</div>
                  <div class="card-sub">
                    <span class="badge">{(d.doc_type || 'SOP').toUpperCase()}</span>
                    {#if st === 'processing' || st === 'queued'}
                      <span class="dot b"></span> Processing
                    {:else if st === 'failed' || st === 'cancelled'}
                      <span class="dot f"></span> {st}
                    {:else}
                      <span class="dot"></span> Ready
                    {/if}
                  </div>
                  <div class="card-stats">
                    <span>{d.page_count ?? '—'}p</span>
                    <span class="used">{(d.used_count ?? 0) > 0 ? `${d.used_count}×` : '—'}</span>
                    {#if acc !== null}<span class="acc">{acc}%</span>{/if}
                  </div>
                </div>
              </div>
            {/each}
          </div>
        {/each}
      {/if}
    {/if}
  </main>
</div>

<!-- ===== RIGHT PROCESS / DETAIL PANEL ===== -->
<div class="proc-scrim" class:open={panelOpen} role="button" tabindex="-1" aria-label="Close panel"
  onclick={closePanel} onkeydown={onScrim}
  style="position:fixed;inset:0;z-index:60;border:none;background:rgba(20,18,16,.16);display:{panelOpen ? 'block' : 'none'}"></div>
<!-- critical layout forced inline so it can't be lost to CSS-scope/cascade issues (was rendering full-width coral) -->
<aside class="proc" class:open={panelOpen} aria-hidden={!panelOpen}
  style="position:fixed;top:0;right:0;width:520px;max-width:95vw;height:100vh;background:#fff;border-left:1px solid var(--line);z-index:61;display:flex;flex-direction:column;transition:transform .22s ease;transform:translateX({panelOpen ? '0' : '100%'})">
  {#if panelOpen}
    <div class="ph">
      <button class="px" onclick={closePanel} aria-label="Close">✕</button>
      {#if procLive}
        <div class="live"><span class="spin sm"></span> live · {fmtElapsed(elapsed)}</div>
      {/if}
      <h2>{proc ? title(proc.doc) : 'Loading…'}</h2>
      {#if proc}
        <div class="meta">
          {(proc.doc.status || 'ready')} · {proc.doc.page_count ?? '—'} pages ·
          {(folders.find((f) => f.id === proc.doc.folder_id)?.name) ?? 'Unfiled'} ·
          {proc.doc.uploaded_by ?? '—'}{proc.doc.lang ? ' · ' + proc.doc.lang : ''}
        </div>
      {/if}
    </div>

    <div class="pbody">
      {#if !proc}
        <div class="hint">Loading processing detail…</div>
      {:else if procStatus === 'ready'}
        <!-- ───── RICH INSPECTOR (ready) ───── -->
        <button class="open" onclick={openFullDoc}>Open full document →</button>

        {@const stt = detail?.stats ?? {}}
        {@const pc = detail?.doc?.page_count ?? proc.doc?.page_count ?? 0}
        {@const visN = stt.vision_pages ?? proc.doc?.vision_pages ?? 0}
        {@const txtN = stt.text_pages ?? proc.doc?.text_pages ?? 0}
        {@const secN = stt.sections ?? proc.doc?.sections ?? 0}
        {@const usedN = uses(detail) || (proc.doc?.used_count ?? 0)}
        {@const readPct = pc ? Math.round((visN / pc) * 100) : 0}
        {@const visPct = pc ? Math.round((visN / pc) * 100) : 0}
        {@const txtPct = pc ? Math.round((txtN / pc) * 100) : 0}

        <div class="tiles">
          <div class="tile"><b>{pc || '—'}</b><small>pages</small></div>
          <div class="tile"><b>{secN}</b><small>sections</small></div>
          <div class="tile"><b style="color:var(--coral)">{usedN}×</b><small>used</small></div>
          <div class="tile"><b style="color:var(--green)">{readPct}%</b><small>read</small></div>
        </div>

        <div class="pt">Extraction</div>
        <div class="ibar"><span class="lb2">vision</span><span class="tr"><i style="width:{visPct}%;background:var(--green)"></i></span><span class="pc2">{visPct}%</span></div>
        <div class="ibar"><span class="lb2">text</span><span class="tr"><i style="width:{txtPct}%;background:var(--coral)"></i></span><span class="pc2">{txtPct}%</span></div>

        <div class="kv"><span>Uploaded</span><span class="v">{shortDate(proc.doc?.created_at)} · {relDays(proc.doc?.created_at)}</span></div>
        <div class="kv"><span>Last answered</span><span class="v">{relDays(stt.last_used_at ?? proc.doc?.last_used_at)} · {usedN} questions</span></div>
        <div class="kv"><span>Structure</span><span class="v">{secN} sections{stt.has_tree ? ' · tree ✓' : ''}</span></div>

        <!-- page preview -->
        <div class="prev">
          {#if curPageId}
            <img src="/api/pages/{curPageId}" alt="page {curPageNo}" />
          {:else}
            <span class="prev-ph">page image</span>
          {/if}
          <div class="pg2">
            <button class="pgb" onclick={(e) => { e.stopPropagation(); prevPage(); }} disabled={coverIdx <= 0} aria-label="Previous page">‹</button>
            page {curPageNo} / {totalPages || '?'}
            <button class="pgb" onclick={(e) => { e.stopPropagation(); nextPage(); }} disabled={coverIdx >= ((pageIds.length || totalPages) - 1)} aria-label="Next page">›</button>
          </div>
        </div>

        {#if (detail?.outline?.length ?? 0) > 0}
          <div class="pt">In this document</div>
          {#each detail.outline as o, i (i)}
            <div class="oi">{o.title || ('Page ' + (o.page_no ?? i + 1))}<span class="pn">p.{o.page_no ?? i + 1}</span></div>
          {/each}
        {/if}

        <!-- ───── READER TABS ───── -->
        <div class="pt">Reader</div>
        <div class="rtabs">
          {#each READER_TABS as t (t.key)}
            <button class="rtab" class:on={readerTab === t.key} onclick={() => openReaderTab(t.key)}>{t.label}</button>
          {/each}
        </div>
        <div class="rcontent">
          {#if readerLoading}
            <div class="hint">Loading…</div>
          {:else if readerTab === 'pages'}
            {@const pgs = readerData?.outline || readerData?.pages || []}
            {#if (readerData?.page_count ?? 0) || pgs.length}
              <div class="readlist">
                <div class="rrow"><b>{readerData?.page_count ?? pgs.length}</b> pages indexed.</div>
                {#each pgs as p, i (i)}
                  <div class="rrow">{p.title || p.heading || ('Page ' + (p.page_no ?? p.page ?? i + 1))}</div>
                {/each}
              </div>
            {:else}
              <div class="hint">No page outline.</div>
            {/if}
          {:else if readerTab === 'text'}
            {#if readerData?.md || readerData?.text}
              <pre class="readmd">{readerData.md || readerData.text}</pre>
            {:else if Array.isArray(readerData?.pages) && readerData.pages.length}
              <pre class="readmd">{readerData.pages.map((p: any) => p.md || p.text || p.vision_text || '').join('\n\n')}</pre>
            {:else}
              <div class="hint">No compiled text.</div>
            {/if}
          {:else if readerTab === 'playbook'}
            {@const pb = readerData?.playbook ?? readerData}
            {#if pb && (pb.goal || pb.steps)}
              <div class="readlist">
                {#if pb.goal}<div class="rrow"><b>Goal:</b> {pb.goal}</div>{/if}
                {#if pb.who}<div class="rrow"><b>Who:</b> {pb.who}</div>{/if}
                {#if pb.prerequisites?.length}<div class="rrow"><b>Prerequisites:</b> {pb.prerequisites.join(', ')}</div>{/if}
                {#each (pb.steps || []) as s, i (i)}
                  <div class="rrow">{i + 1}. {typeof s === 'string' ? s : (s.text || s.step || JSON.stringify(s))}</div>
                {/each}
              </div>
            {:else}
              <div class="hint">No playbook for this document.</div>
            {/if}
          {:else if readerTab === 'entities'}
            {@const ents = readerData?.entities || []}
            {#if ents.length}
              <div class="chips2">
                {#each ents as e, i (i)}<span class="chip2">{e.name || e.label || e}</span>{/each}
              </div>
            {:else}
              <div class="hint">No entities indexed.</div>
            {/if}
          {:else if readerTab === 'lookup'}
            {@const lk = readerData?.lookup || readerData?.terms || readerData || []}
            {#if Array.isArray(lk) && lk.length}
              <div class="readlist">
                {#each lk as row, i (i)}
                  <div class="rrow"><b>{row.term || row.key}</b> — {row.value}{row.page ? ` (p.${row.page})` : ''}</div>
                {/each}
              </div>
            {:else}
              <div class="hint">No lookup table.</div>
            {/if}
          {:else if readerTab === 'dependencies'}
            {@const deps = readerData?.dependencies || readerData?.prerequisites || []}
            {#if Array.isArray(deps) && deps.length}
              <div class="readlist">
                {#each deps as dp, i (i)}
                  <div class="rrow">{dp.name || dp.title || dp.doc_name || dp}</div>
                {/each}
              </div>
            {:else}
              <div class="hint">No prerequisite procedures.</div>
            {/if}
          {:else if readerTab === 'tree'}
            {#if readerData?.tree || readerData?.nodes}
              <pre class="readmd">{JSON.stringify(readerData.tree || readerData.nodes, null, 2)}</pre>
            {:else}
              <div class="hint">No troubleshooting tree.</div>
            {/if}
          {/if}
        </div>
      {:else}
        <!-- ───── PIPELINE (queued / processing / failed) ───── -->
        <div class="pt">Pipeline · stage {Math.min(proc.stage + 1, proc.stages.length)} of {proc.stages.length}</div>
        <div class="vt">
          {#each proc.stages as s, i (i)}
            {@const ss = stageState(i)}
            <div class="vstep" class:done={ss === 'done'} class:cur={ss === 'cur'}>
              <div class="d">{ss === 'done' ? '✓' : ''}</div>
              <div><div class="lbl">{s}</div><div class="sub">{stageSub(s)}</div></div>
            </div>
          {/each}
        </div>

        {#if proc.pages?.length}
          <div class="pt">Vision — page images</div>
          <div class="pages">
            {#each proc.pages as p, i (i)}
              <div class="pg" class:ok={p.read} class:cur={i === curPageIdx}>
                {p.page_no}{p.read ? '✓' : ''}
              </div>
            {/each}
          </div>
        {/if}

        <div class="pt">Knowledge enrichers</div>
        {#each ENRICHERS as en (en.key)}
          {@const es = enrState(en.key)}
          <div class="enr" class:ok={es === 'ok'} class:run={es === 'run'} class:wait={es === 'wait'}>
            {#if es === 'run'}<span class="spin sm st"></span>{:else}<span class="st">{es === 'ok' ? '✓' : '○'}</span>{/if}
            {en.label}
            <span class="x">{(proc.enrichers?.[en.key] ?? 0) > 0 ? proc.enrichers[en.key] : (es === 'run' ? 'running…' : 'queued')}</span>
          </div>
        {/each}

        {#if proc.log?.length}
          <div class="pt">Live activity</div>
          <div class="log">
            {#each proc.log as l (l.id)}
              <div class="ln">
                {#if l.ts}<span class="ts">{l.ts}</span>{/if}
                <span class="tg {logClass(l.step)}">{(l.step || '').toUpperCase()}</span>
                <span>{l.msg}</span>
              </div>
            {/each}
            {#if procLive}<div class="ln"><span class="caret"></span></div>{/if}
          </div>
        {/if}
      {/if}
    </div>

    <div class="pfoot">
      {#if procLive}
        <span class="ft-note">auto-updates while processing</span>
        <span class="ft-acts">
          <button class="bsm" onclick={doCancel} disabled={actionBusy}>⏹ Cancel</button>
          <button class="bsm del" onclick={doDelete} disabled={actionBusy}>🗑 Delete</button>
        </span>
      {:else}
        <span class="ft-acts">
          <button class="bsm" onclick={doRetry} disabled={actionBusy}>↻ Re-process</button>
          <span class="movewrap">
            <button class="bsm" onclick={() => (moveOpen = !moveOpen)} disabled={actionBusy}>📁 Move ▾</button>
            {#if moveOpen}
              <div class="movepop">
                <button class="mopt" onclick={() => doMove(null)}>○ Unfiled</button>
                {#each folders as f (f.id)}
                  <button class="mopt" onclick={() => doMove(f.id)}>📁 {f.name}</button>
                {/each}
              </div>
            {/if}
          </span>
          <button class="bsm del" onclick={doDelete} disabled={actionBusy}>🗑 Delete</button>
        </span>
      {/if}
    </div>
  {/if}
</aside>

<!-- ===== CREATE-FOLDER MODAL ===== -->
{#if showFolderModal}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={closeFolderModal} onkeydown={(e) => { if (e.key === 'Escape') closeFolderModal(); }}></div>
  <div class="modal" role="dialog" aria-modal="true" aria-label="New folder"
    onkeydown={(e) => { if (e.key === 'Escape') closeFolderModal(); }}>
    <h2>New folder</h2>
    <label class="row">
      <span class="lab">Name</span>
      <input bind:this={nameInput} class="tin" placeholder="HR Policies" bind:value={newName}
        onkeydown={(e) => { if (e.key === 'Enter' && canCreate) makeFolder(); }} />
    </label>
    <div class="qa">Who can access this folder?</div>
    <div class="opts">
      <label class="opt"><input type="radio" value="sector" bind:group={accessMode} /><span>Everyone in my sector</span></label>
      <label class="opt"><input type="radio" value="specific" bind:group={accessMode} /><span>Specific people / groups</span></label>
      {#if accessMode === 'specific'}
        <div class="specific">
          {#if picked.length === 0}
            <div class="chip-empty">No one added yet — add a person or group below.</div>
          {:else}
            <div class="chips">
              {#each picked as p (p.type + ':' + p.id)}
                <span class="chip">{p.label}<button type="button" class="chip-x" title="Remove" onclick={() => removePrincipal(p)}>×</button></span>
              {/each}
            </div>
          {/if}
          <div class="picker">
            <button type="button" class="addppl" onclick={() => { pickerOpen = !pickerOpen; if (pickerOpen) ensurePrincipals(); }}>+ add person or group ▾</button>
            {#if pickerOpen}
              <div class="pop">
                <input class="psearch" placeholder="Search by email or group…" bind:value={pickerSearch} />
                <div class="plist">
                  {#if !principalsLoaded}<div class="pempty">Loading…</div>
                  {:else if pickerOptions.length === 0}<div class="pempty">No matches.</div>
                  {:else}
                    {#each pickerOptions as p (p.type + ':' + p.id)}
                      <button type="button" class="popt" onclick={() => addPrincipal(p)}><span class="pic">{p.type === 'group' ? '👥' : '◦'}</span>{p.label}</button>
                    {/each}
                  {/if}
                </div>
              </div>
            {/if}
          </div>
        </div>
      {/if}
      <label class="opt"><input type="radio" value="org" bind:group={accessMode} /><span>Whole organisation (all sectors)</span></label>
    </div>
    <div class="actions">
      <button class="btn-cancel" onclick={closeFolderModal} disabled={creating}>Cancel</button>
      <button class="btn-create" onclick={makeFolder} disabled={!canCreate}>{creating ? 'Creating…' : 'Create folder'}</button>
    </div>
  </div>
{/if}

<!-- ===== SHARE / MANAGE-ACCESS MODAL ===== -->
{#if showShareModal && shareFolder}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={closeShareModal} onkeydown={(e) => { if (e.key === 'Escape') closeShareModal(); }}></div>
  <div class="modal" role="dialog" aria-modal="true" aria-label="Share folder"
    onkeydown={(e) => { if (e.key === 'Escape') closeShareModal(); }}>
    <h2>Share — {shareFolder.name}</h2>
    {#if shareLoading}<div class="sloading">Loading current access…</div>{/if}
    <div class="qa">Who can access this folder?</div>
    <div class="opts">
      <label class="opt"><input type="radio" value="sector" bind:group={sAccessMode} /><span>Everyone in my sector</span></label>
      <label class="opt"><input type="radio" value="specific" bind:group={sAccessMode} /><span>Specific people / groups</span></label>
      {#if sAccessMode === 'specific'}
        <div class="specific">
          {#if sPicked.length === 0}
            <div class="chip-empty">No one added yet — add a person or group below.</div>
          {:else}
            <div class="chips">
              {#each sPicked as p (p.type + ':' + p.id)}
                <span class="chip">{p.label}<button type="button" class="chip-x" title="Remove" onclick={() => sRemovePrincipal(p)}>×</button></span>
              {/each}
            </div>
          {/if}
          <div class="picker">
            <button type="button" class="addppl" onclick={() => { sPickerOpen = !sPickerOpen; if (sPickerOpen) ensurePrincipals(); }}>+ add person or group ▾</button>
            {#if sPickerOpen}
              <div class="pop">
                <input class="psearch" placeholder="Search by email or group…" bind:value={sPickerSearch} />
                <div class="plist">
                  {#if !principalsLoaded}<div class="pempty">Loading…</div>
                  {:else if sPickerOptions.length === 0}<div class="pempty">No matches.</div>
                  {:else}
                    {#each sPickerOptions as p (p.type + ':' + p.id)}
                      <button type="button" class="popt" onclick={() => sAddPrincipal(p)}><span class="pic">{p.type === 'group' ? '👥' : '◦'}</span>{p.label}</button>
                    {/each}
                  {/if}
                </div>
              </div>
            {/if}
          </div>
        </div>
      {/if}
      <label class="opt"><input type="radio" value="org" bind:group={sAccessMode} /><span>Whole organisation (all sectors)</span></label>
    </div>
    <div class="actions">
      <button class="btn-cancel" onclick={closeShareModal} disabled={saving}>Cancel</button>
      <button class="btn-create" onclick={saveShare} disabled={!canSave}>{saving ? 'Saving…' : 'Save'}</button>
    </div>
  </div>
{/if}
{/if}

<style>
  :global(:root) {
    --cream: #faf9f5; --paper: #fff; --sand: #f0eee6; --ink: #1f1e1d; --muted: #8a857c; --line: #e9e6dd;
    --coral: #c2683f; --blue: #3f7fb0; --teal: #2f8f83; --violet: #7b6bd6; --amber: #c98a2e; --green: #3f8f5f; --red: #c0492f;
  }

  /* ===== 3-pane shell ===== */
  .src-shell { height: 100%; display: flex; min-height: 0; background: var(--cream); }

  /* === LEFT folder rail (persistent) === */
  .rail { width: 228px; flex: none; background: var(--sand); border-right: 1px solid #e4e0d6; display: flex; flex-direction: column; padding: 12px 10px; overflow: auto; }
  .rail .grp { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; padding: 8px 10px 6px; }
  .fitem { display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: 9px; font-size: 13px; color: #4a463e; cursor: pointer; border: none; background: transparent; width: 100%; text-align: left; font: inherit; }
  .fitem:hover { background: #e7e3d9; }
  .fitem.on { background: #fff; color: var(--blue); font-weight: 600; box-shadow: inset 2px 0 0 var(--blue); }
  .fitem .ic { width: 16px; text-align: center; flex: none; }
  .fitem .fname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fitem .n { margin-left: auto; color: var(--muted); font-size: 11px; flex: none; }
  .fitem .lk { font-size: 10px; color: var(--amber); flex: none; }
  .fitem .sh { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 6px; color: var(--muted); opacity: .55; cursor: pointer; }
  .fitem:hover .sh { opacity: 1; }
  .fitem .sh:hover { background: #eaf2f8; color: var(--blue); opacity: 1; }
  .rail-empty { padding: 8px 11px; color: var(--muted); font-size: 12px; }
  .newf { margin: 8px 6px; padding: 9px; border: 1px dashed #cfc9bc; border-radius: 10px; text-align: center; color: var(--muted); font-size: 12.5px; cursor: pointer; }
  .newf:hover { background: #e7e3d9; color: var(--blue); border-color: var(--blue); }
  .rail .spacer { flex: 1; }
  .railfoot { font-size: 11px; color: var(--muted); padding: 8px 10px; border-top: 1px solid #e4e0d6; }

  /* === CENTER doc list === */
  .center { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--cream); overflow: auto; }
  .chead { display: flex; align-items: center; gap: 6px; padding: 16px 22px 10px; }
  .chead h1 { margin: 0; font-size: 18px; }
  .chead small { color: var(--muted); font-weight: 400; margin-left: 4px; font-size: 13px; }
  .up { margin-left: auto; background: var(--ink); color: #fff; border: none; border-radius: 9px; padding: 8px 13px; font-weight: 600; font-size: 13px; cursor: pointer; }
  .up:disabled { opacity: .55; cursor: default; }
  .hidden-file { display: none; }
  .list { margin: 0 16px 20px; background: #fff; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
  .lr { cursor: pointer; border: none; background: transparent; width: 100%; text-align: left; font: inherit; }
  .lr:hover { background: #fcfbf8; }
  .lr.sel { background: #f3f8fc; box-shadow: inset 3px 0 0 var(--blue); }
  .lr:last-child { border-bottom: none; }
  .nm { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
  .muted { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pb { height: 5px; background: #edeae3; border-radius: 99px; overflow: hidden; width: 100%; margin-top: 6px; }
  .pb i { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), #7cb4da); }
  .pill { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 6px; justify-self: start; }
  .p-proc { background: #eaf2f8; color: var(--blue); }
  .p-ready { background: #e7f3ec; color: var(--green); }
  .p-fail { background: #f7e7e3; color: var(--red); }
  .p-queued { background: #fbf3e6; color: var(--amber); }

  .empty { padding: 40px 16px; text-align: center; color: var(--muted); font-size: 13px; }

  /* coral token alias (mockup uses --coral; app token is --clay = coral) */
  :global(:root) { --coral: var(--clay); }

  /* === category chips === */
  .chips-row { display: flex; gap: 8px; flex-wrap: wrap; padding: 4px 22px 6px; align-items: center; }
  .chip { font-size: 12.5px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--line); background: #fff; color: #5a554c; cursor: pointer; font: inherit; }
  .chip:hover { background: var(--sand); }
  .chip.on { border-color: var(--coral); background: #fbeee7; color: var(--coral); font-weight: 600; }
  .chip .c { color: var(--muted); margin-left: 5px; }
  .chip.on .c { color: var(--coral); }
  .retag { margin-left: auto; font-size: 12px; color: var(--coral); border: 1px solid #eccdbd; background: #fff; border-radius: 8px; padding: 5px 10px; cursor: pointer; font: inherit; }
  .retag:hover { background: #fbeee7; }
  .retag:disabled { opacity: .55; cursor: default; }

  /* === toolbar === */
  .tb { display: flex; align-items: center; gap: 10px; padding: 4px 22px 8px; font-size: 12.5px; color: var(--muted); flex-wrap: wrap; }
  .tb-lab { color: var(--muted); }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; }
  .seg button { border: none; background: none; padding: 5px 11px; font: inherit; font-size: 12.5px; cursor: pointer; color: var(--muted); }
  .seg button.on { background: var(--sand); color: var(--ink); font-weight: 600; }
  .jobs { border: 1px solid #eccdbd; background: #fff; color: var(--coral); border-radius: 8px; padding: 5px 10px; display: flex; gap: 6px; align-items: center; }
  .tb-sort { margin-left: auto; display: flex; gap: 5px; align-items: center; }
  .tb-sort select { border: 1px solid var(--line); border-radius: 7px; background: #fff; font: inherit; font-size: 12.5px; padding: 4px 6px; color: var(--ink); cursor: pointer; }

  /* === rich list === */
  .glbl { padding: 11px 16px 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; }
  .glbl.pad { padding: 11px 22px 6px; }
  .list .lh, .list .lr { display: grid; grid-template-columns: 1fr 56px 56px 58px 78px 70px; align-items: center; gap: 8px; padding: 11px 16px; border-bottom: 1px solid var(--line); }
  .list .lh { color: var(--muted); font-size: 10.5px; text-transform: uppercase; font-weight: 600; }
  .list .lr:last-child { border-bottom: none; }
  .ti { display: flex; align-items: center; gap: 9px; min-width: 0; }
  .ti .ic { width: 26px; height: 30px; border-radius: 6px; background: #eef2f6; border: 1px solid #dde6ee; flex: none; }
  .ti .ic.my { background: #fbf3e6; border-color: #ecd9b3; }
  .tt { min-width: 0; }
  .tt .s { color: var(--muted); font-size: 11px; margin-top: 2px; display: flex; gap: 6px; align-items: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 400; }
  .badge { font-size: 9.5px; font-weight: 700; letter-spacing: .03em; padding: 1px 6px; border-radius: 5px; background: #fbeee7; color: var(--coral); flex: none; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); display: inline-block; flex: none; }
  .dot.b { background: var(--blue); }
  .dot.f { background: var(--red); }
  .lang { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 5px; justify-self: start; }
  .lang.en { background: #eaf2f8; color: var(--blue); }
  .lang.my { background: #fbf3e6; color: var(--amber); }
  .used { color: var(--coral); font-weight: 600; }
  .acc { color: var(--green); font-weight: 600; }

  /* === cards view === */
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 12px; padding: 4px 22px 16px; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; cursor: pointer; text-align: left; }
  .card:hover { border-color: #d8d3c7; }
  .card.sel { border-color: var(--blue); box-shadow: inset 0 0 0 1px var(--blue); }
  .card-cover { position: relative; height: 96px; background: #f4f2ec; display: grid; place-items: center; overflow: hidden; }
  .card-cover img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
  .card-noimg { font-size: 11px; font-weight: 700; color: var(--coral); background: #fbeee7; padding: 4px 8px; border-radius: 6px; }
  .card-cover .lang { position: absolute; top: 7px; right: 7px; }
  .card-body { padding: 9px 10px 11px; }
  .card-ttl { font-weight: 600; font-size: 13px; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .card-sub { display: flex; gap: 6px; align-items: center; margin-top: 6px; font-size: 11px; color: var(--muted); }
  .card-stats { display: flex; gap: 10px; margin-top: 7px; font-size: 11.5px; color: var(--muted); }

  /* spinner */
  .spin { width: 12px; height: 12px; border: 2px solid var(--blue); border-top-color: transparent; border-radius: 50%; animation: sp .8s linear infinite; flex: none; display: inline-block; }
  .spin.sm { width: 11px; height: 11px; }
  @keyframes sp { to { transform: rotate(360deg); } }

  /* === RIGHT process panel (slide-over) === */
  .proc-scrim { position: fixed; inset: 0; background: rgba(20, 18, 16, .16); display: none; z-index: 60; border: none; }
  .proc-scrim.open { display: block; }
  .proc { position: fixed; top: 0; right: 0; width: 520px; max-width: 95vw; height: 100vh; background: #fff; border-left: 1px solid var(--line); transform: translateX(100%); transition: transform .22s ease; z-index: 61; display: flex; flex-direction: column; }
  .proc.open { transform: none; }
  .ph { padding: 15px 18px; border-bottom: 1px solid var(--line); position: relative; }
  .ph h2 { margin: 0; font-size: 15.5px; padding-right: 24px; }
  .ph .meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .ph .live { position: absolute; top: 15px; right: 42px; display: flex; gap: 6px; align-items: center; color: var(--blue); font-weight: 600; font-size: 12px; }
  .px { position: absolute; top: 13px; right: 14px; background: none; border: none; font-size: 20px; color: var(--muted); cursor: pointer; }
  .pbody { flex: 1; overflow: auto; padding: 16px 18px; }
  .pt { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; margin: 14px 0 9px; }
  .pt:first-child { margin-top: 0; }

  /* vertical stage timeline */
  .vt { position: relative; margin-left: 6px; }
  .vstep { display: flex; gap: 12px; padding-bottom: 14px; position: relative; }
  .vstep::before { content: ""; position: absolute; left: 8px; top: 18px; bottom: -2px; width: 2px; background: #dfe7ee; }
  .vstep:last-child::before { display: none; }
  .vstep .d { width: 18px; height: 18px; border-radius: 50%; background: #dfe7ee; border: 2px solid #dfe7ee; flex: none; display: grid; place-items: center; color: #fff; font-size: 10px; z-index: 1; }
  .vstep.done .d { background: var(--blue); border-color: var(--blue); }
  .vstep.cur .d { background: #fff; border-color: var(--blue); animation: pl 1.2s infinite; }
  @keyframes pl { 0%, 100% { box-shadow: 0 0 0 0 rgba(63, 127, 176, .4); } 50% { box-shadow: 0 0 0 6px rgba(63, 127, 176, 0); } }
  .vstep .lbl { font-size: 13px; font-weight: 600; }
  .vstep.cur .lbl { color: var(--blue); }
  .vstep:not(.done):not(.cur) .lbl { color: var(--muted); font-weight: 500; }
  .vstep .sub { font-size: 11.5px; color: var(--muted); margin-top: 1px; }

  /* per-page vision chips */
  .pages { display: flex; flex-wrap: wrap; gap: 6px; }
  .pg { width: 36px; height: 46px; border-radius: 6px; border: 1px solid var(--line); display: grid; place-items: center; font-size: 11px; font-weight: 600; color: var(--muted); background: #faf9f6; }
  .pg.ok { background: #e7f3ec; border-color: #bfe0cb; color: var(--green); }
  .pg.cur { border-color: var(--blue); color: var(--blue); background: #eaf2f8; animation: pl 1.2s infinite; }

  /* enrichers */
  .enr { display: flex; align-items: center; gap: 9px; padding: 6px 0; font-size: 12.5px; border-bottom: 1px dashed var(--line); }
  .enr:last-child { border-bottom: none; }
  .enr .st { width: 15px; text-align: center; flex: none; }
  .enr.ok .st { color: var(--green); }
  .enr.run { color: var(--blue); font-weight: 600; }
  .enr.wait { color: var(--muted); }
  .enr .x { margin-left: auto; color: var(--muted); font-size: 11px; }

  /* log */
  .log { font: 11.5px/1.7 ui-monospace, Menlo, monospace; max-height: 170px; overflow: auto; background: #fbfaf7; border-radius: 8px; padding: 8px 10px; }
  .ln { display: flex; gap: 8px; }
  .ln .ts { color: #b8b2a6; flex: none; }
  .tg { font-weight: 700; flex: none; width: 56px; }
  .c-render { color: var(--violet); }
  .c-vision { color: var(--blue); }
  .c-compile { color: var(--teal); }
  .c-enrich { color: var(--amber); }
  .caret { display: inline-block; width: 6px; height: 12px; background: var(--blue); animation: bl 1s steps(1) infinite; }
  @keyframes bl { 50% { opacity: 0; } }

  /* reader tabs */
  .rtabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .rtab { border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 6px 11px; font: inherit; font-size: 12px; font-weight: 600; color: #4a463e; cursor: pointer; }
  .rtab:hover { background: var(--sand); }
  .rtab.on { background: var(--blue); border-color: var(--blue); color: #fff; }
  .rcontent { font-size: 13px; }
  .readlist { display: flex; flex-direction: column; gap: 6px; }
  .rrow { padding: 7px 10px; background: #fbfaf7; border: 1px solid var(--line); border-radius: 8px; line-height: 1.45; }
  .readmd { white-space: pre-wrap; word-break: break-word; font: 12px/1.6 ui-monospace, Menlo, monospace; background: #fbfaf7; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; max-height: 60vh; overflow: auto; margin: 0; }
  .chips2 { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip2 { background: #eaf2f8; color: var(--blue); border-radius: 999px; padding: 4px 11px; font-size: 12px; font-weight: 600; }

  /* === rich inspector (ready docs) === */
  .open { width: 100%; background: var(--coral); color: #fff; border: none; border-radius: 10px; padding: 11px; font: inherit; font-weight: 600; font-size: 13.5px; cursor: pointer; margin-bottom: 14px; }
  .open:hover { filter: brightness(.96); }
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; margin-bottom: 4px; }
  .tile { border: 1px solid var(--line); border-radius: 9px; padding: 9px 4px; text-align: center; }
  .tile b { display: block; font-size: 16px; }
  .tile small { color: var(--muted); font-size: 9.5px; text-transform: uppercase; }
  .ibar { display: flex; align-items: center; gap: 9px; padding: 5px 0; font-size: 12px; }
  .ibar .lb2 { width: 42px; color: var(--muted); flex: none; }
  .ibar .tr { flex: 1; height: 6px; background: #edeae3; border-radius: 99px; overflow: hidden; }
  .ibar .tr i { display: block; height: 100%; }
  .ibar .pc2 { width: 38px; text-align: right; color: var(--muted); flex: none; }
  .kv { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 12.5px; }
  .kv .v { color: var(--muted); }
  .prev { margin-top: 12px; border: 1px solid var(--line); border-radius: 11px; background: #faf9f6; min-height: 170px; display: grid; place-items: center; color: var(--muted); position: relative; overflow: hidden; }
  .prev img { width: 100%; max-height: 320px; object-fit: contain; display: block; }
  .prev-ph { font-size: 12px; }
  .prev .pg2 { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); display: flex; gap: 10px; align-items: center; font-size: 11px; color: var(--muted); background: rgba(255,255,255,.9); border-radius: 8px; padding: 3px 8px; }
  .prev .pgb { width: 22px; height: 22px; border: 1px solid var(--line); border-radius: 6px; display: grid; place-items: center; background: #fff; cursor: pointer; font: inherit; color: var(--ink); }
  .prev .pgb:disabled { opacity: .4; cursor: default; }
  .oi { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px dashed var(--line); font-size: 12.5px; color: #4a463e; }
  .oi .pn { margin-left: auto; color: var(--muted); font-size: 11px; flex: none; }

  .pfoot { padding: 11px 18px; border-top: 1px solid var(--line); display: flex; gap: 8px; align-items: center; color: var(--muted); font-size: 12px; }
  .ft-note { color: var(--muted); }
  .ft-acts { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .bsm { border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 6px 11px; font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
  .bsm:hover { background: var(--sand); }
  .bsm:disabled { opacity: .5; cursor: default; }
  .bsm.del { color: var(--red); border-color: #e8c9c1; }
  .movewrap { position: relative; }
  .movepop { position: absolute; bottom: calc(100% + 6px); right: 0; min-width: 180px; max-height: 240px; overflow: auto; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 6px; z-index: 5; box-shadow: 0 6px 24px rgba(0, 0, 0, .12); }
  .mopt { display: flex; align-items: center; gap: 7px; width: 100%; text-align: left; border: none; background: transparent; border-radius: 7px; padding: 7px 9px; font: inherit; font-size: 12.5px; color: #4a463e; cursor: pointer; }
  .mopt:hover { background: var(--sand); }
  .hint { text-align: center; color: var(--muted); font-size: 12px; padding: 14px 6px; }

  /* ── create / share modals ── */
  .scrim { position: fixed; inset: 0; background: rgba(31, 30, 29, .32); z-index: 80; border: none; }
  .modal { position: fixed; z-index: 81; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(440px, calc(100vw - 32px)); background: var(--paper); border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px 18px; }
  .modal h2 { margin: 0 0 16px; font-size: 17px; font-weight: 650; color: var(--ink); }
  .modal .row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  .modal .lab { flex: none; width: 46px; font-size: 13px; color: var(--muted); font-weight: 600; }
  .modal .tin { flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: 9px; padding: 9px 11px; font: inherit; font-size: 13.5px; background: #fff; }
  .modal .tin:focus { outline: none; border-color: var(--blue); }
  .modal .qa { font-size: 12.5px; font-weight: 600; color: var(--muted); margin-bottom: 8px; }
  .opts { display: flex; flex-direction: column; gap: 4px; }
  .opt { display: flex; align-items: center; gap: 9px; padding: 8px 9px; border-radius: 9px; font-size: 13.5px; color: #4a463e; cursor: pointer; }
  .opt:hover { background: var(--sand); }
  .opt input { accent-color: var(--blue); width: 15px; height: 15px; flex: none; }
  .specific { margin: 2px 0 4px 30px; padding: 10px 12px; background: var(--cream); border: 1px solid var(--line); border-radius: 10px; }
  .chip-empty { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 5px; background: #eaf2f8; color: var(--blue); border-radius: 999px; padding: 4px 6px 4px 11px; font-size: 12px; font-weight: 600; }
  .chip-x { border: none; background: transparent; color: var(--blue); cursor: pointer; font-size: 15px; line-height: 1; padding: 0 2px; }
  .picker { position: relative; }
  .addppl { border: 1px dashed var(--line); background: #fff; border-radius: 8px; padding: 7px 11px; font: inherit; font-size: 12.5px; font-weight: 600; color: var(--blue); cursor: pointer; }
  .addppl:hover { background: #eaf2f8; }
  .pop { position: absolute; top: calc(100% + 5px); left: 0; right: 0; z-index: 5; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 7px; }
  .psearch { width: 100%; border: 1px solid var(--line); border-radius: 7px; padding: 6px 8px; font: inherit; font-size: 12.5px; margin-bottom: 6px; }
  .psearch:focus { outline: none; border-color: var(--blue); }
  .plist { max-height: 168px; overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }
  .popt { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: none; background: transparent; border-radius: 7px; padding: 7px 8px; font: inherit; font-size: 13px; color: #4a463e; cursor: pointer; }
  .popt:hover { background: var(--sand); }
  .popt .pic { flex: none; width: 16px; text-align: center; color: var(--muted); }
  .pempty { padding: 8px; font-size: 12px; color: var(--muted); }
  .sloading { font-size: 12px; color: var(--muted); margin: -8px 0 12px; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
  .btn-cancel { border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 9px 15px; font: inherit; font-size: 13px; font-weight: 600; color: #4a463e; cursor: pointer; }
  .btn-cancel:hover { background: var(--sand); }
  .btn-create { border: none; background: var(--blue); color: #fff; border-radius: 9px; padding: 9px 16px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-create:disabled { opacity: .45; cursor: default; }

  @media (max-width: 760px) {
    .rail { width: 180px; }
    .proc { width: 100vw; }
  }
</style>
