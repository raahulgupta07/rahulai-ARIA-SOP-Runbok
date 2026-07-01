<script lang="ts">
  import { api } from '$lib/api';
  import { goto } from '$app/navigation';
  import { auth, type User } from '$lib/auth';
  import { cleanText } from '$lib/docname';
  import { openConvId } from '$lib/chatstore';
  import { range, tick, navInsight } from '$lib/dashstore';
  import { ago } from '$lib/dashutil';
  import Bars from '$lib/Bars.svelte';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, donutOpt, heatmapOpt, C } from '$lib/charts';

  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));

  // Admin cockpit data (new endpoint)
  let c = $state<any>(null);
  let vit = $state<any>(null);       // live system vitals (Mission Control)
  let heat = $state<any>(null);      // activity heatmap

  // Personal dashboard data (non-admin)
  let pdata = $state<any>(null);

  let booted = false;
  function fmtBytes(n: number) {
    if (!n) return '0';
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
  }

  $effect(() => {
    if (!me) {
      auth.me().then((u) => { me = u; }).catch(() => {});
    }
  });

  $effect(() => {
    $tick;                       // live heartbeat → silent re-fetch
    const days = $range;
    if (!booted) booted = true;
    // NB: don't null the data on refresh — keep the old values on screen so the
    // charts tween to the new numbers instead of flashing an empty state.
    if (isAdmin) {
      api.dashboardCockpit(days).then((r) => (c = r)).catch(() => {});
      api.dashboardVitals().then((r) => (vit = r)).catch(() => {});
      api.activityHeatmap(days).then((r) => (heat = r)).catch(() => {});
    } else if (me) {
      api.dashboardMe(days).then((r) => (pdata = r)).catch(() => {});
    }
  });

  function openChat(id: number) { if (id) openConvId(id); goto('/'); }

  // Alert severity colors
  const SEV_COLOR: Record<string, { bg: string; text: string; border: string }> = {
    info:  { bg: '#e8f0f7', text: '#3f7fb0', border: '#3f7fb0' },
    warn:  { bg: '#fdf3e3', text: '#c98a2e', border: '#c98a2e' },
    alert: { bg: '#fce8e8', text: '#c0492f', border: '#c0492f' },
  };
  function sevStyle(sev: string) {
    const s = SEV_COLOR[sev] ?? SEV_COLOR.info;
    return `background:${s.bg};color:${s.text};border:1px solid ${s.border};`;
  }

  // ---- AI admin (ops) agent ----
  let opsQ = $state('');
  let opsA = $state('');
  let opsBusy = $state(false);
  const OPS_SUGGEST = [
    'How many blind spots this week?',
    'Which documents are cold / never used?',
    'How is answer accuracy trending?',
    'What should I fix first?'
  ];
  async function askOps(q?: string) {
    const question = (q ?? opsQ).trim();
    if (!question || opsBusy) return;
    opsQ = question; opsBusy = true; opsA = '';
    try { opsA = (await api.opsAsk(question, $range)).answer; }
    catch (e: any) { opsA = 'Could not answer: ' + (e?.message || 'error'); }
    finally { opsBusy = false; }
  }
</script>

<!-- ====================== ADMIN COCKPIT ====================== -->
{#if isAdmin}
  {#if !c}
    <div class="muted pad">Loading cockpit…</div>
  {:else}
    <!-- 0. HERO — coverage is the one number that matters -->
    {@const cov = c.rings?.coverage ?? 0}
    {@const vol = (c.trends?.volume ?? []).slice(-24)}
    {@const vmax = Math.max(...vol.map((d: any) => d.n ?? 0), 1)}
    <div class="hero teal">
      <div class="hero-main">
        <div class="hero-num">{cov}<small>%</small></div>
        <div class="hero-lbl">answers grounded in a cited source</div>
        <div class="hero-delta up">live · {c.kpis?.questions ?? 0} answered this period</div>
      </div>
      <div class="hero-spark">
        {#if vol.length}
          <div class="csspark">
            {#each vol as d}<i style="height:{Math.max(8, ((d.n ?? 0) / vmax) * 100)}%"></i>{/each}
          </div>
        {/if}
      </div>
      <div class="hero-side">
        <div class="hs"><b>{c.kpis?.users_active ?? '—'}</b>active users</div>
        <div class="hs"><b>${c.kpis?.cost_total != null ? c.kpis.cost_total.toFixed(2) : '—'}</b>spend</div>
        <div class="hs"><b>{c.kpis?.hours_saved != null ? c.kpis.hours_saved.toFixed(0) : '—'}h</b>saved</div>
      </div>
    </div>

    <!-- 0b. ASK OPS — AI admin agent -->
    <div class="ops">
      <div class="ops-bar">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="color:var(--muted)"><circle cx="11" cy="11" r="7"/><path d="m21 21-4-4"/></svg>
        <input class="ops-in" bind:value={opsQ} placeholder="Ask about your knowledge ops — blind spots, cold docs, accuracy…"
               onkeydown={(e) => { if (e.key === 'Enter') askOps(); }} />
        <button class="ops-go" onclick={() => askOps()} disabled={opsBusy || !opsQ.trim()}>{opsBusy ? '…' : 'Ask'}</button>
      </div>
      {#if !opsA && !opsBusy}
        <div class="ops-sugg">{#each OPS_SUGGEST as s}<button class="ops-chip" onclick={() => askOps(s)}>{s}</button>{/each}</div>
      {/if}
      {#if opsBusy}<div class="ops-ans muted">Analyzing live metrics…</div>{/if}
      {#if opsA}<div class="ops-ans">{opsA}</div>{/if}
    </div>

    <!-- 1. ALERTS STRIP -->
    <div class="alert-strip">
      {#if c.alerts?.length}
        <div class="alerts-row">
          {#each c.alerts as a}
            <button
              class="alert-chip"
              class:pulse={a.severity === 'alert'}
              style={sevStyle(a.severity)}
              onclick={() => navInsight(a.href)}
            >
              <span class="chip-label">{a.label}</span>
              {#if a.count > 0}
                <span class="chip-count">{a.count}</span>
              {/if}
            </button>
          {/each}
        </div>
      {:else}
        <div class="all-clear">
          <span class="all-clear-icon">✓</span>
          <span>All clear — nothing needs attention</span>
        </div>
      {/if}
    </div>

    <!-- 2. HEALTH RINGS -->
    {#if c.rings}
      <div class="rings-row">
        <div class="ring-cell">
          <EChart option={donutOpt(c.rings.coverage, { color: C.teal, label: 'Coverage' })} height={120} />
          <div class="ring-label">Coverage</div>
        </div>
        <div class="ring-cell">
          <EChart option={donutOpt(c.rings.trust, { color: C.violet, label: 'Trust' })} height={120} />
          <div class="ring-label">Citation trust</div>
          {#if c.rings.trust == null}
            <div class="ring-note muted sm">run verify</div>
          {/if}
        </div>
        <div class="ring-cell">
          <EChart option={donutOpt(c.rings.helpful, { color: C.blue, label: 'Helpful', suffix: '%' })} height={120} />
          <div class="ring-label">Helpful</div>
        </div>
        <div class="ring-cell">
          <EChart option={donutOpt(c.rings.ingest_health, { color: C.green, label: 'Ingest' })} height={120} />
          <div class="ring-label">Ingest health</div>
        </div>
      </div>
    {/if}

    <!-- 3. KPI TILES -->
    <div class="kpi-row">
      <!-- Active users -->
      <button class="kpi-tile" onclick={() => navInsight('/dashboard/users')}>
        <div class="kpi-val">{c.kpis?.users_active ?? '—'}<span class="kpi-of">/{c.kpis?.users_total ?? '—'}</span></div>
        <div class="kpi-label">Active users</div>
        <div class="kpi-note muted sm">of total registered</div>
      </button>
      <!-- Questions -->
      <button class="kpi-tile" onclick={() => navInsight('/dashboard/exec')}>
        <div class="kpi-val">{c.kpis?.questions ?? '—'}</div>
        <div class="kpi-label">Questions</div>
        {#if c.kpis?.questions_today != null}
          <div class="kpi-note muted sm">{c.kpis.questions_today} today</div>
        {/if}
      </button>
      <!-- Cost -->
      <button class="kpi-tile" onclick={() => navInsight('/dashboard/exec')}>
        <div class="kpi-val">${c.kpis?.cost_total != null ? c.kpis.cost_total.toFixed(2) : '—'}</div>
        <div class="kpi-label">LLM cost</div>
        <div class="kpi-note muted sm">last {c.days ?? $range} days</div>
      </button>
      <!-- Value -->
      <button class="kpi-tile" onclick={() => navInsight('/dashboard/exec')}>
        <div class="kpi-val">{c.kpis?.hours_saved != null ? c.kpis.hours_saved.toFixed(1) : '—'}<span class="kpi-unit">h</span></div>
        <div class="kpi-label">Hours saved</div>
        <div class="kpi-note muted sm">estimated ROI</div>
      </button>
    </div>

    <!-- 4. TREND CHARTS -->
    <div class="trends-row">
      <!-- Volume area chart -->
      <div class="card trend-card">
        <div class="ctitle">Question volume <span class="cnote">last {c.days ?? $range} days</span></div>
        {#if c.trends?.volume?.length}
          <EChart option={areaOpt(c.trends.volume, { yKey: 'n', color: C.blue })} height={150} />
        {:else}
          <div class="muted sm">No data yet.</div>
        {/if}
      </div>

      <!-- Cost area chart -->
      <div class="card trend-card">
        <div class="ctitle">Daily cost <span class="cnote">USD</span></div>
        {#if c.trends?.cost?.length}
          <EChart option={areaOpt(c.trends.cost, { yKey: 'v', color: C.amber })} height={150} />
        {:else}
          <div class="muted sm">No data yet.</div>
        {/if}
      </div>
    </div>

    <!-- 5. ACTIVITY HEATMAP + SYSTEM VITALS -->
    <div class="mc-row">
      <div class="card mc-heat">
        <div class="ctitle">Activity rhythm <span class="cnote">questions · weekday × hour · last {c.days ?? $range}d</span></div>
        {#if heat?.grid?.length}
          <EChart option={heatmapOpt(heat.grid, heat.max)} height={184} />
        {:else}
          <div class="muted sm">No activity yet.</div>
        {/if}
      </div>
      <div class="card mc-vit">
        <div class="ctitle">System vitals</div>
        {#if vit}
          <div class="vrow"><span>Database</span><b>{fmtBytes(vit.db_bytes)}</b></div>
          <div class="vrow"><span>Ingest queue</span><b class:warn={vit.ingest_queue > 0}>{vit.ingest_queue}</b></div>
          {#if vit.ingest_stuck}<div class="vrow"><span>Stuck</span><b class="bad">{vit.ingest_stuck}</b></div>{/if}
          {#if vit.ingest_failed}<div class="vrow"><span>Failed</span><b class="bad">{vit.ingest_failed}</b></div>{/if}
          <div class="vsep"></div>
          {#each vit.daemons as d}
            <div class="vrow vdaemon">
              <span class="ddot" class:off={!d.enabled} class:stale={d.stale}></span>
              <span class="dname">{d.name}</span>
              <span class="dstat">{d.enabled ? (d.stale ? 'stale' : 'ok') : 'off'}</span>
            </div>
          {/each}
        {:else}
          <div class="muted sm">Loading…</div>
        {/if}
      </div>
    </div>
  {/if}


<!-- ====================== USER PERSONAL ====================== -->
{:else if pdata}
  {@const k = pdata.kpis}
  <div class="kstrip">
    <div class="kcell"><div class="kcv">{k.questions}</div><div class="kl">Questions</div></div>
    <div class="kcell"><div class="kcv">{k.chats}</div><div class="kl">Chats</div></div>
    <div class="kcell"><div class="kcv">{k.sourced_pct}%</div><div class="kl">Sourced</div></div>
    <div class="kcell"><div class="kcv">{k.helpful_pct}%</div><div class="kl">Helpful</div></div>
  </div>
  <div class="card">
    <div class="ctitle">Your activity <span class="cnote">last {$range} days</span></div>
    <Bars data={pdata.activity} />
  </div>
  <div class="fw2">
    <div class="card">
      <div class="ctitle">Recent questions</div>
      {#if pdata.recent?.length}
        <div class="qlist">
          {#each pdata.recent as r}
            <button class="qrow" onclick={() => openChat(r.conversation_id)}>
              <span class="qq">{cleanText(r.q)}</span>
              <span class="qmeta">
                {#if r.source}<span class="qsrc">{r.source}</span>{/if}
                <span class="qtime">{ago(r.created_at)}</span>
              </span>
            </button>
          {/each}
        </div>
      {:else}<div class="muted sm">No questions yet.</div>{/if}
    </div>
    <div class="card">
      <div class="ctitle">Top docs that helped you</div>
      {#if pdata.top_docs?.length}
        <div class="rank">
          {#each pdata.top_docs as d, i}
            <div class="rrow"><span class="ri">{i + 1}</span><span class="rl">{d.label}</span>
              <span class="rb"><span style="width:{(d.hits / Math.max(...pdata.top_docs.map((x: any) => x.hits), 1)) * 100}%"></span></span><span class="rv">{d.hits}×</span></div>
          {/each}
        </div>
      {:else}<div class="muted sm">No cited docs yet.</div>{/if}
    </div>
  </div>
  <div class="card">
    <div class="ctitle">You taught <span class="cnote">{pdata.taught_counts?.active || 0} active · {pdata.taught_counts?.pending || 0} pending</span></div>
    {#if pdata.taught?.length}
      <div class="flist">
        {#each pdata.taught as f}
          <div class="frow2"><span class="ft">{f.value}</span>
            <span class="fmeta">
              {#if f.status === 'pending'}<span class="pill warn">pending</span>{:else}<span class="pill ok">active ✓</span>{/if}
              {#if f.cited_count}<span class="cited">cited {f.cited_count}×</span>{/if}
            </span>
          </div>
        {/each}
      </div>
    {:else}<div class="muted sm">You haven't taught any facts yet.</div>{/if}
  </div>
{:else}
  <div class="muted pad">Loading…</div>
{/if}

<style>
  /* ── ALERT STRIP ── */
  .alert-strip {
    margin-bottom: 20px;
  }
  .alerts-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .alert-chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 9px 16px;
    border-radius: 8px;
    font-size: 0.88rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.15s;
    white-space: nowrap;
  }
  .alert-chip:hover {
    opacity: 0.85;
    transform: translateY(-1px);
  }
  /* entrance pop so the strip doesn't read as a static wall */
  .alert-chip { animation: chipPop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both; }
  /* high-severity (alert) chips breathe to pull the eye */
  .alert-chip.pulse { animation: chipPop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both, chipBreathe 2s ease-in-out 0.4s infinite; }
  @keyframes chipPop { from { opacity: 0; transform: scale(0.86); } to { opacity: 1; transform: none; } }
  @keyframes chipBreathe {
    0%, 100% { box-shadow: 0 0 0 0 rgba(192, 73, 47, 0); }
    50% { box-shadow: 0 0 0 4px rgba(192, 73, 47, 0.18); }
  }
  .chip-count {
    transition: transform 0.2s;
  }
  .alert-chip:hover .chip-count { transform: scale(1.12); }
  @media (prefers-reduced-motion: reduce) {
    .alert-chip, .alert-chip.pulse { animation: none; }
  }
  .chip-label { }
  .chip-count {
    background: rgba(0,0,0,0.12);
    border-radius: 99px;
    padding: 1px 7px;
    font-size: 0.8rem;
  }
  .all-clear {
    display: flex;
    align-items: center;
    gap: 10px;
    background: #e8f5ee;
    border: 1px solid #3f8f5f;
    color: #2a6b45;
    border-radius: 10px;
    padding: 13px 20px;
    font-size: 0.93rem;
    font-weight: 500;
  }
  .all-clear-icon {
    font-size: 1.1rem;
    font-weight: 700;
  }

  /* ── RINGS ROW ── */
  .rings-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .ring-cell {
    flex: 1 1 120px;
    background: #fff;
    border: 1px solid var(--line, #efefec);
    border-radius: 12px;
    padding: 12px 12px 14px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }
  .ring-label {
    font-size: 0.8rem;
    color: var(--muted, #888);
    text-align: center;
    font-weight: 500;
  }
  .ring-note {
    font-size: 0.72rem;
    color: var(--muted, #999);
    text-align: center;
  }

  /* ── KPI TILES ── */
  .kpi-row {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .kpi-tile {
    flex: 1 1 140px;
    background: #fff;
    border: 1px solid var(--line, #efefec);
    border-radius: 12px;
    padding: 18px 16px 14px;
    text-align: left;
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .kpi-tile:hover {
    transform: translateY(-2px);
    box-shadow: none;
  }
  .kpi-val {
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--ink, #1f1e1d);
    line-height: 1.1;
  }
  .kpi-of {
    font-size: 1rem;
    font-weight: 400;
    color: var(--muted, #888);
  }
  .kpi-unit {
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--muted, #888);
    margin-left: 2px;
  }
  .kpi-label {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--ink, #1f1e1d);
    margin-top: 4px;
  }
  .kpi-note {
    font-size: 0.76rem;
    color: var(--muted, #999);
  }

  /* ── TREND CHARTS ── */
  .trends-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 680px) {
    .trends-row { grid-template-columns: 1fr; }
  }
  .trend-card {
    min-height: 160px;
  }

  @keyframes lpulse { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

  /* Ask Ops — AI admin agent */
  .ops { background: #fff; border: 1px solid var(--border); border-radius: 14px; padding: 13px 15px; margin-bottom: 16px; }
  .ops-bar { display: flex; align-items: center; gap: 9px; }
  .ops-in { flex: 1; border: 1px solid var(--border); border-radius: 9px; padding: 9px 12px; font-size: 13.5px; color: var(--ink); background: var(--cream); outline: none; }
  .ops-in:focus { border-color: var(--muted); }
  .ops-go { flex: none; font-size: 13px; font-weight: 600; padding: 9px 16px; border-radius: 9px; background: var(--ink); color: var(--cream); border: none; cursor: pointer; }
  .ops-go:disabled { opacity: .5; cursor: default; }
  .ops-sugg { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
  .ops-chip { font-size: 12px; color: var(--ink); background: var(--sand); border: 1px solid var(--border); border-radius: 99px; padding: 5px 12px; cursor: pointer; }
  .ops-chip:hover { background: var(--hover, #efefec); }
  .ops-ans { margin-top: 11px; font-size: 13.5px; line-height: 1.55; color: var(--ink); white-space: pre-wrap; border-top: 1px solid var(--border); padding-top: 11px; }
  .ops-ans.muted { color: var(--muted); }

  .mc-row {
    display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-top: 16px;
  }
  @media (max-width: 820px) { .mc-row { grid-template-columns: 1fr; } }
  .mc-vit { display: flex; flex-direction: column; }
  .vrow {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; padding: 5px 0; color: var(--ink);
  }
  .vrow b { font-variant-numeric: tabular-nums; }
  .vrow b.warn { color: #c98a2e; }
  .vrow b.bad { color: #c0492f; }
  .vsep { height: 1px; background: var(--border); margin: 7px 0; }
  .vdaemon { gap: 8px; justify-content: flex-start; }
  .ddot { width: 8px; height: 8px; border-radius: 50%; background: #3f8f5f; flex: none; }
  .ddot.off { background: #c9c4b8; }
  .ddot.stale { background: #c0492f; animation: lpulse 1.2s ease-in-out infinite; }
  .dname { flex: 1; color: var(--muted); }
  .dstat { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }
</style>
