<script lang="ts">
  import '$lib/dashboard.css';
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import { range, tick, wsItem, WS_ITEMS, brainTeachSignal, brainFilesSignal, brainScanSignal, brainS3Signal, loadBrainData } from '$lib/dashstore';
  import { RANGES } from '$lib/dashutil';
  import { onMount } from 'svelte';
  import Overview from '$lib/dashboard/sections/Overview.svelte';
  import Exec from '$lib/dashboard/sections/Exec.svelte';
  import Users from '$lib/dashboard/sections/Users.svelte';
  import Perf from '$lib/dashboard/sections/Perf.svelte';
  import KnowledgeSec from '$lib/dashboard/sections/Knowledge.svelte';
  import Review from '$lib/dashboard/sections/Review.svelte';
  import System from '$lib/dashboard/sections/System.svelte';
  import Learning from '$lib/dashboard/sections/Learning.svelte';
  import Dream from '$lib/dashboard/sections/Dream.svelte';
  import Cockpit from '$lib/dash/Cockpit.svelte';
  import Brain from '../brain/+page.svelte';

  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  const COMP: Record<string, any> = {
    overview: Overview, live: Cockpit, exec: Exec, users: Users, perf: Perf,
    knowledge: KnowledgeSec, review: Review, system: System, learning: Learning,
    dream: Dream
  };

  // live ticker (admin)
  let vit = $state<any>(null);
  let flash = $state(false);
  $effect(() => {
    $tick;
    if (!isAdmin) return;
    api.dashboardVitals().then((r) => { vit = r; flash = true; setTimeout(() => (flash = false), 700); }).catch(() => {});
  });
  const daemonsOk = $derived(vit ? vit.daemons.every((d: any) => !d.enabled || !d.stale) : true);

  // Health badge (audit score) for the BRAIN → Health rail row
  let healthScore = $state<number | null>(null);
  onMount(() => {
    loadBrainData();   // revalidate the shared Brain bundle (store already hydrated → instant paint everywhere)
    api.auditCoverage(30).then((r) => (healthScore = r?.score ?? null)).catch(() => {});
  });

  // ---- master kill switch ----
  let ingState = $state<any>(null);
  let killConfirm = $state(false);
  let killBusy = $state(false);
  async function refreshIng() { try { ingState = await api.ingestState(); } catch {} }
  $effect(() => { $tick; if (isAdmin) refreshIng(); });
  async function doStopAll() { killBusy = true; try { await api.ingestStop(); await refreshIng(); } catch {} finally { killBusy = false; killConfirm = false; } }
  async function doResume() { killBusy = true; try { await api.ingestResume(); await refreshIng(); } catch {} finally { killBusy = false; } }
  let paused = $derived(!!ingState?.paused);
  // Upload / Teach buttons (top-bar) bridge into the embedded Brain. Only on Brain views.
  let onBrain = $derived(active?.kind === 'knowledge');   // Brain mounted → buttons work
  function fireTeach() { brainTeachSignal.update((n) => n + 1); }

  // ---- OpenWebUI-style upload menu (no modal) ----
  let upMenu = $state(false);
  let upScan = $state<{ exists: boolean; found: number; new: number } | null>(null);
  let upS3 = $state<{ configured: boolean; found: number; new: number } | null>(null);
  let wFileInput: HTMLInputElement;
  let wFolderInput: HTMLInputElement;
  function toggleUpMenu() {
    upMenu = !upMenu;
    if (upMenu && isAdmin) {
      api.scanPreview().then((r) => (upScan = r)).catch(() => (upScan = null));
      api.s3ScanPreview().then((r) => (upS3 = r)).catch(() => (upS3 = null));
    }
  }
  function bridgeFiles(files: FileList | null) {
    if (files && files.length) brainFilesSignal.set(Array.from(files));
    upMenu = false;
  }
  function fireScan() { brainScanSignal.update((n) => n + 1); upMenu = false; }
  function fireS3() { brainS3Signal.update((n) => n + 1); upMenu = false; }

  // ---- Cloud import: SharePoint + OneDrive (location only; creds live in Settings) ----
  type GraphCfg = { site_host: string; site_path: string; user_upn: string; drive_id: string; folder: string; sync_enabled?: boolean; sync_interval_h?: number; creds_ready?: boolean; kind_enabled?: boolean };
  const emptyCfg = (): GraphCfg => ({ site_host: '', site_path: '', user_upn: '', drive_id: '', folder: '', sync_enabled: false, sync_interval_h: 6, creds_ready: false, kind_enabled: true });
  let spOpen = $state(false);
  let spKind = $state<'sharepoint' | 'onedrive'>('sharepoint');
  let spCfg = $state<GraphCfg>(emptyCfg());
  let spBusy = $state(false);
  let spMsg = $state('');
  let spReady = $derived(!!spCfg.creds_ready && spCfg.kind_enabled !== false);
  function loadGraphCfg() {
    spMsg = '';
    api.graphConfig(spKind).then((r) => { spCfg = { ...emptyCfg(), ...r }; }).catch(() => { spCfg = emptyCfg(); });
  }
  function openSharePoint(kind: 'sharepoint' | 'onedrive' = 'sharepoint') {
    upMenu = false; spOpen = true; spKind = kind; loadGraphCfg();
  }
  function spSwitch(kind: 'sharepoint' | 'onedrive') {
    if (kind === spKind) return;
    spKind = kind; loadGraphCfg();
  }
  async function spSave() {
    spBusy = true; spMsg = '';
    try { const r = await api.graphSaveConfig(spKind, spCfg as any); spCfg = { ...spCfg, ...r }; spMsg = 'Saved.'; }
    catch (e: any) { spMsg = e?.message || 'save failed'; } finally { spBusy = false; }
  }
  async function spTest() {
    spBusy = true; spMsg = 'Testing…';
    try { const r = await api.graphTest(spKind); spMsg = r.ok ? 'Connected ✓' : ('Failed: ' + (r.detail || 'check creds')); }
    catch (e: any) { spMsg = e?.message || 'test failed'; } finally { spBusy = false; }
  }
  async function spImport() {
    spBusy = true; spMsg = 'Importing…';
    try {
      const r = await api.graphImport(spKind);
      spMsg = r.ok ? `Queued ${r.queued} · skipped ${r.skipped} (of ${r.found})` : ('Failed: ' + (r.detail || 'not configured'));
    } catch (e: any) { spMsg = e?.message || 'import failed'; } finally { spBusy = false; }
  }
  function fmtBytes(n: number) {
    if (!n) return '0';
    if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(0)} MB`;
    return `${(n / 1073741824).toFixed(1)} GB`;
  }

  // role-filtered items + grouped rail
  let items = $derived(WS_ITEMS.filter((t) => t.roles.includes(isAdmin ? 'admin' : 'user')));
  let groups = $derived([...new Set(items.map((i) => i.group))]);
  let active = $derived(items.find((i) => i.id === $wsItem) ?? items[0]);

  // non-admins can't land on an admin-only item → fall back to their first item
  $effect(() => { if (active && !active.roles.includes(isAdmin ? 'admin' : 'user')) wsItem.set(items[0]?.id ?? 'brain'); });

  // deep-link ?v=exec  + old /dashboard#exec style
  onMount(() => {
    const v = new URLSearchParams(location.search).get('v') || location.hash.replace('#', '');
    if (v && WS_ITEMS.some((t) => t.id === v)) wsItem.set(v);
  });
  function pick(id: string) {
    wsItem.set(id);
    try { history.replaceState(null, '', '/workspace?v=' + id); } catch {}
  }
</script>

<div class="ws">
  <!-- persistent left rail -->
  <aside class="wsrail">
    {#each groups as g}
      {#if g !== 'Overview'}<div class="wsr-grp">{g}</div>{/if}
      {#each items.filter((i) => i.group === g) as it}
        <button class="wsr-item" class:on={active?.id === it.id} onclick={() => pick(it.id)}>
          <span>{it.label}</span>
          {#if it.badge === 'health' && healthScore != null}<span class="wsr-badge">{healthScore}</span>{/if}
        </button>
      {/each}
    {/each}
    <div class="wsr-foot">{vit ? 'Connected' : '—'}</div>
  </aside>

  <!-- content -->
  <div class="wscol">
    {#if isAdmin && vit}
      <div class="statbar" class:flash>
        <div class="spills">
          <span class="spill" title="Users online now"><span class="livedot"></span><b>{vit.active_now ?? 0}</b> online</span>
          <span class="spill" title="Questions today">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <b>{vit.questions_today ?? 0}</b> today
          </span>
          <span class="spill" title="Spend today">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            <b>${(vit.cost_today ?? 0).toFixed(2)}</b>
          </span>
          <span class="spill" class:warn={vit.ingest_queue > 0} title="Ingest queue">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            <b>{vit.ingest_queue ?? 0}</b> queued
          </span>
          <span class="spill" title="Background daemons"><span class="ddot" class:bad={!daemonsOk}></span>{daemonsOk ? 'Healthy' : 'Stale'}</span>
          <span class="spill" title="Database size">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg>
            <b>{fmtBytes(vit.db_bytes)}</b>
          </span>
        </div>
        <div class="sacts">
          {#if onBrain}
            <div class="upwrap">
              <button class="tbtn up" onclick={toggleUpMenu} title="Add documents">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
                Add
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="margin-left:-1px"><path d="M6 9l6 6 6-6"/></svg>
              </button>
              {#if upMenu}
                <button class="upmenu-scrim" onclick={() => (upMenu = false)} aria-label="Close"></button>
                <div class="upmenu" role="menu">
                  <button class="upmi" onclick={() => wFileInput.click()}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>
                    <span><b>Upload files</b><i>one or many · PDF, PNG, JPG</i></span>
                  </button>
                  <button class="upmi" onclick={() => wFolderInput.click()}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                    <span><b>Upload directory</b><i>whole folder · subfolders</i></span>
                  </button>
                  {#if isAdmin}
                    <button class="upmi" onclick={fireScan}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h6l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><path d="M12 11v5"/><path d="M9.5 13.5 12 11l2.5 2.5"/></svg>
                      <span><b>Import from server</b><i>{upScan ? (upScan.exists ? `${upScan.new} new · ${upScan.found} in folder` : 'folder not found') : 'scan server documents folder'}</i></span>
                    </button>
                    {#if upS3 && upS3.configured}
                      <div class="upmenu-sep"></div>
                      <button class="upmi" onclick={fireS3}>
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/><path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg>
                        <span><b>Import from S3</b><i>{`${upS3.new} new · ${upS3.found} in bucket`}</i></span>
                      </button>
                    {/if}
                    <div class="upmenu-sep"></div>
                    <button class="upmi" onclick={() => openSharePoint('sharepoint')}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 9h18"/><path d="M8 14h4"/></svg>
                      <span><b>Import from SharePoint</b><i>Microsoft 365 document library</i></span>
                    </button>
                    <button class="upmi" onclick={() => openSharePoint('onedrive')}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 19h11a4 4 0 0 0 .8-7.9A5.5 5.5 0 0 0 7.6 9.2 4.2 4.2 0 0 0 6.5 19z"/></svg>
                      <span><b>Import from OneDrive</b><i>A user's OneDrive files</i></span>
                    </button>
                  {/if}
                </div>
              {/if}
              <input bind:this={wFileInput} type="file" multiple accept=".pdf,.png,.jpg,.jpeg" class="hidden"
                onchange={(e) => { bridgeFiles(e.currentTarget.files); e.currentTarget.value = ''; }} />
              <input bind:this={wFolderInput} type="file" multiple webkitdirectory class="hidden"
                onchange={(e) => { bridgeFiles(e.currentTarget.files); e.currentTarget.value = ''; }} />
            </div>
            <button class="tbtn" onclick={fireTeach} title="Teach a fact">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg>
              Teach fact
            </button>
          {/if}
        </div>
      </div>
    {/if}

    {#if paused}
      <div class="pausebar">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><line x1="9" y1="4" x2="9" y2="20"/><line x1="15" y1="4" x2="15" y2="20"/></svg> <b>Ingest paused.</b>
        {ingState?.queued ?? 0} held · {ingState?.processing ?? 0} stopping · {ingState?.cancelled ?? 0} cancelled · chat replies paused
        <button class="pb-resume" onclick={doResume} disabled={killBusy}>{killBusy ? '…' : 'Resume'}</button>
      </div>
    {/if}

    {#if active?.kind === 'insight'}
      <!-- dashboard sections: own header + range filter -->
      <div class="ws-head">
        <div>
          <h1 class="ws-title">{active.label}</h1>
          <p class="ws-sub">{isAdmin ? 'Org-wide knowledge health, usage & people' : 'Your knowledge activity & insights'}</p>
        </div>
        <div class="seg-group">
          {#each RANGES as r}
            <button class="segb" class:on={$range === r.d} onclick={() => range.set(r.d)}>{r.label}</button>
          {/each}
        </div>
      </div>
      <div class="ws-body">
        {#key active.id}
          {@const Comp = COMP[active.id]}
          <div class="mcbody">
            <Comp />
            {#if active.id === 'overview'}
              <h2 class="ws-sechead">Performance</h2>
              <Perf />
            {/if}
          </div>
        {/key}
      </div>
    {:else}
      <!-- knowledge: embedded Brain hub (its own header), view driven by prop.
           {#key active.id} forces a clean remount per rail item so extView is
           re-read (single-mount kept the previous tab → Documents showed Audit).
           Instant paint preserved: docs/facts come from the shared brainData store. -->
      {#key active.id}
        <div class="ws-brain">
          <Brain embedded showTabs={false} extView={active?.view ?? { tab: 'brain', feed: 'all' }} />
        </div>
      {/key}
    {/if}
  </div>

  {#if killConfirm}
    <button class="killscrim" onclick={() => (killConfirm = false)} aria-label="Close"></button>
    <div class="killcard" role="dialog" aria-modal="true">
      <div class="kc-title"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px"><circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/></svg> Stop everything?</div>
      <p class="kc-body">Freezes the ingest queue, cancels the document being processed, and aborts running LLM calls (vision · compile · tag · chat). Queued docs are held — Resume to continue.</p>
      <div class="kc-acts">
        <button class="btn" onclick={() => (killConfirm = false)}>Cancel</button>
        <button class="killbtn" onclick={doStopAll} disabled={killBusy}>{killBusy ? 'Stopping…' : 'Stop all'}</button>
      </div>
    </div>
  {/if}
</div>

{#if spOpen}
  <button class="sp-scrim" onclick={() => (spOpen = false)} aria-label="Close"></button>
  <div class="sp-modal" role="dialog" aria-label="Import from cloud">
    <div class="sp-head">
      <b>Import from cloud</b>
      <button class="sp-x" onclick={() => (spOpen = false)} aria-label="Close">✕</button>
    </div>
    <div class="sp-tabs">
      <button class="sp-tab {spKind === 'sharepoint' ? 'on' : ''}" onclick={() => spSwitch('sharepoint')}>SharePoint</button>
      <button class="sp-tab {spKind === 'onedrive' ? 'on' : ''}" onclick={() => spSwitch('onedrive')}>OneDrive</button>
    </div>
    <p class="sp-sub">
      {#if spKind === 'sharepoint'}Pulls every PDF/image from a Microsoft 365 document library into Aria.{:else}Pulls every PDF/image from a user's OneDrive into Aria.{/if}
    </p>
    {#if !spReady}
      <div class="sp-gate">
        {#if !spCfg.creds_ready}
          Microsoft 365 isn't connected yet. <a href="/settings/microsoft">Set it up in Settings →</a>
        {:else}
          {spKind === 'sharepoint' ? 'SharePoint' : 'OneDrive'} is turned off. Enable it in <a href="/settings/microsoft">Settings → Microsoft 365 →</a>
        {/if}
      </div>
    {:else}
      <div class="sp-grid">
        {#if spKind === 'sharepoint'}
          <label class="sp-f"><span>Site host</span><input bind:value={spCfg.site_host} placeholder="contoso.sharepoint.com" /></label>
          <label class="sp-f"><span>Site path</span><input bind:value={spCfg.site_path} placeholder="/sites/IT" /></label>
        {:else}
          <label class="sp-f sp-f-wide"><span>User (UPN / email)</span><input bind:value={spCfg.user_upn} placeholder="alice@contoso.com" /></label>
        {/if}
        <label class="sp-f"><span>Drive ID <i>(optional)</i></span><input bind:value={spCfg.drive_id} placeholder="default drive if blank" /></label>
        <label class="sp-f"><span>Folder <i>(optional)</i></span><input bind:value={spCfg.folder} placeholder="Runbooks/SOPs" /></label>
      </div>
      <label class="sp-toggle">
        <input type="checkbox" bind:checked={spCfg.sync_enabled} />
        <span>Auto-sync — keep pulling new files on a schedule</span>
      </label>
      {#if spCfg.sync_enabled}
        <label class="sp-interval">
          <span>Sync every</span>
          <input type="number" min="1" max="168" bind:value={spCfg.sync_interval_h} />
          <span>hours</span>
        </label>
      {/if}
      {#if spMsg}<div class="sp-msg">{spMsg}</div>{/if}
      <div class="sp-actions">
        <button class="sp-btn ghost" disabled={spBusy} onclick={spSave}>Save</button>
        <button class="sp-btn ghost" disabled={spBusy} onclick={spTest}>Test</button>
        <button class="sp-btn" disabled={spBusy} onclick={spImport}>Import all</button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .ws { display: grid; grid-template-columns: 210px 1fr; height: 100%; min-height: 0; }
  .wsrail {
    background: var(--sand); border-right: 1px solid var(--border);
    padding: 16px 11px; display: flex; flex-direction: column; gap: 1px; overflow-y: auto;
  }
  .wsr-grp { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; padding: 14px 8px 5px; }
  .wsr-grp:first-child { padding-top: 2px; }
  .wsr-item {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    text-align: left; padding: 8px 12px; border-radius: 9px; font-size: 13.5px; font-weight: 500;
    color: #46443f; background: transparent; border: none; cursor: pointer; transition: background .14s, color .14s;
  }
  .wsr-item:hover { background: var(--hover, #efefec); }
  .wsr-item.on { background: var(--navpill); color: var(--ink); font-weight: 600; }
  .wsr-badge { font-size: 11px; font-weight: 700; color: var(--muted); background: var(--cream, #faf9f5); border: 1px solid var(--border); border-radius: 6px; padding: 0 6px; line-height: 17px; }
  .wsr-foot { margin-top: auto; font-size: 11.5px; color: var(--muted); padding: 10px 8px 2px; }

  .wscol { min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }

  /* master kill switch */
  .sacts { display: flex; align-items: center; gap: 7px; margin-left: auto; padding: 0 14px; }
  .tbtn { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: var(--ink); cursor: pointer; white-space: nowrap; transition: background .14s; }
  .tbtn:hover { background: var(--hover, #efefec); }
  .tbtn.up { background: var(--clay); color: #fff; border-color: var(--clay); }
  .tbtn.up:hover { opacity: .92; background: var(--clay); }
  /* OpenWebUI-style add menu */
  .upwrap { position: relative; display: inline-flex; }
  .hidden { display: none; }
  .upmenu-scrim { position: fixed; inset: 0; z-index: 60; background: transparent; border: none; cursor: default; }
  .upmenu { position: absolute; top: calc(100% + 6px); right: 0; z-index: 61; width: 244px; background: #fff; border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 10px 30px rgba(40,35,30,.16); padding: 5px; animation: upin .13s ease-out; }
  @keyframes upin { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  .upmi { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; padding: 8px 9px; border: none; background: transparent; border-radius: 8px; cursor: pointer; transition: background .12s; }
  .upmi:hover { background: var(--hover, #f4f3f0); }
  .upmi svg { flex: none; color: var(--clay); }
  .upmi span { display: flex; flex-direction: column; min-width: 0; }
  .upmi b { font-size: 12.5px; font-weight: 600; color: var(--ink); }
  .upmi i { font-size: 11px; font-style: normal; color: var(--muted); }
  .upmenu-sep { height: 1px; background: var(--border); margin: 5px 7px; }
  .killbtn { font-size: 12.5px; font-weight: 600; padding: 6px 13px; border-radius: 8px; border: 1px solid #d8a99c; background: #fbeeea; color: #b03a22; cursor: pointer; white-space: nowrap; transition: background .14s; }
  .killbtn:hover { background: #f6ddd5; }
  .killbtn:disabled { opacity: .6; cursor: default; }
  .killbtn.resume { border-color: #bfe0c6; background: #eef7f0; color: #2f7d4f; }
  .pausebar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; background: #fdf6ec; border-bottom: 1px solid #ecd9b6; color: #8a6d2f; font-size: 12.5px; padding: 8px 18px; position: sticky; top: 0; z-index: 7; }
  .pausebar b { color: #6f5118; }
  .pb-resume { margin-left: auto; font-size: 12px; font-weight: 600; padding: 5px 12px; border-radius: 7px; border: 1px solid #bfe0c6; background: #eef7f0; color: #2f7d4f; cursor: pointer; }
  .killscrim { position: fixed; inset: 0; z-index: 60; background: rgba(40,35,30,.4); border: none; cursor: default; }
  .killcard { position: fixed; z-index: 61; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 380px; max-width: 92vw; background: #fff; border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px; box-shadow: 0 12px 40px rgba(40,35,30,.22); }
  .kc-title { font-family: var(--serif); font-size: 19px; font-weight: 600; color: #b03a22; }
  .kc-body { font-size: 13px; color: var(--muted); line-height: 1.55; margin: 8px 0 16px; }
  .kc-acts { display: flex; justify-content: flex-end; gap: 8px; }

  /* pinned status bar (Option A) — pill stats, dot status, no dividers */
  .statbar {
    position: sticky; top: 0; z-index: 8;
    display: flex; align-items: center;
    background: rgba(255,255,255,.85);
    -webkit-backdrop-filter: blur(14px) saturate(1.2); backdrop-filter: blur(14px) saturate(1.2);
    border-bottom: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
    transition: box-shadow .5s;
  }
  .statbar.flash { box-shadow: none; }
  .spills {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 9px 8px 9px 16px; min-width: 0;
  }
  .spill {
    display: inline-flex; align-items: center; gap: 6px; white-space: nowrap;
    font-size: 12.5px; color: var(--muted);
    padding: 5px 11px; border-radius: 999px;
    border: 1px solid var(--border); background: #faf9f7;
  }
  .spill b { font-weight: 700; color: var(--ink); font-size: 13px; }
  .spill svg { width: 13px; height: 13px; color: var(--muted); flex: none; }
  .spill.warn { border-color: #e7cfa0; background: #fdf6e9; color: #9a6a16; }
  .spill.warn b { color: #9a6a16; }
  .livedot { width: 7px; height: 7px; border-radius: 50%; background: #3f8f5f; flex: none;
    box-shadow: 0 0 0 0 rgba(63,143,95,.55); animation: lvping 2s ease-out infinite; }
  @keyframes lvping { 0% { box-shadow: 0 0 0 0 rgba(63,143,95,.5); } 70%,100% { box-shadow: 0 0 0 5px rgba(63,143,95,0); } }
  .ddot { width: 8px; height: 8px; border-radius: 50%; background: #3f8f5f; flex: none; }
  .ddot.bad { background: #c0492f; }
  @media (max-width: 820px) {
    .statbar { overflow-x: auto; flex-wrap: nowrap; }
    .spills { flex-wrap: nowrap; }
  }
  @media (prefers-reduced-motion: reduce) { .livedot { animation: none; } }

  .ws-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding: 18px 28px 12px; border-bottom: 1px solid var(--border); }
  .ws-title { font-family: var(--serif); font-size: 23px; font-weight: 600; color: var(--ink); }
  .ws-sub { font-size: 13px; color: var(--muted); margin-top: 3px; }
  .ws-body { padding: 18px 28px 48px; display: flex; flex-direction: column; gap: 13px; }
  .ws-sechead { font-family: var(--serif); font-size: 20px; font-weight: 600; color: var(--ink); margin: 26px 0 4px; padding-top: 18px; border-top: 1px solid var(--border); }
  .ws-brain { flex: 1; min-height: 0; }
  /* the embedded Brain root is h-full flex — give it height */
  .ws-brain :global(> div) { height: 100%; }

  @media (max-width: 720px) { .ws { grid-template-columns: 1fr; } .wsrail { display: none; } }

  /* SharePoint import modal */
  .sp-scrim { position: fixed; inset: 0; background: rgba(20,18,15,.34); z-index: 60; border: 0; }
  .sp-modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 61;
    width: min(560px, 92vw); background: var(--paper, #fff); border: 1px solid var(--border, #e0dfda);
    border-radius: 14px; padding: 18px 20px; box-shadow: 0 18px 50px rgba(0,0,0,.18); }
  .sp-head { display: flex; align-items: center; justify-content: space-between; font-size: 15px; color: var(--ink); }
  .sp-x { font-size: 13px; color: var(--muted); padding: 4px 8px; border-radius: 8px; }
  .sp-x:hover { background: #f1f0ec; }
  .sp-sub { font-size: 12px; color: var(--muted); margin: 6px 0 14px; line-height: 1.5; }
  .sp-tabs { display: inline-flex; gap: 2px; margin-top: 12px; padding: 3px; background: var(--bg-alt, #f4f3f0); border-radius: 9px; }
  .sp-tab { border: 0; background: transparent; padding: 5px 14px; font-size: 12.5px; border-radius: 7px; color: var(--muted); cursor: pointer; }
  .sp-tab.on { background: #fff; color: var(--ink); box-shadow: 0 1px 2px rgba(0,0,0,.08); }
  .sp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .sp-f-wide { grid-column: 1 / -1; }
  .sp-gate { margin-top: 14px; padding: 14px 16px; background: var(--bg-alt, #f4f3f0); border: 1px solid var(--border, #e0dfda); border-radius: 10px; font-size: 13px; color: var(--muted); line-height: 1.6; }
  .sp-gate a { color: var(--clay); font-weight: 600; }
  .sp-toggle { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 12.5px; color: var(--ink); cursor: pointer; }
  .sp-toggle input { width: 15px; height: 15px; accent-color: var(--clay); }
  .sp-interval { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12.5px; color: var(--muted); }
  .sp-interval input { width: 64px; border: 1px solid var(--border, #e0dfda); border-radius: 8px; padding: 5px 8px; font-size: 13px; color: var(--ink); background: #fff; outline: none; }
  .sp-interval input:focus { border-color: var(--clay); }
  .sp-f { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
  .sp-f i { font-style: normal; opacity: .7; }
  .sp-f input { border: 1px solid var(--border, #e0dfda); border-radius: 8px; padding: 7px 9px; font-size: 13px; color: var(--ink); background: #fff; outline: none; }
  .sp-f input:focus { border-color: var(--clay); }
  .sp-msg { margin-top: 10px; font-size: 12.5px; color: var(--ink); }
  .sp-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
  .sp-btn { font-size: 13px; padding: 7px 16px; border-radius: 9px; background: var(--clay); color: #fff; }
  .sp-btn.ghost { background: #fff; color: var(--muted); border: 1px solid var(--border, #e0dfda); }
  .sp-btn:disabled { opacity: .5; }
</style>
