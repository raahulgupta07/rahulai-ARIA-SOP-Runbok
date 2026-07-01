<script lang="ts">
  import '$lib/dashboard.css';
  import { page } from '$app/stores';
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import { range, startLive, tick, mcTab, MC_TABS } from '$lib/dashstore';
  import { RANGES } from '$lib/dashutil';
  import { onMount } from 'svelte';

  let { children } = $props();
  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  onMount(() => startLive());   // open the SSE push stream for live dashboards

  let path = $derived($page.url.pathname);
  // Mission Control = the index route (section tabs). Graph = its own canvas route.
  let onMC = $derived(path === '/dashboard');

  // ── glass live ticker data (frozen, very top) ──
  let vit = $state<any>(null);
  let flash = $state(false);
  $effect(() => {
    $tick;
    if (!isAdmin) return;
    api.dashboardVitals().then((r) => {
      vit = r; flash = true; setTimeout(() => (flash = false), 700);
    }).catch(() => {});
  });
  const daemonsOk = $derived(vit ? vit.daemons.every((d: any) => !d.enabled || !d.stale) : true);
  function fmtBytes(n: number) {
    if (!n) return '0';
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
  }

  // route nav (Mission Control vs Graph)
  const ADMIN_NAV = [
    { href: '/dashboard', label: 'Mission Control', exact: true },
    { href: '/dashboard/graph', label: 'Graph' },
    { href: '/dashboard/contradictions', label: 'Contradictions' }
  ];
  const USER_NAV = [
    { href: '/dashboard', label: 'Mission Control', exact: true },
    { href: '/dashboard/graph', label: 'My map' }
  ];
  const nav = $derived(isAdmin ? ADMIN_NAV : USER_NAV);
  function on(item: any) { return item.exact ? path === item.href : path.startsWith(item.href); }

  // section tabs filtered by role
  const tabs = $derived(MC_TABS.filter((t) => t.roles.includes(isAdmin ? 'admin' : 'user')));
</script>

<div class="dash">
  <div class="dhead">
    <!-- frozen glass live ticker, very top -->
    {#if isAdmin && vit}
      <div class="gtick" class:flash>
        <span class="gt-live"></span>
        <span class="gt-item"><b>{vit.active_now ?? 0}</b> online</span>
        <span class="gt-div">·</span>
        <span class="gt-item"><b>{vit.questions_today ?? 0}</b> Q today</span>
        <span class="gt-div">·</span>
        <span class="gt-item"><b>${(vit.cost_today ?? 0).toFixed(2)}</b> today</span>
        <span class="gt-div">·</span>
        <span class="gt-item">queue <b class:warn={vit.ingest_queue > 0}>{vit.ingest_queue ?? 0}</b></span>
        <span class="gt-div">·</span>
        <span class="gt-dae"><span class="gt-dot" class:bad={!daemonsOk}></span>{daemonsOk ? 'daemons ok' : 'daemon stale'}</span>
        <span class="gt-db">DB {fmtBytes(vit.db_bytes)}</span>
      </div>
    {/if}

    <div class="dhrow">
      <div class="dtitle">
        <h1>{isAdmin ? 'Organisation dashboard' : 'My dashboard'}</h1>
        <p class="sub">{isAdmin ? 'Org-wide knowledge health, usage & people' : 'Your knowledge activity & insights'}</p>
      </div>
      <div class="dfilters">
        <div class="seg-group">
          {#each RANGES as r}
            <button class="segb" class:on={$range === r.d} onclick={() => range.set(r.d)}>{r.label}</button>
          {/each}
        </div>
      </div>
    </div>

    <nav class="subnav">
      {#each nav as item}
        <a class="snav" class:on={on(item)} href={item.href}>{item.label}</a>
      {/each}
    </nav>

    <!-- frozen section tabs (Mission Control only) -->
    {#if onMC}
      <div class="mctabs">
        {#each tabs as t}
          <button class="mctab" class:on={$mcTab === t.id} onclick={() => mcTab.set(t.id)}>{t.label}</button>
        {/each}
        <span class="mc-livechip"><i></i> live</span>
      </div>
    {/if}
  </div>

  <div class="dpage">
    {@render children()}
  </div>
</div>
