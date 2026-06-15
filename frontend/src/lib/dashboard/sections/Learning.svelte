<script lang="ts">
  import { api } from '$lib/api';
  import { range, tick } from '$lib/dashstore';
  import { ago } from '$lib/dashutil';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, funnelOpt, hbarOpt, C } from '$lib/charts';

  let d = $state<any>(null);
  $effect(() => { $tick; api.analyticsLearningOverview($range).then((r) => (d = r)).catch(() => {}); });

  let perDay = $derived((d?.per_day ?? []).map((x: any) => ({ label: x.label, n: (x.auto || 0) + (x.hand || 0) })));
  let growth = $derived((() => {
    let run = 0;
    return (d?.per_day ?? []).map((x: any) => {
      run += (x.auto || 0) + (x.hand || 0);
      return { label: x.label, n: run };
    });
  })());
  let funnel = $derived(d ? [
    { name: 'Extracted', value: d.funnel.extracted },
    { name: 'Pending', value: d.funnel.pending },
    { name: 'Approved', value: d.funnel.approved },
    { name: 'Cited', value: d.funnel.cited },
  ].filter((s) => s.value > 0) : []);
  let topFacts = $derived((d?.top_facts ?? []).slice(0, 8).map((t: any) => ({ label: t.label, value: t.cited_count })));
  let confMax = $derived(d ? Math.max(1, d.confidence.lo, d.confidence.mid, d.confidence.hi, d.confidence.top) : 1);

  function statusPill(s: string) {
    if (s === 'pending') return { t: 'pending', bg: '#fbf1df', c: '#a9742a' };
    if (s === 'active') return { t: 'active', bg: '#eef3ef', c: '#3f8f5f' };
    return { t: s, bg: '#f0efed', c: '#8b8b85' };
  }
</script>

{#if !d}
  <div class="muted pad">Loading learning…</div>
{:else}
  <!-- hero: how much Aria has learned -->
  <div class="hero">
    <div class="hero-main">
      <div class="hero-num">{d.totals.auto}</div>
      <div class="hero-lbl">facts auto-learned from chat</div>
      <div class="hero-delta up">{d.totals.pending} pending review · {d.totals.active} active</div>
    </div>
    <div class="hero-spark">
      {#if perDay.length}
        {@const mx = Math.max(...perDay.map((p) => p.n), 1)}
        <div class="csspark">{#each perDay.slice(-24) as p}<i style="height:{Math.max(8, (p.n / mx) * 100)}%"></i>{/each}</div>
      {/if}
    </div>
    <div class="hero-side">
      <div class="hs"><b>{d.totals.hand}</b>hand-taught</div>
      <div class="hs"><b>{d.totals.citations}</b>citations</div>
      <div class="hs"><b>{d.totals.avg_conf ? Math.round(d.totals.avg_conf * 100) + '%' : '—'}</b>avg confidence</div>
    </div>
  </div>

  <!-- charts -->
  <div class="bento">
    <div class="bt w2">
      <div class="ctitle">Learned over time <span class="cnote">facts/day · last {d.days}d</span></div>
      {#if perDay.length}<EChart option={areaOpt(perDay, { yKey: 'n', color: C.violet })} height={170} />
      {:else}<div class="muted sm">No facts yet.</div>{/if}
    </div>
    <div class="bt w2">
      <div class="ctitle">Knowledge growth <span class="cnote">cumulative facts · last {d.days}d</span></div>
      {#if growth.length}<EChart option={areaOpt(growth, { yKey: 'n', color: C.teal })} height={170} />
      {:else}<div class="muted sm">No facts yet.</div>{/if}
    </div>
    <div class="bt w2">
      <div class="ctitle">Learning funnel</div>
      {#if funnel.length}<EChart option={funnelOpt(funnel)} height={170} />
      {:else}<div class="muted sm">No facts yet.</div>{/if}
    </div>

    <div class="bt w2">
      <div class="ctitle">Auto-extraction confidence</div>
      <div class="cbars">
        {#each [['<70%', d.confidence.lo, '#c98a2e'], ['70–90%', d.confidence.mid, '#7b6bd6'], ['90–95%', d.confidence.hi, '#3f7fb0'], ['≥95% (auto-approve)', d.confidence.top, '#3f8f5f']] as [lab, n, col]}
          <div class="cbar">
            <span class="cbl">{lab}</span>
            <span class="ctrack"><span class="cfill" style="width:{(n / confMax) * 100}%; background:{col}"></span></span>
            <span class="cnum">{n}</span>
          </div>
        {/each}
      </div>
      <div class="cnote2">≥95% from an admin auto-activates; everything else waits for review.</div>
    </div>
    <div class="bt w2">
      <div class="ctitle">Most-cited facts <span class="cnote">shaped the most answers</span></div>
      {#if topFacts.length}<EChart option={hbarOpt(topFacts, { color: C.teal })} height={170} />
      {:else}<div class="muted sm">No cited facts yet.</div>{/if}
    </div>
  </div>

  <!-- recent learned feed -->
  <div class="card" style="margin-top:13px">
    <div class="ctitle">Recently learned <span class="cnote">newest first</span></div>
    {#if d.recent?.length}
      <div class="lfeed">
        {#each d.recent as f (f.id)}
          {@const sp = statusPill(f.status)}
          <div class="lrow">
            <div class="lmain">
              <div class="lval">{f.value}</div>
              {#if f.origin}<div class="lorigin">from: "{f.origin}"</div>{/if}
            </div>
            <div class="lmeta">
              {#if f.auto}<span class="lchip auto">AUTO{f.confidence != null ? ' ' + Math.round(f.confidence * 100) + '%' : ''}</span>
              {:else}<span class="lchip hand">hand</span>{/if}
              <span class="lchip" style="background:{sp.bg}; color:{sp.c}">{sp.t}</span>
              <span class="ltime">{ago(f.created_at)}</span>
            </div>
          </div>
        {/each}
      </div>
    {:else}<div class="muted sm">Nothing learned yet.</div>{/if}
  </div>
{/if}

<style>
  .cbars { display: flex; flex-direction: column; gap: 9px; }
  .cbar { display: flex; align-items: center; gap: 10px; font-size: 12px; }
  .cbl { width: 130px; flex: none; color: var(--ink); }
  .ctrack { flex: 1; height: 8px; background: var(--sand); border-radius: 5px; overflow: hidden; }
  .cfill { display: block; height: 100%; border-radius: 5px; }
  .cnum { width: 28px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }

  .lfeed { display: flex; flex-direction: column; }
  .lrow { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--border); }
  .lrow:last-child { border-bottom: 0; }
  .lmain { min-width: 0; flex: 1; }
  .lval { font-size: 13px; color: var(--ink); line-height: 1.45; }
  .lorigin { font-size: 11px; color: var(--muted); margin-top: 3px; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lmeta { display: flex; align-items: center; gap: 6px; flex: none; }
  .lchip { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; padding: 2px 7px; border-radius: 6px; white-space: nowrap; }
  .lchip.auto { background: #eceaf4; color: #5a4f86; }
  .lchip.hand { background: #f0efed; color: #6b6760; }
  .ltime { font-size: 11px; color: var(--muted); white-space: nowrap; }
</style>
