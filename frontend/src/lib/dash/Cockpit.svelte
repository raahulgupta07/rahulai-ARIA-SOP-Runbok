<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { tick, brainData, loadBrainData } from '$lib/dashstore';
  import { onMount } from 'svelte';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived(me?.role === 'admin');

  // ── last-good data kept across refetches (no skeleton thrash) ──
  let vit = $state<any>(null);          // SYSTEM + ERRORS source
  let perf = $state<any>(null);         // THROUGHPUT source
  let docs = $state<any[]>([]);         // INGEST PIPELINE source
  let answers = $state<any[]>([]);      // LIVE ANSWERS source
  let loaded = $state(false);
  let flash = $state(false);

  // expanded in-flight doc → live event log
  let selDoc = $state<number | null>(null);
  let docEvents = $state<any[]>([]);

  // re-fetch on every heartbeat; keep last-good values on failure
  $effect(() => {
    $tick;
    if (!isAdmin) return;
    Promise.allSettled([
      api.dashboardVitals(),
      api.analyticsPerf(7),
      api.documents(),
      api.opsAnswersRecent(20)
    ]).then(([v, p, d, a]) => {
      if (v.status === 'fulfilled') vit = v.value;
      if (p.status === 'fulfilled') perf = p.value;
      if (d.status === 'fulfilled') docs = (d.value?.docs ?? d.value ?? []) as any[];
      if (a.status === 'fulfilled') answers = (a.value?.answers ?? []) as any[];
      loaded = true;
      flash = true; setTimeout(() => (flash = false), 600);
    });
  });

  onMount(() => { loadBrainData(); });
  // revalidate the shared Brain bundle on the heartbeat (stale-while-revalidate, no flicker)
  $effect(() => { $tick; if (isAdmin) loadBrainData(); });

  // KNOWLEDGE counts from the shared brain store
  const kdocs = $derived($brainData.docs?.length ?? 0);
  const kpages = $derived(($brainData.docs ?? []).reduce((s: number, d: any) => s + (d.pages ?? d.page_count ?? 0), 0));
  const kfacts = $derived($brainData.facts?.length ?? 0);
  const kqa = $derived($brainData.qaPairs?.length ?? 0);

  // refresh the expanded doc's event log alongside the heartbeat
  $effect(() => {
    $tick;
    if (selDoc == null) { docEvents = []; return; }
    api.documentLog(selDoc, 200).then((r) => (docEvents = r?.events ?? [])).catch(() => {});
  });

  // ── derived views ──
  const k = $derived(perf?.kpis ?? {});
  // in-flight = anything not yet ready (queued/processing/failed-but-retrying)
  const inflight = $derived(
    (docs ?? [])
      .filter((d: any) => d.status && d.status !== 'ready')
      .sort((a: any, b: any) => statusRank(a.status) - statusRank(b.status))
      .slice(0, 12)
  );
  const recent0 = $derived(answers[0] ?? null);

  function statusRank(s: string) {
    return ({ processing: 0, queued: 1, failed: 2 } as Record<string, number>)[s] ?? 3;
  }
  function ms(v: number) { return v == null ? '—' : v >= 1000 ? (v / 1000).toFixed(1) + 's' : Math.round(v) + 'ms'; }
  function fmtBytes(n: number) {
    if (!n) return '0';
    if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(0)} MB`;
    return `${(n / 1073741824).toFixed(1)} GB`;
  }
  function ago(iso: string | null) {
    if (!iso) return '';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return Math.max(1, Math.round(s)) + 's';
    if (s < 3600) return Math.round(s / 60) + 'm';
    return Math.round(s / 3600) + 'h';
  }
  function hueOk(pct: number) { return pct >= 80 ? '#3f8f5f' : pct >= 55 ? '#c98a2e' : '#c0492f'; }
  function shortDoc(n: string) { return (n || 'document').replace(/\.[a-z0-9]+$/i, '').replace(/[_-]+/g, ' ').slice(0, 42); }

  const daemonsOk = $derived(vit ? (vit.daemons ?? []).every((d: any) => !d.enabled || !d.stale) : true);
  const errorCount = $derived(
    vit ? (vit.ingest_failed ?? 0) + (vit.ingest_stuck ?? 0) + ((vit.daemons ?? []).filter((d: any) => d.enabled && d.stale).length) : 0
  );

  function pickDoc(id: number) { selDoc = selDoc === id ? null : id; }

  // funnel max for the retrieval-funnel bars
  function funnel(a: any) {
    const rows = [
      { lab: 'scanned', v: a?.scanned ?? 0, c: '#3f7fb0' },
      { lab: 'pool', v: a?.pool ?? 0, c: '#7b6bd6' },
      { lab: 'reranked', v: a?.reranked ?? 0, c: '#c98a2e' },
      { lab: 'cited', v: a?.cited ?? 0, c: '#3f8f5f' }
    ];
    const mx = Math.max(1, ...rows.map((r) => r.v));
    return rows.map((r) => ({ ...r, pct: (r.v / mx) * 100 }));
  }
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if !loaded}
  <div class="muted pad">Connecting to live operations…</div>
{:else}
  <div class="cockpit" class:flash>
    <!-- 1 · SYSTEM -->
    <section class="cpanel sys">
      <header class="ph"><span class="livedot"></span>SYSTEM</header>
      <div class="metrics">
        <div class="metric"><b style="color:#3f8f5f">online</b><span>API status</span></div>
        <div class="metric"><b>{fmtBytes(vit?.db_bytes)}</b><span>database</span></div>
        <div class="metric"><b>{vit?.active_now ?? 0}</b><span>users online</span></div>
        <div class="metric"><b class:bad={!daemonsOk}>{daemonsOk ? 'healthy' : 'stale'}</b><span>daemons</span></div>
        <div class="metric"><b class:warn={(vit?.ingest_queue ?? 0) > 0}>{vit?.ingest_queue ?? 0}</b><span>ingest queue</span></div>
        <div class="metric"><b>{vit?.questions_today ?? 0}</b><span>questions today</span></div>
      </div>
    </section>

    <!-- 2 · THROUGHPUT -->
    <section class="cpanel">
      <header class="ph">THROUGHPUT <i>· last 7d</i></header>
      <div class="metrics">
        <div class="metric"><b>{(k.answers ?? 0).toLocaleString()}</b><span>answers</span></div>
        <div class="metric"><b style="color:{hueOk(k.cache_hit_rate ?? 0)}">{k.cache_hit_rate ?? 0}%</b><span>cache hit</span></div>
        <div class="metric"><b>{ms(k.p50_total_ms)}</b><span>p50 latency</span></div>
        <div class="metric"><b>{ms(k.p95_total_ms)}</b><span>p95 latency</span></div>
      </div>
    </section>

    <!-- 7 · ERRORS -->
    <section class="cpanel">
      <header class="ph">ERRORS</header>
      {#if errorCount === 0}
        <div class="ok-line">✓ none — pipeline & daemons clean</div>
      {:else}
        <div class="err-list">
          {#if (vit?.ingest_failed ?? 0) > 0}<div class="err-row"><span class="ed"></span>{vit.ingest_failed} failed ingest{vit.ingest_failed > 1 ? 's' : ''}</div>{/if}
          {#if (vit?.ingest_stuck ?? 0) > 0}<div class="err-row warn"><span class="ed"></span>{vit.ingest_stuck} stuck in pipeline</div>{/if}
          {#each (vit?.daemons ?? []).filter((d: any) => d.enabled && d.stale) as d}
            <div class="err-row warn"><span class="ed"></span>daemon stale: {d.name}</div>
          {/each}
        </div>
      {/if}
    </section>

    <!-- 3 · KNOWLEDGE -->
    <section class="cpanel">
      <header class="ph">KNOWLEDGE</header>
      <div class="metrics">
        <div class="metric"><b>{kdocs.toLocaleString()}</b><span>documents</span></div>
        <div class="metric"><b>{kpages.toLocaleString()}</b><span>pages</span></div>
        <div class="metric"><b>{kfacts.toLocaleString()}</b><span>facts</span></div>
        <div class="metric"><b>{kqa.toLocaleString()}</b><span>Q&amp;A pairs</span></div>
      </div>
    </section>

    <!-- 4 · INGEST PIPELINE (wide) -->
    <section class="cpanel wide">
      <header class="ph">INGEST PIPELINE <i>· {inflight.length} in flight</i></header>
      {#if inflight.length === 0}
        <div class="muted sm">No active ingests — corpus is settled. ✓</div>
      {:else}
        <div class="ingest-list">
          {#each inflight as d (d.id)}
            <button class="ing-row" class:open={selDoc === d.id} onclick={() => pickDoc(d.id)}>
              <div class="ing-top">
                <span class="ing-name">{shortDoc(d.name)}</span>
                <span class="ing-stage st-{d.status}">{d.status}{d.progress != null ? ' · ' + d.progress + '%' : ''}</span>
              </div>
              <div class="pbar"><i class="pf st-{d.status}" style="width:{d.progress ?? (d.status === 'queued' ? 4 : 0)}%"></i></div>
            </button>
            {#if selDoc === d.id}
              <div class="ing-log">
                {#if docEvents.length === 0}
                  <div class="muted sm">No events logged yet…</div>
                {:else}
                  {#each docEvents as e}
                    <div class="logline lv-{e.level}"><span class="lt">{ago(e.ts)}</span><span class="ls">{e.stage}</span><span class="lm">{e.msg}</span></div>
                  {/each}
                {/if}
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    </section>

    <!-- 6 · RETRIEVAL FUNNEL (most recent answer) -->
    <section class="cpanel">
      <header class="ph">RETRIEVAL FUNNEL <i>· latest answer</i></header>
      {#if !recent0}
        <div class="muted sm">No answers yet.</div>
      {:else}
        {@const fz = funnel(recent0)}
        <div class="funnel">
          {#each fz as r}
            <div class="fn-row">
              <span class="fn-lab">{r.lab}</span>
              <div class="fn-track"><i style="width:{r.pct}%; background:{r.c}"></i></div>
              <span class="fn-val">{r.v}</span>
            </div>
          {/each}
        </div>
        <div class="fn-q">{recent0.q || '(question unavailable)'}</div>
      {/if}
    </section>

    <!-- 5 · LIVE ANSWERS (wide) -->
    <section class="cpanel wide tall">
      <header class="ph">LIVE ANSWERS <i>· {answers.length}</i></header>
      {#if answers.length === 0}
        <div class="muted sm">No answers streamed yet — ask Aria a few questions.</div>
      {:else}
        <div class="ans-feed">
          {#each answers as a}
            <div class="ans-row">
              <div class="ans-q">{a.q || '(question unavailable)'}</div>
              <div class="ans-meta">
                {#if a.cache_hit}<span class="tag cache">cache</span>{:else}<span class="tag live">{a.mode}</span>{/if}
                {#if a.blind}<span class="tag blind">blind</span>{/if}
                <span class="ans-fn">{a.scanned}→{a.pool}→{a.reranked}→{a.cited}</span>
                <span class="ans-ms">{ms(a.ms)}</span>
                <span class="ans-ago">{ago(a.ts)}</span>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </section>
  </div>
{/if}

<style>
  .pad { padding: 40px 4px; }
  .muted { color: var(--muted); }
  .sm { font-size: 12.5px; }

  .cockpit {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 13px;
    transition: box-shadow .4s;
  }
  .cpanel {
    background: #fff; border: 1px solid var(--border); border-radius: 13px;
    padding: 14px 15px; min-height: 120px; min-width: 0;
  }
  .cpanel.wide { grid-column: span 2; }
  .cpanel.tall { min-height: 240px; }
  @media (max-width: 1000px) {
    .cockpit { grid-template-columns: 1fr 1fr; }
    .cpanel.wide { grid-column: span 2; }
  }
  @media (max-width: 640px) {
    .cockpit { grid-template-columns: 1fr; }
    .cpanel.wide { grid-column: span 1; }
  }

  .ph {
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    color: var(--muted); font-weight: 700; display: flex; align-items: center; gap: 7px;
    margin-bottom: 12px;
  }
  .ph i { font-style: normal; font-weight: 500; opacity: .8; text-transform: none; letter-spacing: 0; }
  .livedot {
    width: 7px; height: 7px; border-radius: 50%; background: #3f8f5f; flex: none;
    box-shadow: 0 0 0 0 rgba(63,143,95,.55); animation: lvping 2s ease-out infinite;
  }
  @keyframes lvping { 0% { box-shadow: 0 0 0 0 rgba(63,143,95,.5); } 70%,100% { box-shadow: 0 0 0 5px rgba(63,143,95,0); } }
  @media (prefers-reduced-motion: reduce) { .livedot { animation: none; } }

  .metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
  .metric b { display: block; font-size: 21px; font-weight: 700; color: var(--ink); line-height: 1; font-variant-numeric: tabular-nums; }
  .metric b.warn { color: #c98a2e; }
  .metric b.bad { color: #c0492f; }
  .metric span { font-size: 11.5px; color: var(--muted); margin-top: 4px; display: block; }

  .ok-line { font-size: 13px; color: #3f8f5f; font-weight: 600; padding: 6px 0; }
  .err-list { display: flex; flex-direction: column; gap: 7px; }
  .err-row { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: #c0492f; font-weight: 500; }
  .err-row.warn { color: #c98a2e; }
  .err-row .ed { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }

  /* ingest */
  .ingest-list { display: flex; flex-direction: column; gap: 9px; max-height: 360px; overflow-y: auto; }
  .ing-row { text-align: left; width: 100%; background: var(--sand); border: 1px solid var(--border); border-radius: 9px; padding: 9px 11px; cursor: pointer; transition: background .12s; }
  .ing-row:hover { background: var(--hover, #efefec); }
  .ing-row.open { background: #fff; border-color: #cfcdc7; }
  .ing-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 7px; }
  .ing-name { font-size: 12.5px; font-weight: 600; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ing-stage { font-size: 11px; font-weight: 600; white-space: nowrap; text-transform: capitalize; }
  .st-processing { color: #c98a2e; } .st-queued { color: #3f7fb0; } .st-failed { color: #c0492f; }
  .pbar { height: 6px; border-radius: 99px; background: var(--cream, #faf9f5); overflow: hidden; }
  .pf { display: block; height: 100%; border-radius: 99px; background: #c98a2e; transition: width .4s; }
  .pf.st-queued { background: #3f7fb0; } .pf.st-failed { background: #c0492f; }
  .ing-log { background: #1f1e1d; border-radius: 9px; padding: 9px 11px; margin: 2px 0 4px; max-height: 200px; overflow-y: auto; font-family: ui-monospace, monospace; }
  .logline { display: flex; gap: 9px; font-size: 11px; color: #cdc9c2; padding: 1.5px 0; line-height: 1.4; }
  .logline .lt { color: #7d7a73; flex: none; width: 34px; }
  .logline .ls { color: #c98a2e; flex: none; width: 70px; text-transform: capitalize; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .logline .lm { color: #e6e2da; min-width: 0; }
  .logline.lv-error .lm { color: #f0a08c; }
  .logline.lv-warn .lm { color: #e7c98a; }

  /* funnel */
  .funnel { display: flex; flex-direction: column; gap: 9px; }
  .fn-row { display: grid; grid-template-columns: 64px 1fr 34px; align-items: center; gap: 9px; }
  .fn-lab { font-size: 11.5px; color: var(--muted); text-transform: capitalize; }
  .fn-track { height: 14px; border-radius: 99px; background: var(--sand); overflow: hidden; }
  .fn-track i { display: block; height: 100%; border-radius: 99px; transition: width .5s; }
  .fn-val { font-size: 12.5px; font-weight: 700; color: var(--ink); text-align: right; font-variant-numeric: tabular-nums; }
  .fn-q { margin-top: 12px; font-size: 12px; color: var(--muted); line-height: 1.45; border-top: 1px solid var(--line, var(--border)); padding-top: 10px; }

  /* live answers */
  .ans-feed { display: flex; flex-direction: column; gap: 0; max-height: 420px; overflow-y: auto; }
  .ans-row { padding: 9px 0; border-bottom: 1px solid var(--line, var(--border)); }
  .ans-row:last-child { border-bottom: none; }
  .ans-q { font-size: 13px; color: var(--ink); line-height: 1.4; margin-bottom: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ans-meta { display: flex; align-items: center; gap: 9px; font-size: 11.5px; color: var(--muted); flex-wrap: wrap; }
  .tag { font-size: 10px; font-weight: 700; padding: 1.5px 7px; border-radius: 99px; text-transform: uppercase; letter-spacing: .03em; }
  .tag.cache { background: #e6f1ea; color: #3f8f5f; }
  .tag.live { background: #eef3f9; color: #3f7fb0; }
  .tag.blind { background: #f6e1da; color: #c0492f; }
  .ans-fn { font-family: ui-monospace, monospace; font-size: 11px; color: var(--muted); }
  .ans-ms { font-weight: 700; color: var(--ink); }
  .ans-ago { margin-left: auto; }
</style>
