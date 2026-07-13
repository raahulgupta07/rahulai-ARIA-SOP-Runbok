<script lang="ts">
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import { parseDocName } from '$lib/docname';
  import { onDestroy } from 'svelte';
  import { goto } from '$app/navigation';

  // ── reactive admin/me gate (cachedUser is non-reactive → revalidate via me()) ──
  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  type Folder = {
    id: number; name: string; sector_id?: number | null; sector_label?: string | null; doc_count: number;
    access_mode?: 'sector' | 'specific' | 'org'; access_n?: number;
    parent_id?: number | null; is_expanded?: boolean;
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
  let docs = $state<Doc[]>([]);
  let total = $state(0);
  let selected = $state<number | 'all'>('all');   // 'all' aggregate view, or a real folder id
  let loadingDocs = $state(false);

  // ── center list: category filter / group / view / sort ──
  let catFilter = $state<string>('all');           // 'all' or a category label
  let groupBy = $state<'date' | 'category'>('date');
  let viewMode = $state<'list' | 'cards'>('list');
  let sortBy = $state<'newest' | 'oldest' | 'used' | 'name' | 'pages' | 'acc'>('newest');
  let sortDir = $state<'asc' | 'desc'>('desc');     // column-header sort direction
  let retagging = $state(false);
  let query = $state('');                            // live title search
  let dragOver = $state(false);                      // drag-drop dropzone state
  let selIds = $state<Set<number>>(new Set());       // bulk-selected doc ids
  let bulkBusy = $state(false);
  let bulkMoveOpen = $state(false);

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
    newParent = null;                       // top-level
    newName = ''; accessMode = 'sector'; picked = [];
    pickerOpen = false; pickerSearch = '';
    showFolderModal = true;
    ensurePrincipals();
    setTimeout(() => nameInput?.focus(), 0);
  }

  // ── folder tree (nesting) ──────────────────────────────────────────────
  const MAX_DEPTH = 6;                       // matches backend MAX_FOLDER_DEPTH
  let newParent = $state<number | null>(null);   // parent for the create-folder modal
  // group folders by parent so the rail can render a tree
  let childrenOf = $derived.by(() => {
    const m = new Map<number | null, Folder[]>();
    for (const f of folders) {
      const p = (f.parent_id ?? null) as number | null;
      (m.get(p) ?? m.set(p, []).get(p)!).push(f);
    }
    return m;
  });
  let topFolders = $derived(childrenOf.get(null) ?? []);
  const kids = (id: number): Folder[] => childrenOf.get(id) ?? [];
  const nameById = (id: number | null): string =>
    id == null ? 'Top level' : (folders.find((f) => f.id === id)?.name ?? 'Folder');

  async function toggleExpand(f: Folder, e?: Event) {
    e?.stopPropagation();
    const next = !f.is_expanded;
    const i = folders.findIndex((x) => x.id === f.id);
    if (i >= 0) folders[i] = { ...folders[i], is_expanded: next };   // Svelte5: mutate by index
    try { await api.patchFolder(f.id, { is_expanded: next }); } catch {}
  }
  function openSubfolder(parentId: number, e?: Event) {
    e?.stopPropagation();
    newParent = parentId; newName = ''; accessMode = 'sector'; picked = [];
    pickerOpen = false; pickerSearch = '';
    showFolderModal = true; ensurePrincipals();
    setTimeout(() => nameInput?.focus(), 0);
  }

  // ── directory import — rebuild the folder tree from webkitRelativePath ──
  type ImpNode = { name: string; path: string; parentPath: string | null; files: number; exists: boolean };
  let dirImportOpen = $state(false);
  let dirFiles = $state<File[]>([]);
  let dirTree = $state<ImpNode[]>([]);
  let dirRootName = $state('');
  let dirUnderId = $state<number | null>(null);   // "Create under" an existing folder
  let dirBusy = $state(false);
  let dirProgress = $state('');
  let dirNewCount = $derived(dirTree.filter((n) => !n.exists).length);

  const relOf = (f: File) => ((f as any).webkitRelativePath as string) || f.name;
  const isJunk = (rel: string) => rel.split('/').some((s) => s.startsWith('.') || s.startsWith('~$'));
  const dirOf = (f: File) => { const p = relOf(f).split('/'); p.pop(); return p.slice(0, MAX_DEPTH).join('/'); };

  function onDirPicked(e: Event) {
    const input = e.target as HTMLInputElement;
    const ok = /\.(pdf|png|jpe?g)$/i;
    const files = Array.from(input.files ?? []).filter((f) => ok.test(f.name) && !isJunk(relOf(f)));
    input.value = '';
    if (!files.length) { flashToast('No PDF/PNG/JPG files in that folder', 'err'); return; }
    // every folder path that appears in the tree, + file count per leaf dir
    const paths = new Set<string>();
    const fileCount = new Map<string, number>();
    for (const f of files) {
      const segs = relOf(f).split('/'); segs.pop();
      const capped = segs.slice(0, MAX_DEPTH);
      let acc = '';
      for (const s of capped) { acc = acc ? `${acc}/${s}` : s; paths.add(acc); }
      const leaf = capped.join('/');
      if (leaf) fileCount.set(leaf, (fileCount.get(leaf) ?? 0) + 1);
    }
    dirRootName = relOf(files[0]).split('/')[0] || 'folder';
    const existingTop = new Set(topFolders.map((f) => f.name.toLowerCase()));
    dirTree = [...paths]
      .sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b))
      .map((p) => {
        const segs = p.split('/'); const name = segs[segs.length - 1];
        const parentPath = segs.length > 1 ? segs.slice(0, -1).join('/') : null;
        // "exists" hint is top-level only; deeper dedup is handled server-side (get-or-create)
        const exists = parentPath === null && existingTop.has(name.toLowerCase());
        return { name, path: p, parentPath, files: fileCount.get(p) ?? 0, exists };
      });
    dirFiles = files;
    dirUnderId = typeof selected === 'number' ? selected : null;
    dirImportOpen = true;
  }

  async function runDirImport() {
    if (dirBusy) return;
    dirBusy = true;
    const pathToId = new Map<string, number>();
    try {
      let made = 0;
      for (const n of dirTree) {                       // dirTree is depth-sorted (parents first)
        const parentId = n.parentPath ? (pathToId.get(n.parentPath) ?? dirUnderId) : dirUnderId;
        dirProgress = `Creating ${n.path}…`;
        const r = await api.ensureFolder(n.name, parentId);
        pathToId.set(n.path, r.folder.id);
        if (r.created) made++;
      }
      let up = 0, errs = 0;
      for (const f of dirFiles) {
        const fid = pathToId.get(dirOf(f)) ?? dirUnderId;
        dirProgress = `Uploading ${f.name}…`;
        try { await api.uploadTo(f, fid); up++; } catch { errs++; }
      }
      dirImportOpen = false;
      await Promise.all([loadFolders(), loadDocs()]);
      flashToast(
        `Imported ${up} file${up === 1 ? '' : 's'} into ${made} new folder${made === 1 ? '' : 's'}` +
        (errs ? ` · ${errs} failed` : ''), errs ? 'err' : 'ok');
    } finally { dirBusy = false; dirProgress = ''; }
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
  let dirInput = $state<HTMLInputElement>();        // webkitdirectory picker
  // ── Add ▾ menu ──
  let addOpen = $state(false);
  let serverScan = $state<{ found: number; new: number } | null>(null);
  let s3Scan = $state<{ configured: boolean; found: number; new: number } | null>(null);
  function toggleAdd() {
    addOpen = !addOpen;
    if (addOpen && serverScan === null) {
      api.scanPreview().then((r: any) => { serverScan = { found: r.found ?? 0, new: r.new ?? 0 }; }).catch(() => { serverScan = { found: 0, new: 0 }; });
      api.s3ScanPreview().then((r: any) => { s3Scan = r; }).catch(() => { s3Scan = { configured: false, found: 0, new: 0 }; });
    }
  }
  async function importS3() {
    addOpen = false;
    uploading = true;
    try {
      const r: any = await api.s3Import();
      const q = r.queued ?? 0;
      if (q) { flashToast(`${q} document${q > 1 ? 's' : ''} queued from S3`, 'ok'); await Promise.all([loadFolders(), loadDocs()]); }
      else flashToast(`Nothing new in S3 (${r.skipped ?? 0} already present)`, 'ok');
    } catch (e: any) {
      const msg = (e?.message || '').toLowerCase();
      if (msg.includes('not configured') || msg.includes('s3_bucket') || msg.includes('400')) flashToast('S3 not configured (set S3_BUCKET)', 'err');
      else flashToast(e?.message || 'S3 import failed', 'err');
    } finally { uploading = false; }
  }
  function pickDir() { addOpen = false; dirInput?.click(); }

  // ── delete confirm modal (double-confirm: info + type-to-arm) ──
  type DelReq = { mode: 'one'; id: number; doc: any } | { mode: 'bulk'; n: number } | { mode: 'folder'; folder: Folder };
  let delReq = $state<DelReq | null>(null);
  let delInput = $state('');
  let delArmed = $derived.by(() => {
    if (!delReq) return false;
    if (delReq.mode === 'bulk') return delInput.trim().toUpperCase() === 'DELETE';
    if (delReq.mode === 'folder') {
      const fn = (delReq.folder?.name || '').trim();
      return fn.length > 0 && delInput.trim() === fn;
    }
    const name = (delReq.doc?.name || '').trim();
    return name.length > 0 && delInput.trim() === name;
  });
  function openDel(r: DelReq) { delReq = r; delInput = ''; }
  function closeDel() { delReq = null; delInput = ''; }
  async function confirmDelete() {
    if (!delReq || !delArmed) return;
    const r = delReq; closeDel();
    if (r.mode === 'one') await performDelete(r.id);
    else if (r.mode === 'folder') await performFolderDelete(r.folder);
    else await performBulkDelete();
  }
  async function performFolderDelete(f: Folder) {
    try {
      const r: any = await api.deleteFolder(f.id);
      if (selected === f.id) selected = 'all';
      await Promise.all([loadFolders(), loadDocs()]);
      const n = r?.unfiled_docs ?? 0;
      flashToast(n > 0 ? `Folder deleted · ${n} doc${n === 1 ? '' : 's'} un-filed` : 'Folder deleted', 'ok');
    } catch (err: any) {
      flashToast(err?.message?.includes('403') ? 'Not allowed to delete this folder' : 'Delete failed', 'err');
    }
  }
  async function importServer() {
    addOpen = false;
    uploading = true;
    try {
      const r: any = await api.scanImport();
      const q = r.queued ?? 0;
      if (q) { flashToast(`${q} document${q > 1 ? 's' : ''} queued from server`, 'ok'); await Promise.all([loadFolders(), loadDocs()]); }
      else flashToast(`Nothing new on server (${r.skipped ?? 0} already present)`, 'ok');
      serverScan = null;
    } catch (e: any) { flashToast(e?.message || 'Server import failed', 'err'); }
    finally { uploading = false; }
  }
  async function importGraph(kind: 'sharepoint' | 'onedrive') {
    addOpen = false;
    const label = kind === 'sharepoint' ? 'SharePoint' : 'OneDrive';
    uploading = true;
    try {
      const r: any = await api.graphImport(kind);
      const q = r.queued ?? 0;
      if (q) { flashToast(`${q} document${q > 1 ? 's' : ''} queued from ${label}`, 'ok'); await Promise.all([loadFolders(), loadDocs()]); }
      else flashToast(`Nothing new in ${label} (${r.skipped ?? 0} already present)`, 'ok');
    } catch (e: any) {
      const msg = (e?.message || '').toLowerCase();
      if (msg.includes('not configured') || msg.includes('not enabled') || msg.includes('400')) {
        flashToast(`${label} not set up — Settings → Integrations`, 'err');
        setTimeout(() => goto('/settings/microsoft'), 900);
      } else flashToast(e?.message || `${label} import failed`, 'err');
    } finally { uploading = false; }
  }
  let fileInput: HTMLInputElement | null = null;
  let uploadToast = $state('');          // error/skip message shown on screen
  let uploadToastKind = $state<'err' | 'ok'>('err');
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  function flashToast(msg: string, kind: 'err' | 'ok' = 'err') {
    uploadToast = msg;
    uploadToastKind = kind;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { uploadToast = ''; }, 6000);
  }

  // ── derived: which folder_id query to send for the selected source ──
  let selFolderId = $derived(typeof selected === 'number' ? selected : null);
  let breadcrumb = $derived.by(() => {
    if (selected === 'all') return 'All documents';
    return folders.find((f) => f.id === selected)?.name ?? 'Folder';
  });

  function removeFolder(f: Folder, e?: Event) {
    e?.stopPropagation();
    openDel({ mode: 'folder', folder: f });   // styled double-confirm (type folder name)
  }

  async function loadFolders() {
    try {
      const r = await api.folders();
      folders = r.folders || [];
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
  let inFlight = $derived(docs.some((d) => d.status === 'queued' || d.status === 'processing' || d.status === 'ready_lite'));
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

  // ── jobs (queued / processing / still-enriching) ──
  let activeJobs = $derived(docs.filter((d) => {
    const s = (d.status || 'ready').toLowerCase();
    return s === 'queued' || s === 'processing' || s === 'ready_lite';
  }));
  let jobsCount = $derived(activeJobs.length);
  let jobsOpen = $state(false);

  // ── Enrichment Agent (deferred phase-2) ──
  let agent = $state<any>(null);          // { enabled, running, paused, concurrency, enriching, queued, recent }
  let agentBusy = $state(false);
  let agentTimer: ReturnType<typeof setInterval> | null = null;
  async function loadAgent() { try { agent = await api.enrichStatus(); } catch { agent = null; } }
  $effect(() => {
    if (jobsOpen) {
      loadAgent();
      if (!agentTimer) agentTimer = setInterval(loadAgent, 2500);
    } else if (agentTimer) { clearInterval(agentTimer); agentTimer = null; }
  });
  onDestroy(() => { if (agentTimer) clearInterval(agentTimer); });
  async function toggleAgent() {
    if (!agent || agentBusy) return;
    agentBusy = true;
    try {
      if (agent.paused) { await api.enrichResume(); flashToast('Agent resumed', 'ok'); }
      else { await api.enrichPause(); flashToast('Agent paused', 'ok'); }
      await loadAgent();
    } catch (e: any) { flashToast(e?.message || 'Could not toggle agent', 'err'); }
    finally { agentBusy = false; }
  }
  async function setConcurrency(n: number) {
    n = Math.max(1, Math.min(8, Math.round(n) || 1));
    try { await api.enrichConcurrency(n); await loadAgent(); } catch {}
  }
  async function skipEnrich(d: Doc, e: Event) {
    e.stopPropagation();
    try { await api.enrichSkip(d.id); await Promise.all([loadAgent(), loadDocs()]); flashToast('Accepted as-is', 'ok'); }
    catch (err: any) { flashToast(err?.message || 'Could not skip', 'err'); }
  }
  function jobStage(d: Doc): string {
    const s = (d.status || '').toLowerCase();
    if (s === 'queued') return 'queued';
    if (s === 'ready_lite') return 'answerable · enriching';
    if (s === 'processing') return 'processing';
    return s || 'working';
  }
  function jobPct(d: Doc): number {
    if (typeof d.progress === 'number' && d.progress > 0) return Math.min(100, Math.round(d.progress));
    const s = (d.status || '').toLowerCase();
    if (s === 'ready_lite') return 50;          // text done, vision/enrichers pending
    if (s === 'queued') return 5;
    return 0;
  }
  function openJob(d: Doc) { jobsOpen = false; openPanel(d); }
  // ── stop controls ──
  let stoppingIds = $state<Set<number>>(new Set());
  let stopAllBusy = $state(false);
  async function stopJob(d: Doc, e: Event) {
    e.stopPropagation();
    if (stoppingIds.has(d.id)) return;
    stoppingIds = new Set(stoppingIds).add(d.id);
    try { await api.cancelDoc(d.id); await Promise.all([loadFolders(), loadDocs()]); flashToast('Job stopped', 'ok'); }
    catch (err: any) { flashToast(err?.message || 'Could not stop job', 'err'); }
    finally { const s = new Set(stoppingIds); s.delete(d.id); stoppingIds = s; }
  }
  async function stopAllJobs() {
    if (stopAllBusy || activeJobs.length === 0) return;
    if (!confirm(`Stop all ${activeJobs.length} job${activeJobs.length > 1 ? 's' : ''}? Queued and processing documents will be halted.`)) return;
    stopAllBusy = true;
    try { await api.ingestStop(); await Promise.all([loadFolders(), loadDocs()]); flashToast('All jobs stopped', 'ok'); }
    catch (err: any) { flashToast(err?.message || 'Could not stop jobs', 'err'); }
    finally { stopAllBusy = false; }
  }

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
    const q = query.trim().toLowerCase();
    if (q) list = list.filter((d) => title(d).toLowerCase().includes(q) || (d.name || '').toLowerCase().includes(q));
    const t = (d: Doc) => (d.created_at ? new Date(d.created_at).getTime() : 0);
    const dir = sortDir === 'asc' ? 1 : -1;
    list = [...list].sort((a, b) => {
      // column-header sorts honour sortDir; the legacy select keeps its own order
      if (sortBy === 'name') return dir * title(a).localeCompare(title(b));
      if (sortBy === 'pages') return dir * ((a.page_count ?? 0) - (b.page_count ?? 0));
      if (sortBy === 'acc') return dir * ((accuracyOf(a) ?? -1) - (accuracyOf(b) ?? -1));
      if (sortBy === 'used') return dir * ((a.used_count ?? 0) - (b.used_count ?? 0));
      if (sortBy === 'oldest') return t(a) - t(b);
      return t(b) - t(a); // newest
    });
    return list;
  });
  function setSort(col: 'name' | 'pages' | 'used' | 'acc') {
    if (sortBy === col) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    else { sortBy = col; sortDir = col === 'name' ? 'asc' : 'desc'; }
  }
  function sortArrow(col: string): string {
    if (sortBy !== col) return '';
    return sortDir === 'asc' ? ' ↑' : ' ↓';
  }

  // ── folder summary strip ──
  let folderStats = $derived.by(() => {
    const n = docs.length;
    const pages = docs.reduce((s, d) => s + (d.page_count ?? 0), 0);
    const langs = new Set(docs.map((d) => langOf(d)));
    const read = docs.reduce((s, d) => s + (d.vision_pages ?? 0), 0);
    const cov = pages > 0 ? Math.round((read / pages) * 100) : 0;
    return { n, pages, langs: [...langs].join(' · ') || '—', cov };
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
  function byUser(d: Doc): string {
    const u = d.uploaded_by || '';
    return u ? u.split('@')[0] : '—';     // username only — full email on hover title
  }
  function accuracyOf(d: Doc): number | null {
    // "Read coverage" = fraction of pages vision-read. On a fresh / lite doc this
    // is 0 → show "—" instead of an alarming 0%. Only report once some pages read.
    const pc = d.page_count ?? 0;
    const vp = d.vision_pages ?? 0;
    if (!pc || vp <= 0) return null;
    return Math.round((vp / pc) * 100);
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
      await api.createFolder(n, { access_mode: accessMode, principals, parent_id: newParent });
      showFolderModal = false;
      newParent = null;
      await loadFolders();
    } catch {} finally { creating = false; }
  }

  function pickFiles() { fileInput?.click(); }
  async function uploadFiles(files: File[] | FileList) {
    const arr = Array.from(files);
    if (!arr.length) return;
    uploading = true;
    const errs: string[] = [];
    let okCount = 0;
    try {
      const fid = typeof selected === 'number' ? selected : null;
      for (const f of arr) {
        try { await api.uploadTo(f, fid); okCount++; }
        catch (e: any) { errs.push(e?.message || `${f.name}: upload failed`); }
      }
      await Promise.all([loadFolders(), loadDocs()]);
    } finally {
      uploading = false;
    }
    if (errs.length) flashToast(errs.join('  ·  '), 'err');
    else if (okCount) flashToast(`${okCount} file${okCount > 1 ? 's' : ''} uploaded`, 'ok');
  }
  async function onFilesPicked(e: Event) {
    const input = e.target as HTMLInputElement;
    const files = input.files;
    if (!files || !files.length) return;
    // directory picker drags in everything → keep only ingestable types
    const ok = /\.(pdf|png|jpe?g)$/i;
    const arr = Array.from(files).filter((f) => ok.test(f.name));
    if (!arr.length) { flashToast('No PDF/PNG/JPG files in that folder', 'err'); input.value = ''; return; }
    await uploadFiles(arr);
    input.value = '';
  }
  // ── drag-drop dropzone ──
  function onDragOver(e: DragEvent) { if (selected === 'all') return; e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; dragOver = true; }
  function onDragLeave(e: DragEvent) { if (e.currentTarget === e.target) dragOver = false; }
  async function onDrop(e: DragEvent) {
    if (selected === 'all') return;            // All-view = read-only aggregate; pick a folder to upload
    e.preventDefault(); dragOver = false;
    const files = e.dataTransfer?.files;
    if (files && files.length) await uploadFiles(files);
  }

  // ── ask this doc in chat ──
  function askDoc(d: Doc, e?: Event) {
    e?.stopPropagation();
    goto(`/?ask=${encodeURIComponent('About "' + title(d) + '": ')}`);
  }

  // ── bulk selection ──
  function toggleSel(id: number, e?: Event) {
    e?.stopPropagation();
    const s = new Set(selIds);
    s.has(id) ? s.delete(id) : s.add(id);
    selIds = s;
  }
  function allVisibleSelected(): boolean {
    return visibleDocs.length > 0 && visibleDocs.every((d) => selIds.has(d.id));
  }
  function toggleSelAll(e?: Event) {
    e?.stopPropagation();
    if (allVisibleSelected()) selIds = new Set();
    else selIds = new Set(visibleDocs.map((d) => d.id));
  }
  function clearSel() { selIds = new Set(); bulkMoveOpen = false; }
  function bulkDelete() {
    if (!selIds.size || bulkBusy) return;
    openDel({ mode: 'bulk', n: selIds.size });
  }
  async function performBulkDelete() {
    bulkBusy = true;
    const ids = [...selIds]; let ok = 0; const errs: string[] = [];
    try {
      for (const id of ids) {
        try { await api.deleteDoc(id); ok++; } catch (e: any) { errs.push(e?.message || `#${id} failed`); }
      }
      await Promise.all([loadFolders(), loadDocs()]);
    } finally { bulkBusy = false; clearSel(); }
    if (errs.length) flashToast(`${ok} deleted · ${errs.length} failed: ${errs[0]}`, 'err');
    else flashToast(`${ok} document${ok > 1 ? 's' : ''} deleted`, 'ok');
  }
  async function bulkMove(folderId: number | null) {
    if (!selIds.size || bulkBusy) return;
    bulkBusy = true; bulkMoveOpen = false;
    const ids = [...selIds]; let ok = 0;
    try {
      for (const id of ids) { try { await api.moveDoc(id, folderId); ok++; } catch {} }
      await Promise.all([loadFolders(), loadDocs()]);
    } finally { bulkBusy = false; clearSel(); }
    flashToast(`${ok} document${ok > 1 ? 's' : ''} moved`, 'ok');
  }
  async function bulkRetag() {
    if (bulkBusy) return; bulkBusy = true;
    try { await api.categorizeAll(false); await loadDocs(); } catch {} finally { bulkBusy = false; clearSel(); }
    flashToast('Re-tagged selection', 'ok');
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
    if (s === 'ready_lite') return 'p-lite';     // answerable, still enriching
    if (s === 'failed' || s === 'cancelled') return 'p-fail';
    if (s === 'processing') return 'p-proc';
    return 'p-queued';
  }
  function pillLabel(s: string, d: Doc): string {
    if (s === 'ready_lite') return 'Ready · enriching…';
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
  let procLive = $derived(procStatus === 'queued' || procStatus === 'processing' || procStatus === 'ready_lite');

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
    // manage the 2s poll based on live status (ready_lite = phase-2 enriching)
    const live = proc && ['queued', 'processing', 'ready_lite'].includes((proc.doc?.status || '').toLowerCase());
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
  function doDelete() {
    if (panelDocId == null) return;
    openDel({ mode: 'one', id: panelDocId, doc: proc?.doc || {} });
  }
  async function performDelete(id: number) {
    actionBusy = true;
    try {
      await api.deleteDoc(id);
      closePanel();
      await Promise.all([loadFolders(), loadDocs()]);
    } catch (e: any) { flashToast(e?.message || 'Delete failed', 'err'); } finally { actionBusy = false; }
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

    <div class="grp">Shared folders</div>
    {#snippet folderNode(f, depth)}
      <button class="fitem" class:on={selected === f.id} title={f.name}
        style="padding-left:{6 + depth * 13}px" onclick={() => (selected = f.id)}>
        {#if kids(f.id).length}
          <span class="chev" role="button" tabindex="0" aria-label="Expand {f.name}"
            onclick={(e) => toggleExpand(f, e)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleExpand(f, e); } }}
          >{f.is_expanded ? '▾' : '▸'}</span>
        {:else}<span class="chev sp"></span>{/if}
        <span class="ic">📁</span>
        <span class="fname">{f.name}</span>
        <span class="fmeta">
          {#if f.access_mode === 'org'}
            <span class="lk" title="Shared with the whole organisation">🌐</span>
          {:else if f.access_mode === 'specific'}
            <span class="lk" title="Shared with specific people / groups">🔒{f.access_n ?? 0}</span>
          {/if}
          <span class="sh add" role="button" tabindex="0" title="New subfolder"
            aria-label="New subfolder in {f.name}"
            onclick={(e) => openSubfolder(f.id, e)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openSubfolder(f.id); } }}
          >＋</span>
          <span class="sh" role="button" tabindex="0" title="Share / manage access"
            aria-label="Share folder {f.name}"
            onclick={(e) => openShareModal(f, e)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openShareModal(f); } }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle>
              <line x1="8.6" y1="13.5" x2="15.4" y2="17.5"></line><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"></line>
            </svg>
          </span>
          <span class="sh del" role="button" tabindex="0" title="Delete folder"
            aria-label="Delete folder {f.name}"
            onclick={(e) => removeFolder(f, e)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); removeFolder(f); } }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
            </svg>
          </span>
          <span class="n">{f.doc_count}</span>
        </span>
      </button>
      {#if f.is_expanded}
        {#each kids(f.id) as c (c.id)}
          {@render folderNode(c, depth + 1)}
        {/each}
      {/if}
    {/snippet}

    {#if folders.length === 0}
      <div class="rail-empty">No folders yet.</div>
    {:else}
      {#each topFolders as f (f.id)}
        {@render folderNode(f, 0)}
      {/each}
    {/if}

    <button class="newf" onclick={openFolderModal}>
      <span class="ic">＋</span><span class="fname">New folder</span>
    </button>
    <div class="spacer"></div>
    <div class="railfoot">{total} {total === 1 ? 'doc' : 'docs'}{inFlight ? ' · processing…' : ''}</div>
  </aside>

  <!-- ===== CENTER — doc list ===== -->
  <main class="center" ondragover={onDragOver} ondragleave={onDragLeave} ondrop={onDrop} role="region" aria-label="Documents">
    {#if dragOver}
      <div class="dropmask">
        <div class="dropcard">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 9 12 4 17 9"/><line x1="12" y1="4" x2="12" y2="16"/></svg>
          <div class="dropttl">Drop to upload</div>
          <div class="dropsub">into {breadcrumb}</div>
        </div>
      </div>
    {/if}
    <div class="chead">
      <h1>{breadcrumb}</h1>
      <small>{total} {total === 1 ? 'document' : 'documents'}</small>
      {#if selected !== 'all'}
      <div class="addwrap">
        <button class="addbtn" onclick={toggleAdd} disabled={uploading} aria-haspopup="menu" aria-expanded={addOpen}>
          {uploading ? 'Working…' : '＋ Add'}<span class="caret">▾</span>
        </button>
        {#if addOpen}
          <button class="addback" aria-label="Close menu" onclick={() => (addOpen = false)}></button>
          <div class="addmenu" role="menu">
            <button class="ai" role="menuitem" onclick={() => { addOpen = false; pickFiles(); }}>
              <span class="aic">⬆</span>
              <span class="atx"><b>Upload files</b><i>one or many · PDF, PNG, JPG</i></span>
            </button>
            <button class="ai" role="menuitem" onclick={pickDir}>
              <span class="aic">▱</span>
              <span class="atx"><b>Upload directory</b><i>whole folder · subfolders</i></span>
            </button>
            <button class="ai" role="menuitem" onclick={importServer}>
              <span class="aic">⤓</span>
              <span class="atx"><b>Import from server</b><i>{serverScan ? `${serverScan.new} new · ${serverScan.found} in folder` : 'scanning…'}</i></span>
            </button>
            {#if s3Scan?.configured}
              <button class="ai" role="menuitem" onclick={importS3}>
                <span class="aic">☁</span>
                <span class="atx"><b>Import from S3</b><i>{`${s3Scan.new} new · ${s3Scan.found} in bucket`}</i></span>
              </button>
            {/if}
            <div class="adiv"></div>
            <button class="ai" role="menuitem" onclick={() => importGraph('sharepoint')}>
              <span class="aic">▦</span>
              <span class="atx"><b>Import from SharePoint</b><i>Microsoft 365 document library</i></span>
            </button>
            <button class="ai" role="menuitem" onclick={() => importGraph('onedrive')}>
              <span class="aic">☁</span>
              <span class="atx"><b>Import from OneDrive</b><i>A user’s OneDrive files</i></span>
            </button>
          </div>
        {/if}
      </div>
      {/if}
      <input bind:this={fileInput} type="file" multiple class="hidden-file"
        accept=".pdf,.png,.jpg,.jpeg" onchange={onFilesPicked} />
      <input bind:this={dirInput} type="file" multiple webkitdirectory class="hidden-file"
        onchange={onDirPicked} />
    </div>
    {#if uploadToast}
      <div class="uptoast {uploadToastKind}" role="alert">
        <span>{uploadToast}</span>
        <button class="uptoast-x" onclick={() => (uploadToast = '')} aria-label="Dismiss">✕</button>
      </div>
    {/if}

    {#if loadingDocs && docs.length === 0}
      <div class="empty">Loading documents…</div>
    {:else if docs.length === 0 && selected === 'all'}
      <div class="empty">No documents yet. Pick a folder and use <b>＋ Add</b> to upload.</div>
    {:else if docs.length === 0}
      <div class="dropzone" role="button" tabindex="0" onclick={pickFiles}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFiles(); } }}>
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 9 12 4 17 9"/><line x1="12" y1="4" x2="12" y2="16"/></svg>
        <div class="dz-ttl">Drop PDFs here, or click to upload</div>
        <div class="dz-sub">They’ll train this folder automatically — answerable in seconds.</div>
      </div>
    {:else}
      <!-- ===== folder summary strip ===== -->
      <div class="sumstrip">
        <span><b>{folderStats.n}</b> docs</span><i></i>
        <span><b>{folderStats.pages}</b> pages</span><i></i>
        <span>{folderStats.langs}</span><i></i>
        <span><b>{folderStats.cov}%</b> read</span>
        {#if inFlight}<i></i><span class="enr"><span class="dot l"></span> enriching…</span>{/if}
      </div>
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
        <span class="tb-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input placeholder="Search documents…" bind:value={query} />
          {#if query}<button class="tb-clear" title="Clear" onclick={() => (query = '')}>✕</button>{/if}
        </span>
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
        <button class="jobs" class:active={jobsCount > 0} onclick={() => (jobsOpen = !jobsOpen)} aria-expanded={jobsOpen}>
          {#if jobsCount > 0}<span class="spin"></span>{/if}
          Jobs · {jobsCount} active
        </button>
        <span class="tb-sort">
          Sort:
          <select bind:value={sortBy}>
            <option value="newest">Newest</option>
            <option value="oldest">Oldest</option>
            <option value="used">Most-used</option>
          </select>
        </span>
      </div>

      {#if selIds.size > 0}
        <div class="bulkbar">
          <span class="bb-n">{selIds.size} selected</span>
          <div class="bb-move">
            <button class="bb-btn" disabled={bulkBusy} onclick={() => (bulkMoveOpen = !bulkMoveOpen)}>Move ▾</button>
            {#if bulkMoveOpen}
              <div class="bb-pop">
                {#each folders as f (f.id)}
                  <button onclick={() => bulkMove(f.id)}>{f.name}</button>
                {/each}
              </div>
            {/if}
          </div>
          <button class="bb-btn" disabled={bulkBusy} onclick={bulkRetag}>Re-tag</button>
          <button class="bb-btn danger" disabled={bulkBusy} onclick={bulkDelete}>Delete</button>
          <button class="bb-clear" onclick={clearSel}>Clear</button>
        </div>
      {/if}

      {#if visibleDocs.length === 0}
        <div class="empty">No documents in this category.</div>
      {:else if viewMode === 'list'}
        <!-- ===== LIST view ===== -->
        <div class="list">
          {#each groups as g (g.label)}
            <div class="glbl">{g.label} · {g.docs.length}</div>
            <div class="lh">
              <div><input class="cbx" type="checkbox" checked={allVisibleSelected()} onclick={toggleSelAll} aria-label="Select all" /></div>
              <button class="sh-col" onclick={() => setSort('name')}>Title{sortArrow('name')}</button>
              <div>Lang</div>
              <button class="sh-col" onclick={() => setSort('pages')}>Pages{sortArrow('pages')}</button>
              <button class="sh-col" onclick={() => setSort('used')}>Used{sortArrow('used')}</button>
              <button class="sh-col" onclick={() => setSort('acc')}>Accuracy{sortArrow('acc')}</button>
              <div>By</div>
            </div>
            {#each g.docs as d (d.id)}
              {@const st = statusOf(d)}
              {@const acc = accuracyOf(d)}
              <div class="lr" class:sel={panelDocId === d.id} class:checked={selIds.has(d.id)} role="button" tabindex="0"
                onclick={() => openPanel(d)}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPanel(d); } }}>
                <div class="cbcell">
                  <input class="cbx" type="checkbox" checked={selIds.has(d.id)} onclick={(e) => toggleSel(d.id, e)} aria-label="Select {title(d)}" />
                </div>
                <div class="ti">
                  {#if d.cover_page_id}
                    <img class="thumb" src="/api/pages/{d.cover_page_id}" alt="" loading="lazy" />
                  {:else}
                    <div class="ic" class:my={langOf(d) === 'MY'}></div>
                  {/if}
                  <div class="tt">
                    <div class="nm">{title(d)}</div>
                    {#if st === 'processing' || st === 'queued'}
                      <div class="s">
                        <span class="dot b"></span>
                        {#if d.page_count}Processing · reading {d.pages_done ?? 0}/{d.page_count}{:else}Queued…{/if}
                      </div>
                      <div class="pb"><i style="width:{d.progress ?? 0}%"></i></div>
                    {:else if st === 'ready_lite'}
                      <div class="s">
                        <span class="badge">{(d.doc_type || 'SOP').toUpperCase()}</span>
                        {#if subLine(d)}{subLine(d)} · {/if}
                        <span class="dot l"></span> Ready · enriching {d.pages_done ?? 0}/{d.page_count ?? '—'}
                      </div>
                      <div class="pb"><i class="lite" style="width:{d.progress ?? 0}%"></i></div>
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
                <div>{#if acc !== null}<span class="acc"><span class="abar"><i style="width:{acc}%"></i></span>{acc}%</span>{:else}<span class="muted">—</span>{/if}</div>
                <div class="muted" title={d.uploaded_by ?? ''}>{byUser(d)}</div>
                <div class="row-actions">
                  <button class="ra" title="Ask in chat" onclick={(e) => askDoc(d, e)}>Ask</button>
                  <button class="ra" title="Open / inspect" onclick={(e) => { e.stopPropagation(); openPanel(d); }}>Open</button>
                </div>
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
                    {:else if st === 'ready_lite'}
                      <span class="dot l"></span> Ready · enriching…
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
          {proc.doc.status === 'ready_lite' ? 'Ready · enriching' : (proc.doc.status || 'ready')} · {proc.doc.page_count ?? '—'} pages ·
          {(folders.find((f) => f.id === proc.doc.folder_id)?.name) ?? 'No folder'} ·
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

<!-- ===== JOBS SLIDE-OVER (right side) ===== -->
<div class="proc-scrim" class:open={jobsOpen} role="button" tabindex="-1" aria-label="Close jobs"
  onclick={() => (jobsOpen = false)} onkeydown={(e) => { if (e.key === 'Escape') jobsOpen = false; }}
  style="position:fixed;inset:0;z-index:60;border:none;background:rgba(20,18,16,.16);display:{jobsOpen ? 'block' : 'none'}"></div>
<aside class="proc jobspanel" class:open={jobsOpen} aria-hidden={!jobsOpen}
  style="position:fixed;top:0;right:0;width:420px;max-width:95vw;height:100vh;background:#fff;border-left:1px solid var(--line);z-index:61;display:flex;flex-direction:column;transition:transform .22s ease;transform:translateX({jobsOpen ? '0' : '100%'})">
  {#if jobsOpen}
    <div class="ph">
      <div class="ph-l">
        <div class="ph-t">{agent?.enabled ? 'Enrichment Agent' : 'Active jobs'}</div>
        <div class="ph-s">
          {#if agent?.enabled}
            <span class="adot" class:paused={agent.paused}></span>
            {agent.paused ? 'Paused' : 'Running'} · {agent.active} enriching · {agent.queued_count} queued
          {:else}
            {jobsCount} processing
          {/if}
        </div>
      </div>
      <div class="ph-r">
        {#if activeJobs.length > 0}
          <button class="stopall" disabled={stopAllBusy} onclick={stopAllJobs}>{stopAllBusy ? 'Stopping…' : '⏹ Stop all'}</button>
        {/if}
        <button class="ph-x" onclick={() => (jobsOpen = false)} aria-label="Close">✕</button>
      </div>
    </div>
    <div class="jbody">
      {#if agent?.enabled}
        <!-- agent controls -->
        <div class="actrls">
          <button class="abtn" class:pause={!agent.paused} disabled={agentBusy} onclick={toggleAgent}>
            {agent.paused ? '▶ Resume agent' : '⏸ Pause agent'}
          </button>
          <span class="aknob">Parallel
            <input type="number" min="1" max="8" value={agent.concurrency}
              onchange={(e) => setConcurrency(+(e.currentTarget as HTMLInputElement).value)} />
          </span>
        </div>
      {/if}

      {#if activeJobs.length === 0}
        <div class="jempty">✓ All caught up — nothing processing right now.</div>
      {:else}
        {#if agent?.enabled}<div class="jsec">Working now</div>{/if}
        {#each activeJobs as d (d.id)}
          {@const lite = (d.status || '').toLowerCase() === 'ready_lite'}
          <div class="jrow" role="button" tabindex="0" onclick={() => openJob(d)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openJob(d); } }}>
            <div class="jr-top">
              <span class="jr-name" title={d.name}>📄 {parseDocName(d.name).title || d.name}</span>
              <span class="jr-pct">{jobPct(d)}%</span>
              {#if agent?.enabled && lite}
                <button class="jr-stop" onclick={(e) => skipEnrich(d, e)} title="Accept as-is, stop enriching">Skip</button>
              {:else}
                <button class="jr-stop" disabled={stoppingIds.has(d.id)} onclick={(e) => stopJob(d, e)} title="Stop this job">
                  {stoppingIds.has(d.id) ? 'Stopping…' : '⏹ Stop'}
                </button>
              {/if}
            </div>
            <div class="jr-bar"><i class:lite style="width:{jobPct(d)}%"></i></div>
            <div class="jr-sub">{lite && agent?.enabled ? 'answerable now · enriching in background' : jobStage(d)}{d.page_count ? ` · ${d.page_count} pages` : ''}</div>
          </div>
        {/each}
      {/if}

      {#if agent?.recent?.length}
        <div class="jsec">Recently enriched</div>
        {#each agent.recent.slice(0, 6) as r}
          <div class="jdone"><span class="jd-i" class:fail={!r.ok}>{r.ok ? '✓' : '⚠'}</span> {parseDocName(r.name).title || r.name}</div>
        {/each}
      {/if}

      <div class="jhint">
        {#if agent?.enabled}
          Upload is instant — docs are answerable immediately and the agent enriches them in the background. Survives restarts via cache.
        {:else}
          Tap a job to open its full processing panel. Updates live.
        {/if}
      </div>
    </div>
  {/if}
</aside>

<!-- ===== DELETE CONFIRM MODAL (double-confirm) ===== -->
{#if delReq}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={closeDel} onkeydown={(e) => { if (e.key === 'Escape') closeDel(); }}></div>
  <div class="delmodal" role="dialog" aria-modal="true" aria-label="Delete confirmation">
    <div class="del-head">
      <span class="del-ttl">{delReq.mode === 'folder' ? 'Delete folder?' : delReq.mode === 'bulk' ? `Delete ${delReq.n} document${delReq.n > 1 ? 's' : ''}?` : 'Delete document?'}</span>
      <button class="del-x" onclick={closeDel} aria-label="Close">✕</button>
    </div>
    <div class="del-warn">{#if delReq.mode === 'folder'}⚠ Deletes the folder. Its documents move to All documents (un-filed) — they are NOT deleted. Cannot be undone.{:else}⚠ Permanently removes {delReq.mode === 'bulk' ? 'these documents' : 'this document'} and all derived data. Cannot be undone.{/if}</div>

    {#if delReq.mode === 'folder'}
      <div class="del-card">
        <div class="del-name">📁 {delReq.folder.name}</div>
        <div class="del-meta">{delReq.folder.doc_count ?? 0} document{(delReq.folder.doc_count ?? 0) === 1 ? '' : 's'} will be un-filed (kept, moved to All documents)</div>
      </div>
    {:else if delReq.mode === 'one'}
      <div class="del-card">
        <div class="del-name">📄 {delReq.doc?.name || 'document'}</div>
        <div class="del-meta">
          {[
            (delReq.doc?.page_count ?? delReq.doc?.pages) ? `${delReq.doc.page_count ?? delReq.doc.pages} pages` : null,
            (folders.find((f) => f.id === delReq.doc?.folder_id)?.name) || null,
            (delReq.doc?.lang || '').toUpperCase() || null,
            delReq.doc?.uploaded_by || null,
            (delReq.doc?.used_count ?? 0) > 0 ? `used ${delReq.doc.used_count}×` : null,
          ].filter(Boolean).join(' · ')}
        </div>
      </div>
    {:else}
      <div class="del-card"><div class="del-name">{delReq.n} selected document{delReq.n > 1 ? 's' : ''}</div></div>
    {/if}

    {#if delReq.mode !== 'folder'}<div class="del-also">Also deleted: extracted pages, Q&amp;A pairs, wiki claims, citations.</div>{/if}

    <label class="del-lab">
      {#if delReq.mode === 'bulk'}Type <b>DELETE</b> to confirm:{:else if delReq.mode === 'folder'}Type the folder name <b>{delReq.folder.name}</b> to confirm:{:else}Type the document name to confirm:{/if}
    </label>
    <input class="del-in" bind:value={delInput}
      placeholder={delReq.mode === 'bulk' ? 'DELETE' : delReq.mode === 'folder' ? delReq.folder.name : (delReq.doc?.name || '')}
      autocomplete="off" spellcheck="false"
      onkeydown={(e) => { if (e.key === 'Enter' && delArmed) confirmDelete(); }} />

    <div class="del-acts">
      <button class="del-cancel" onclick={closeDel}>Cancel</button>
      <button class="del-go" disabled={!delArmed || bulkBusy || actionBusy} onclick={confirmDelete}>{delReq.mode === 'folder' ? 'Delete folder' : 'Delete forever'}</button>
    </div>
  </div>
{/if}

<!-- ===== DIRECTORY-IMPORT PREVIEW ===== -->
{#if dirImportOpen}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={() => { if (!dirBusy) dirImportOpen = false; }}
    onkeydown={(e) => { if (e.key === 'Escape' && !dirBusy) dirImportOpen = false; }}></div>
  <div class="modal wide" role="dialog" aria-modal="true" aria-label="Import folder">
    <h2>Import folder</h2>
    <div class="imp-sel"><b>{dirRootName}/</b> · {dirFiles.length} file{dirFiles.length === 1 ? '' : 's'} · {dirTree.length} folder{dirTree.length === 1 ? '' : 's'}</div>

    <label class="row">
      <span class="lab">Under</span>
      <select class="tin" bind:value={dirUnderId} disabled={dirBusy}>
        <option value={null}>Top level</option>
        {#each folders as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
      </select>
    </label>

    <div class="qa">Folders auto-created from the paths</div>
    <div class="imp-tree">
      {#each dirTree as n (n.path)}
        <div class="imp-row" style="padding-left:{(n.path.split('/').length - 1) * 16}px">
          <span class="ic">📁</span>
          <span class="imp-name">{n.name}</span>
          {#if n.files}<span class="imp-fc">{n.files} file{n.files === 1 ? '' : 's'}</span>{/if}
          <span class="imp-badge {n.exists ? 'ex' : 'new'}">{n.exists ? '✓ exists' : '● new'}</span>
        </div>
      {/each}
    </div>

    <div class="imp-warn">⚠ {dirFiles.length} file{dirFiles.length === 1 ? '' : 's'} will be queued for ingest — each is a vision + compile pass (real cost).</div>
    {#if dirBusy && dirProgress}<div class="imp-prog">{dirProgress}</div>{/if}

    <div class="actions">
      <button class="btn-cancel" onclick={() => { if (!dirBusy) dirImportOpen = false; }} disabled={dirBusy}>Cancel</button>
      <button class="btn-create" onclick={runDirImport} disabled={dirBusy}>
        {dirBusy ? 'Importing…' : `Create ${dirNewCount} folder${dirNewCount === 1 ? '' : 's'} · Import`}
      </button>
    </div>
  </div>
{/if}

<!-- ===== CREATE-FOLDER MODAL ===== -->
{#if showFolderModal}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={closeFolderModal} onkeydown={(e) => { if (e.key === 'Escape') closeFolderModal(); }}></div>
  <div class="modal" role="dialog" aria-modal="true" aria-label="New folder"
    onkeydown={(e) => { if (e.key === 'Escape') closeFolderModal(); }}>
    <h2>{newParent ? 'New subfolder' : 'New folder'}</h2>
    {#if newParent}
      <div class="parenthint">Inside <b>{nameById(newParent)}</b></div>
    {/if}
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
  /* SCOPED to .src-shell — NOT :global(:root). These used to leak globally and
     recolour the whole app beige whenever Sources loaded (Workspace stayed white).
     Structural tokens now match app.css (white claude theme) so Sources == the rest
     of the app; only the accent palette is local. */
  /* these tokens are NOT global (app.css :root lacks --line/--coral/--green/--red/etc).
     The slide-over panel + modals render OUTSIDE .src-shell, so they MUST carry the same
     token block or their text/borders/bars collapse to white. Keep all three in sync. */
  .src-shell, .proc, .modal, .delmodal {
    --cream: #ffffff; --paper: #ffffff; --sand: #f9f9f8; --ink: #1a1a18; --muted: #8b8b85; --line: #e0dfda;
    --coral: var(--brand, #d97757); --blue: #3f7fb0; --teal: #2f8f83; --violet: #7b6bd6; --amber: #c98a2e; --green: #3f8f5f; --red: #c0492f;
  }

  /* ===== 3-pane shell ===== */
  .src-shell { height: 100%; display: flex; min-height: 0; background: var(--cream); }

  /* === LEFT folder rail (persistent) === */
  /* rail matched to Workspace (.wsrail) — same width, padding, neutral pill active state */
  .rail { width: 210px; flex: none; background: var(--sand); border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 1px; padding: 16px 11px; overflow: auto; }
  .rail .grp { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; padding: 14px 8px 5px; }
  .rail .grp:first-child { padding-top: 2px; }
  .fitem { display: flex; align-items: center; gap: 9px; padding: 8px 12px; border-radius: 9px; font-size: 13.5px; font-weight: 500; color: #46443f; cursor: pointer; border: none; background: transparent; width: 100%; text-align: left; font: inherit; transition: background .14s, color .14s; }
  .fitem:hover { background: var(--hover, #efefec); }
  .fitem.on { background: var(--navpill); color: var(--ink); font-weight: 600; }
  .fitem .ic { width: 16px; text-align: center; flex: none; }
  .fitem .fname { flex: 1; min-width: 0; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* right cluster — lock + count always pinned right; share reveals on hover/active
     so the folder NAME gets the space the rest of the time */
  .fitem .fmeta { flex: none; display: flex; align-items: center; gap: 4px; margin-left: 6px; }
  .fitem .n { font-size: 11px; font-weight: 700; flex: none; color: var(--muted); background: var(--cream, #faf9f5); border: 1px solid var(--border); border-radius: 6px; padding: 0 6px; min-width: 16px; text-align: center; line-height: 17px; }
  .fitem.on .n { color: var(--ink); }
  .fitem .lk { font-size: 10px; color: var(--amber); flex: none; }
  .fitem .sh { flex: none; display: inline-flex; align-items: center; justify-content: center;
    width: 0; opacity: 0; overflow: hidden; height: 20px; border-radius: 6px;
    color: var(--muted); cursor: pointer; transition: width .12s, opacity .12s; }
  /* reveal row actions on HOVER only — a selected row keeps its full name
     visible (3 action buttons + count would otherwise squeeze a nested name to 0) */
  .fitem:hover .sh { width: 20px; opacity: .7; }
  .fitem .fname { min-width: 34px; }   /* never let the name collapse to nothing */
  .fitem .sh:hover { background: var(--hover, #efefec); color: var(--ink); opacity: 1; }
  .fitem .sh.del:hover { color: var(--red, #c0492f); }
  .fitem .sh.add { font-size: 15px; font-weight: 400; line-height: 1; }
  /* tree expand toggle — always visible; spacer keeps leaf rows aligned */
  .fitem .chev { flex: none; width: 14px; text-align: center; font-size: 11px; line-height: 1;
    color: var(--muted); cursor: pointer; user-select: none; }
  .fitem .chev.sp { cursor: default; }
  .fitem .chev:hover { color: var(--ink); }
  .rail-empty { padding: 8px 11px; color: var(--muted); font-size: 12px; }
  /* subfolder-parent hint in the create modal */
  .parenthint { font-size: 12px; color: var(--muted); margin: -6px 0 14px; }
  .parenthint b { color: var(--ink); font-weight: 600; }
  /* directory-import preview */
  .modal.wide { width: min(560px, calc(100vw - 32px)); }
  .imp-sel { font-size: 13px; color: var(--ink); margin: -4px 0 16px; }
  .imp-sel b { color: var(--coral); }
  .imp-tree { max-height: 260px; overflow: auto; border: 1px solid var(--line); border-radius: 10px;
    background: var(--sand); padding: 9px 11px; margin: 4px 0 14px; }
  .imp-row { display: flex; align-items: center; gap: 7px; font-size: 12.5px; padding: 3px 0; color: var(--ink); }
  .imp-row .ic { flex: none; }
  .imp-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .imp-fc { font-size: 10.5px; color: var(--muted); margin-left: 6px; flex: none; }
  .imp-badge { margin-left: auto; flex: none; font-size: 10px; font-weight: 600; padding: 1px 8px; border-radius: 20px; }
  .imp-badge.new { color: var(--amber); background: color-mix(in srgb, var(--amber) 14%, transparent); }
  .imp-badge.ex { color: var(--green); background: color-mix(in srgb, var(--green) 14%, transparent); }
  .imp-warn { font-size: 12px; color: var(--amber); background: color-mix(in srgb, var(--amber) 12%, transparent);
    padding: 8px 11px; border-radius: 8px; margin-bottom: 12px; }
  .imp-prog { font-size: 12px; color: var(--muted); margin-bottom: 10px; font-family: ui-monospace, monospace; }
  /* small ghost row directly under folders (matches .fitem height, no dashed box) */
  .newf { display: flex; align-items: center; gap: 9px; width: 100%; margin-top: 2px; padding: 8px 12px; border: none; background: transparent; border-radius: 9px; font: inherit; font-size: 13px; color: var(--muted); cursor: pointer; text-align: left; transition: background .14s, color .14s; }
  .newf:hover { background: var(--hover, #efefec); color: var(--ink); }
  .newf .ic { width: 16px; text-align: center; flex: none; font-size: 14px; }
  .rail .spacer { flex: 1; }
  .railfoot { margin-top: auto; font-size: 11.5px; color: var(--muted); padding: 10px 8px 2px; }

  /* === CENTER doc list === */
  .center { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--cream); overflow-y: auto; overflow-x: hidden; position: relative; }
  .chead { display: flex; align-items: center; gap: 6px; padding: 16px 22px 10px; }
  .chead h1 { margin: 0; font-size: 18px; }
  .chead small { color: var(--muted); font-weight: 400; margin-left: 4px; font-size: 13px; }
  .up { margin-left: auto; background: var(--ink); color: #fff; border: none; border-radius: 9px; padding: 8px 13px; font-weight: 600; font-size: 13px; cursor: pointer; }
  .up:disabled { opacity: .55; cursor: default; }

  /* === Add ▾ dropdown === */
  .addwrap { margin-left: auto; position: relative; }
  .addbtn { display: inline-flex; align-items: center; gap: 6px; background: var(--coral, var(--brand)); color: #fff; border: none; border-radius: 9px; padding: 8px 14px; font-weight: 600; font-size: 13px; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,.08); }
  .addbtn:disabled { opacity: .6; cursor: default; }
  .addbtn .caret { font-size: 10px; opacity: .9; }
  .addback { position: fixed; inset: 0; z-index: 40; background: transparent; border: none; cursor: default; }
  .addmenu { position: absolute; top: calc(100% + 6px); right: 0; z-index: 41; width: 290px; background: #fff; border: 1px solid #e9e6dd; border-radius: 14px; box-shadow: 0 14px 40px rgba(0,0,0,.16); padding: 6px; }
  .ai { display: flex; align-items: flex-start; gap: 11px; width: 100%; text-align: left; background: none; border: none; border-radius: 9px; padding: 9px 11px; cursor: pointer; }
  .ai:hover { background: #f6f4ee; }
  .aic { flex: none; width: 26px; height: 26px; display: grid; place-items: center; border-radius: 7px; background: #f3efe6; color: var(--coral, var(--brand)); font-size: 15px; margin-top: 1px; }
  .atx { display: flex; flex-direction: column; min-width: 0; }
  .atx b { font-size: 13.5px; font-weight: 600; color: #1f1e1d; line-height: 1.3; }
  .atx i { font-style: normal; font-size: 11.5px; color: #8a857c; margin-top: 1px; }
  .adiv { height: 1px; background: #ede9e0; margin: 5px 8px; }
  .hidden-file { display: none; }
  .uptoast { display: flex; align-items: center; gap: 10px; margin: 0 22px 4px; padding: 9px 13px;
    border-radius: 10px; font-size: 13px; font-weight: 500; animation: uptoast-in .18s ease; }
  .uptoast.err { background: #fdecea; color: #9b2c20; border: 1px solid #f3c4bd; }
  .uptoast.ok { background: #e9f6ee; color: #1d6b3a; border: 1px solid #bfe2cb; }
  .uptoast span { flex: 1; }
  .uptoast-x { background: none; border: none; cursor: pointer; color: inherit; opacity: .6; font-size: 13px; }
  .uptoast-x:hover { opacity: 1; }
  @keyframes uptoast-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  .list { margin: 0 16px 20px; background: #fff; border: 1px solid var(--line); border-radius: 14px; overflow-x: auto; }
  /* keep all columns readable; if the viewport is genuinely too narrow the table
     scrolls inside its own card instead of bleeding off-screen */
  .list .lh, .list .lr { min-width: 560px; }
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
  .p-lite { background: #eaf6ef; color: #2f8f6a; }
  .p-fail { background: #f7e7e3; color: var(--red); }
  .p-queued { background: #fbf3e6; color: var(--amber); }

  .empty { padding: 40px 16px; text-align: center; color: var(--muted); font-size: 13px; }

  /* coral token alias (mockup uses --coral; app token is --clay = coral) */
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
  .jobs { border: 1px solid var(--line); background: #fff; color: var(--muted); border-radius: 8px; padding: 5px 10px; display: flex; gap: 6px; align-items: center; font: inherit; font-size: 12.5px; cursor: pointer; }
  .jobs:hover { background: var(--hover, #f4f3f0); }
  .jobs.active { border-color: #eccdbd; color: var(--coral); }

  /* jobs slide-over (reuses .proc chrome) */
  .jobspanel .ph { display: flex; align-items: flex-start; justify-content: space-between; }
  .jobspanel .ph-t { font-size: 16px; font-weight: 650; color: var(--ink); }
  .jobspanel .ph-s { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .jobspanel .ph-r { display: flex; align-items: center; gap: 8px; flex: none; }
  .jobspanel .ph-x { border: none; background: none; font-size: 14px; color: var(--muted); cursor: pointer; padding: 4px 7px; border-radius: 8px; }
  .jobspanel .ph-x:hover { background: var(--hover, #f1f0ec); }
  .stopall { border: 1px solid #e7c3ba; background: #fff; color: var(--red); font: inherit; font-weight: 600; font-size: 12px; padding: 5px 11px; border-radius: 8px; cursor: pointer; }
  .stopall:hover:not(:disabled) { background: #fbeeea; }
  .stopall:disabled { opacity: .55; cursor: default; }
  .jr-stop { flex: none; border: 1px solid #e7c3ba; background: #fff; color: var(--red); font: inherit; font-weight: 600; font-size: 11px; padding: 3px 9px; border-radius: 7px; cursor: pointer; }
  .jr-stop:hover:not(:disabled) { background: #fbeeea; }
  .jr-stop:disabled { opacity: .55; cursor: default; }
  .jbody { flex: 1; min-height: 0; overflow: auto; padding: 10px 12px 18px; }
  .jempty { padding: 22px 12px; font-size: 13px; color: var(--muted); }
  .jrow { display: block; width: 100%; text-align: left; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 11px 13px; margin-bottom: 9px; cursor: pointer; transition: background .12s, border-color .12s; }
  .jrow:hover { background: var(--sand); border-color: var(--muted); }
  .jr-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .jr-name { flex: 1; font-size: 13px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .jr-pct { font-size: 11.5px; font-weight: 700; color: var(--muted); flex: none; }
  .jr-bar { height: 5px; border-radius: 999px; background: #efece4; overflow: hidden; margin: 8px 0 6px; }
  .jr-bar > i { display: block; height: 100%; background: var(--coral); border-radius: 999px; transition: width .3s; }
  .jr-bar > i.lite { background: linear-gradient(90deg, #2f8f6a, #7fc9a8); }
  .jr-sub { font-size: 11.5px; color: var(--muted); }
  .jhint { font-size: 11px; color: var(--muted); padding: 8px 4px 0; line-height: 1.5; }
  /* enrichment agent */
  .adot { width: 8px; height: 8px; border-radius: 50%; background: var(--coral); display: inline-block; animation: dotpulse 1.2s ease-in-out infinite; }
  .adot.paused { background: var(--muted); animation: none; }
  .actrls { display: flex; align-items: center; gap: 10px; padding: 4px 2px 12px; border-bottom: 1px solid var(--line); margin-bottom: 10px; flex-wrap: wrap; }
  .abtn { font-size: 12px; font-weight: 600; border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 6px 12px; cursor: pointer; color: var(--ink); }
  .abtn.pause { border-color: #eccdbd; color: var(--coral); }
  .abtn:disabled { opacity: .55; cursor: default; }
  .aknob { font-size: 11.5px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .aknob input { width: 46px; border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; font: inherit; font-size: 12px; text-align: center; color: var(--ink); }
  .jsec { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 700; margin: 12px 2px 8px; }
  .jdone { font-size: 12.5px; color: var(--muted); padding: 5px 2px; display: flex; align-items: center; gap: 8px; }
  .jd-i { color: var(--green); font-weight: 700; }
  .jd-i.fail { color: var(--amber); }
  .tb-sort { margin-left: auto; display: flex; gap: 5px; align-items: center; }
  .tb-sort select { border: 1px solid var(--line); border-radius: 7px; background: #fff; font: inherit; font-size: 12.5px; padding: 4px 6px; color: var(--ink); cursor: pointer; }
  .tb-search { display: flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 4px 9px; color: var(--muted); }
  .tb-search input { border: none; outline: none; font: inherit; font-size: 12.5px; background: none; width: 170px; color: var(--ink); }
  .tb-clear { border: none; background: none; cursor: pointer; color: var(--muted); font-size: 11px; }
  .tb-clear:hover { color: var(--ink); }

  /* === bulk action bar === */
  .bulkbar { display: flex; align-items: center; gap: 8px; margin: 0 16px 8px; padding: 8px 14px; background: #eef5fb; border: 1px solid #cfe0ee; border-radius: 11px; font-size: 12.5px; }
  .bb-n { font-weight: 700; color: var(--blue); }
  .bb-move { position: relative; }
  .bb-btn { border: 1px solid var(--line); background: #fff; border-radius: 7px; padding: 5px 11px; font: inherit; font-size: 12.5px; font-weight: 600; color: var(--ink); cursor: pointer; }
  .bb-btn:hover { background: var(--sand); }
  .bb-btn.danger { color: var(--red); border-color: #eccac3; }
  .bb-btn.danger:hover { background: #fbeeeb; }
  .bb-btn:disabled { opacity: .5; cursor: default; }
  .bb-pop { position: absolute; top: 110%; left: 0; z-index: 5; background: #fff; border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.1); padding: 5px; min-width: 160px; max-height: 240px; overflow: auto; }
  .bb-pop button { display: block; width: 100%; text-align: left; border: none; background: none; padding: 7px 10px; border-radius: 7px; font: inherit; font-size: 12.5px; cursor: pointer; color: var(--ink); }
  .bb-pop button:hover { background: var(--sand); }
  .bb-clear { margin-left: auto; border: none; background: none; color: var(--muted); cursor: pointer; font: inherit; font-size: 12px; }
  .bb-clear:hover { color: var(--ink); text-decoration: underline; }

  /* === folder summary strip === */
  .sumstrip { display: flex; align-items: center; gap: 10px; padding: 2px 22px 8px; font-size: 12px; color: #6b675e; }
  .sumstrip b { color: var(--ink); font-weight: 700; }
  .sumstrip i { width: 1px; height: 11px; background: var(--line); display: inline-block; }
  .sumstrip .enr { display: flex; align-items: center; gap: 6px; color: #2f8f6a; font-weight: 600; }

  /* === empty-state dropzone + drag overlay === */
  .dropzone { margin: 16px 22px; padding: 48px 20px; border: 2px dashed #d3cdbf; border-radius: 16px; display: flex; flex-direction: column; align-items: center; gap: 8px; color: var(--muted); cursor: pointer; background: #fdfdfb; transition: border-color .15s, background .15s; }
  .dropzone:hover { border-color: var(--blue); background: #f5f9fc; color: var(--blue); }
  .dz-ttl { font-size: 14px; font-weight: 600; color: var(--ink); }
  .dz-sub { font-size: 12px; }
  .dropmask { position: absolute; inset: 0; z-index: 20; background: rgba(63,127,176,.08); backdrop-filter: blur(1px); display: grid; place-items: center; pointer-events: none; }
  .dropcard { background: #fff; border: 2px dashed var(--blue); border-radius: 18px; padding: 30px 44px; display: flex; flex-direction: column; align-items: center; gap: 6px; color: var(--blue); box-shadow: 0 12px 40px rgba(0,0,0,.12); }
  .dropttl { font-size: 16px; font-weight: 700; }
  .dropsub { font-size: 12.5px; color: var(--muted); }

  /* === rich list === */
  .glbl { padding: 11px 16px 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; }
  .glbl.pad { padding: 11px 22px 6px; }
  .list .lh, .list .lr { display: grid; grid-template-columns: 26px 1fr 56px 56px 58px 78px 70px; align-items: center; gap: 8px; padding: 11px 16px; border-bottom: 1px solid var(--line); position: relative; }
  .list .lh { color: var(--muted); font-size: 10.5px; text-transform: uppercase; font-weight: 600; }
  .sh-col { background: none; border: none; padding: 0; font: inherit; font-size: 10.5px; text-transform: uppercase; font-weight: 600; color: var(--muted); cursor: pointer; text-align: left; }
  .sh-col:hover { color: var(--ink); }
  .cbcell { display: flex; align-items: center; }
  .cbx { width: 15px; height: 15px; cursor: pointer; accent-color: var(--blue); margin: 0; }
  .lr.checked { background: #eef5fb; }
  .thumb { width: 26px; height: 30px; border-radius: 6px; object-fit: cover; object-position: top; border: 1px solid #dde6ee; flex: none; }
  .row-actions { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); display: flex; gap: 6px; opacity: 0; transition: opacity .12s; background: linear-gradient(90deg, transparent, #fcfbf8 22%); padding-left: 28px; }
  .lr:hover .row-actions { opacity: 1; }
  .ra { font-size: 11.5px; font-weight: 600; border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 7px; padding: 4px 9px; cursor: pointer; }
  .ra:hover { background: var(--hover, #f4f3f0); border-color: var(--muted); }
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
  .dot.l { background: #2f8f6a; animation: dotpulse 1.2s ease-in-out infinite; }
  @keyframes dotpulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .pb i.lite { background: linear-gradient(90deg, #2f8f6a, #7fc9a8); }
  .lang { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 5px; justify-self: start; }
  .lang.en { background: #eaf2f8; color: var(--blue); }
  .lang.my { background: #fbf3e6; color: var(--amber); }
  /* metrics = ink (data, not alarm); color reserved for status dot + brand */
  .used { color: var(--ink); font-weight: 600; }
  .acc { color: var(--ink); font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
  .acc .abar { width: 26px; height: 4px; border-radius: 999px; background: var(--line, #e7e3d9); overflow: hidden; flex: none; }
  .acc .abar > i { display: block; height: 100%; background: var(--green); border-radius: 999px; }

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
  .log { font: 11.5px/1.7 ui-monospace, Menlo, monospace; max-height: 170px; overflow: auto; background: #fbfaf7; border-radius: 8px; padding: 8px 10px; color: var(--ink, #1a1a18); }
  .ln { display: flex; gap: 8px; }
  .ln .ts { color: #8a857a; flex: none; }
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
  .rcontent { font-size: 13px; color: var(--ink, #1a1a18); }
  .readlist { display: flex; flex-direction: column; gap: 6px; }
  .rrow { padding: 7px 10px; background: #fbfaf7; border: 1px solid var(--line); border-radius: 8px; line-height: 1.45; color: var(--ink, #1a1a18); }
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

  /* delete confirm modal (double-confirm) — literal colors: renders OUTSIDE .src-shell so scoped tokens are unavailable */
  .delmodal { position: fixed; z-index: 81; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(460px, calc(100vw - 32px)); background: #fff; border: 1px solid #e7e3d9; border-radius: 16px; padding: 18px 20px 16px; box-shadow: 0 20px 60px rgba(0,0,0,.22); }
  .del-head { display: flex; align-items: center; justify-content: space-between; }
  .del-ttl { font-size: 16px; font-weight: 650; color: #1a1a18; }
  .del-x { border: none; background: none; font-size: 14px; color: #8a857c; cursor: pointer; padding: 4px 7px; border-radius: 8px; }
  .del-x:hover { background: #f1f0ec; }
  .del-warn { margin: 10px 0 14px; font-size: 12.5px; line-height: 1.5; color: #c0492f; }
  .del-card { background: #f9f9f8; border: 1px solid #e7e3d9; border-radius: 11px; padding: 11px 13px; }
  .del-name { font-size: 13.5px; font-weight: 600; color: #1a1a18; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .del-meta { font-size: 11.5px; color: #8a857c; margin-top: 3px; line-height: 1.4; }
  .del-also { font-size: 11.5px; color: #8a857c; margin: 12px 0 14px; line-height: 1.5; }
  .del-lab { display: block; font-size: 12.5px; color: #1a1a18; margin-bottom: 6px; }
  .del-lab b { font-weight: 700; }
  .del-in { width: 100%; box-sizing: border-box; border: 1px solid #e7e3d9; border-radius: 9px; padding: 9px 11px; font: inherit; font-size: 13px; color: #1a1a18; background: #fff; outline: none; }
  .del-in:focus { border-color: #c0492f; }
  .del-acts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; align-items: center; }
  .del-cancel { border: 1px solid #e7e3d9; background: #fff; color: #1a1a18; font: inherit; font-weight: 600; font-size: 13px; padding: 8px 16px; border-radius: 9px; cursor: pointer; }
  .del-cancel:hover { background: #f4f3f0; }
  .del-go { border: none; background: #c0492f; color: #fff; font: inherit; font-weight: 600; font-size: 13px; padding: 8px 16px; border-radius: 9px; cursor: pointer; }
  .del-go:disabled { background: #e7c3ba; color: #fff; cursor: not-allowed; }
  .del-go:not(:disabled):hover { background: #a93e28; }

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
