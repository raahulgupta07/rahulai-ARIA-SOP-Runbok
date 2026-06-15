<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { range, tick } from '$lib/dashstore';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived(me?.role === 'admin');
  let p = $state<any>(null);
  let vf = $state<any>(null);          // citation-accuracy / trust report
  let running = $state(false);

  function load(days: number) {
    api.analyticsPerf(days).then((r) => (p = r)).catch(() => {});
    api.analyticsVerify(days).then((r) => (vf = r)).catch(() => {});
  }
  async function runVerify() {
    running = true;
    try { await api.analyticsVerifyRun(10); load($range); } finally { running = false; }
  }
  $effect(() => { $tick; if (isAdmin) load($range); });

  function ms(v: number) { return v == null ? '—' : v >= 1000 ? (v / 1000).toFixed(1) + 's' : Math.round(v) + 'ms'; }
  function money(v: number) { return '$' + (v ?? 0).toFixed(v < 1 ? 4 : 2); }
  function shortModel(m: string) { return (m || '—').split('/').pop()!.replace(/:.*/, ''); }
  let k = $derived(p?.kpis);
  let latTrend = $derived((p?.trend || []).map((t: any) => ({ label: t.label, n: t.p50_ms })));
  let costTrend = $derived((p?.trend || []).map((t: any) => ({ label: t.label, n: Math.round((t.cost || 0) * 10000) })));
  // hit-rate colour
  function hueOk(pct: number) { return pct >= 80 ? '#3f8f5f' : pct >= 55 ? '#c98a2e' : '#c0492f'; }
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if !p || (k?.answers ?? 0) === 0}
  <div class="muted pad">No answer telemetry yet — ask Aria a few questions, then refresh.</div>
{:else}
  {@const _maxLat = Math.max(1, ...latTrend.map((t: any) => t.n || 0))}
  <div class="hero green">
    <div class="hero-main">
      <div class="hero-num">{k.p50_total_ms != null ? (k.p50_total_ms / 1000).toFixed(1) : '—'}<small>s</small></div>
      <div class="hero-lbl">median answer time (p50, lower is better)</div>
      <div class="hero-delta">{(k.answers ?? 0).toLocaleString()} answers · {p.days ?? '—'}d window</div>
    </div>
    {#if latTrend.length}
      <div class="hero-spark">
        <div class="csspark">
          {#each latTrend as d}<i style="height:{Math.max(8, ((d.n || 0) / _maxLat) * 100)}%"></i>{/each}
        </div>
      </div>
    {/if}
    <div class="hero-side">
      <div class="hs"><b>{ms(k.p95_total_ms)}</b>p95 latency</div>
      <div class="hs"><b>{money(k.cost_per_answer)}</b>cost / answer</div>
      <div class="hs"><b>{k.hit_rate ?? '—'}%</b>retrieval hit rate</div>
    </div>
  </div>

  <!-- KPI cards -->
  <div class="exgrid">
    <div class="excard">
      <div class="exh">Speed · time to first token</div>
      <div class="exbig">{ms(k.p50_first_ms)}<span class="exden">p50</span></div>
      <div class="exsub">p95 {ms(k.p95_first_ms)}</div>
      <div class="exrow"><span>full answer p50 {ms(k.p50_total_ms)}</span><span>p95 {ms(k.p95_total_ms)}</span></div>
    </div>
    <div class="excard val">
      <div class="exh">Cost · real tokens</div>
      <div class="exbig">{money(k.cost_total)}<span class="exden">{p.days}d</span></div>
      <div class="exsub">{money(k.cost_per_answer)} / answer</div>
      <div class="exrow"><span>{k.tok_total.toLocaleString()} tok</span><span>{k.avg_tok_in}+{k.avg_tok_out} avg</span></div>
    </div>
    <div class="excard">
      <div class="exh">Retrieval · grounding</div>
      <div class="exbig" style="color:{hueOk(k.hit_rate)}">{k.hit_rate}%<span class="exden">hit rate</span></div>
      <div class="exsub">{k.avg_cited} cited · {k.avg_seed} retrieved avg</div>
      <div class="exrow"><span>wider-retry {k.wider_rate}%</span><span>blind {k.blind_rate}%</span></div>
    </div>
    <div class="excard">
      <div class="exh">Citation trust <span class="exmini" style="float:right">verify pass</span></div>
      {#if vf && vf.kpis.scored > 0}
        <div class="exbig" style="color:{hueOk(vf.kpis.avg_score)}">{vf.kpis.avg_score}%<span class="exden">backed</span></div>
        <div class="exsub">{vf.kpis.support_rate}% pages support · {vf.kpis.refute_rate}% refute</div>
        <div class="exrow"><span>{vf.kpis.scored} checked</span><span>{vf.kpis.weak} weak</span></div>
        {#if vf.kpis.pending}<div class="exmini">{vf.kpis.pending} unverified · <button class="lnk" disabled={running} onclick={runVerify}>{running ? 'verifying…' : 'verify now'}</button></div>{/if}
      {:else}
        <div class="exbig" style="color:var(--muted); font-size:20px; padding-top:6px">—</div>
        <div class="exsub">no answers verified yet</div>
        <div class="exmini"><button class="lnk" disabled={running} onclick={runVerify}>{running ? 'verifying…' : 'run verify pass'}</button>{#if vf?.kpis?.pending} · {vf.kpis.pending} pending{/if}</div>
      {/if}
    </div>
    <div class="excard">
      <div class="exh">Throughput</div>
      <div class="exbig">{k.answers}<span class="exden">answers</span></div>
      <div class="exsub">{k.users} active users</div>
      <div class="exmini">{p.days}-day window</div>
    </div>
  </div>

  <!-- weakest answers by citation trust -->
  {#if vf?.weak?.length}
    <div class="excard mt">
      <div class="exh">Weakest citations <span class="exmini" style="float:right">cited pages that don't back the claim · ids only</span></div>
      <table class="ptab">
        <thead><tr><th>When</th><th>Msg</th><th>Trust</th><th>Cited</th><th>Why flagged</th></tr></thead>
        <tbody>
          {#each vf.weak as w}
            <tr>
              <td class="muted">{w.at}</td>
              <td class="muted">#{w.message_id}</td>
              <td><b style="color:{hueOk(w.verify_score)}">{w.verify_score}%</b></td>
              <td>{w.cited_n}</td>
              <td class="muted">{w.why || '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- charts -->
  <div class="pcharts">
    <div class="excard">
      <div class="exh">Median latency / day</div>
      {#if latTrend?.length}
        <EChart option={areaOpt(latTrend, { yKey: 'n', color: C.violet })} height={150} />
      {/if}
      <div class="exmini">area height = p50 answer time (ms)</div>
    </div>
    <div class="excard">
      <div class="exh">Spend / day</div>
      {#if costTrend?.length}
        <EChart option={areaOpt(costTrend, { yKey: 'n', color: C.amber })} height={150} />
      {/if}
      <div class="exmini">area height = daily LLM cost (×0.0001 $)</div>
    </div>
  </div>

  <!-- model split -->
  {#if p.models?.length}
    <div class="excard mt">
      <div class="exh">Models</div>
      <table class="ptab">
        <thead><tr><th>Model</th><th>Answers</th><th>Avg latency</th><th>Cost</th></tr></thead>
        <tbody>
          {#each p.models as m}
            <tr><td><b>{shortModel(m.model)}</b></td><td>{m.n}</td><td>{ms(m.avg_ms)}</td><td>{money(m.cost)}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- slowest answers (no chat text — ids + timings only) -->
  {#if p.slowest?.length}
    <div class="excard mt">
      <div class="exh">Slowest answers <span class="exmini" style="float:right">message id · timings only, no chat text</span></div>
      <table class="ptab">
        <thead><tr><th>When</th><th>Msg</th><th>Total</th><th>First tok</th><th>Out tok</th><th>Cited</th></tr></thead>
        <tbody>
          {#each p.slowest as s}
            <tr>
              <td>{s.at}</td>
              <td class="muted">#{s.message_id ?? '—'}</td>
              <td><b>{ms(s.ms_total)}</b></td>
              <td>{ms(s.ms_first_token)}</td>
              <td>{s.tok_out ?? '—'}</td>
              <td>{s.blind ? '⚠ blind' : s.cited_n}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}

<style>
  .pcharts{display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px;}
  @media (max-width:760px){ .pcharts{grid-template-columns:1fr;} }
  .mt{margin-top:14px;}
  .ptab{width:100%; border-collapse:collapse; font-size:13px; margin-top:8px;}
  .ptab th{text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; padding:6px 8px; border-bottom:1px solid var(--line);}
  .ptab td{padding:7px 8px; border-bottom:1px solid var(--line); color:var(--ink);}
  .ptab tr:last-child td{border-bottom:none;}
  .ptab .muted{color:var(--muted);}
  .lnk{background:none; border:none; color:var(--clay); font:inherit; cursor:pointer; padding:0; text-decoration:underline;}
  .lnk:disabled{color:var(--muted); cursor:default; text-decoration:none;}
</style>
