<script lang="ts">
  import '$lib/dashboard.css';
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import { range, tick, wsItem, WS_ITEMS, brainTeachSignal, loadBrainData, mobileNav } from '$lib/dashstore';
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
  import Accuracy from '$lib/dashboard/sections/Accuracy.svelte';
  import SelfHeal from '$lib/dashboard/sections/SelfHeal.svelte';
  import GraphRAG from '$lib/dashboard/sections/GraphRAG.svelte';
  import Eval from '$lib/dashboard/sections/Eval.svelte';
  import Cockpit from '$lib/dash/Cockpit.svelte';
  import Brain from '../brain/+page.svelte';

  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  const COMP: Record<string, any> = {
    overview: Overview, live: Cockpit, exec: Exec, users: Users, perf: Perf,
    knowledge: KnowledgeSec, review: Review, system: System, learning: Learning,
    dream: Dream, accuracy: Accuracy, selfheal: SelfHeal, graphrag: GraphRAG,
    eval: Eval
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

  // Document upload/import lives ONLY on the Sources page — Workspace has no upload UI.
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
    mobileNav.set(false);           // close the mobile rail after choosing
    try { history.replaceState(null, '', '/workspace?v=' + id); } catch {}
  }
</script>

<div class="ws">
  {#if $mobileNav}
    <button class="wsrail-scrim" onclick={() => mobileNav.set(false)} aria-label="Close menu"></button>
  {/if}
  <!-- persistent left rail (slide-in overlay on mobile via the header hamburger) -->
  <aside class="wsrail" class:mob-open={$mobileNav}>
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
  .hidden { display: none; }
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

  /* mobile rail overlay (toggled by the global header hamburger) */
  .wsrail-scrim { display: none; }

  @media (max-width: 860px) {
    .ws { grid-template-columns: 1fr; }
    /* rail becomes a slide-in overlay instead of vanishing */
    .wsrail {
      position: fixed; top: 56px; left: 0; bottom: 0; width: 250px; max-width: 82vw;
      z-index: 71; transform: translateX(-100%); transition: transform .22s ease;
      box-shadow: 2px 0 24px rgba(40,35,30,.16);
    }
    .wsrail.mob-open { transform: none; }
    .wsrail-scrim {
      display: block; position: fixed; inset: 0; z-index: 70;
      background: rgba(30,28,25,.34); border: none; cursor: default;
    }
    .ws-head, .ws-body { padding-left: 16px; padding-right: 16px; }
  }

</style>
