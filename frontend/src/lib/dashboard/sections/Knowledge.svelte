<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { range, tick } from '$lib/dashstore';
  import { dmax, INTENT_LABEL } from '$lib/dashutil';
  import { parseDocName } from '$lib/docname';
  import EChart from '$lib/EChart.svelte';
  import { barOpt, hbarOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  let o = $state<any>(null);
  let dp = $state<any>(null);                 // doc performance scorecard
  let cp = $state<any>(null);                 // corpus hygiene (dup/conflict facts)
  let kw = $state<any>(null);                 // topics / keyword demand analysis
  let sort = $state<'cites' | 'helpful' | 'age'>('cites');
  $effect(() => {
    $tick;
    if (!isAdmin) return;
    api.dashboardAdmin($range).then((r) => (o = r)).catch(() => {});
    api.analyticsDocs($range).then((r) => (dp = r)).catch(() => {});
    api.analyticsCorpus().then((r) => (cp = r)).catch(() => {});
    api.dashboardKeywords($range).then((r) => (kw = r)).catch(() => {});
  });
  function title(n: string) { return parseDocName(n).title; }
  function covColor(pct: number | null) {
    if (pct == null) return '#9aa0a6';
    return pct >= 70 ? '#3f8f5f' : pct >= 40 ? '#c98a2e' : '#c0492f';
  }
  let scored = $derived.by(() => {
    const list = [...(dp?.docs || [])];
    if (sort === 'helpful') list.sort((a, b) => (b.helpful_pct ?? -1) - (a.helpful_pct ?? -1));
    else if (sort === 'age') list.sort((a, b) => (b.age_days ?? 0) - (a.age_days ?? 0));
    else list.sort((a, b) => (b.cites_win ?? 0) - (a.cites_win ?? 0) || (b.cites_all ?? 0) - (a.cites_all ?? 0));
    return list;
  });
  function hue(p: number | null) { return p == null ? 'var(--muted)' : p >= 70 ? '#3f8f5f' : p >= 40 ? '#c98a2e' : '#c0492f'; }
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if o}
  <div class="hero teal">
    <div class="hero-main">
      <div class="hero-num">{o.health?.coverage_pct ?? '—'}<small>%</small></div>
      <div class="hero-lbl">answer coverage — questions answered with a cited source</div>
      {#if (o.health?.weak_pages ?? 0) > 0}
        <div class="hero-delta dn">{o.health.weak_pages} weak pages need attention</div>
      {:else}
        <div class="hero-delta up">corpus healthy — no weak pages</div>
      {/if}
    </div>
    <div class="hero-side">
      <div class="hs"><b>{o.health?.docs ?? '—'}</b>documents</div>
      <div class="hs"><b>{o.health?.pages ?? '—'}</b>pages</div>
      <div class="hs"><b>{o.health?.facts_active ?? '—'}</b>active facts</div>
    </div>
  </div>
  <div class="card">
    <div class="ctitle">Knowledge health</div>
    <div class="health">
      <div class="hstat"><div class="hv">{o.health.docs}</div><div class="hl">Documents</div></div>
      <div class="hstat"><div class="hv">{o.health.pages}</div><div class="hl">Pages</div></div>
      <div class="hstat"><div class="hv">{o.health.facts_active}</div><div class="hl">Active facts</div></div>
      <div class="hstat" class:warn={o.health.facts_pending > 0}><div class="hv">{o.health.facts_pending}</div><div class="hl">Pending facts</div></div>
      <div class="hstat" class:warn={o.health.weak_pages > 0}><div class="hv">{o.health.weak_pages}</div><div class="hl">Weak pages</div></div>
      <div class="hstat"><div class="hv">{o.health.coverage_pct}%</div><div class="hl">Answer coverage</div></div>
    </div>
  </div>
  <div class="fw2">
    <div class="card">
      <div class="ctitle">Most-cited documents</div>
      {#if o.top_docs?.length}
        <div class="rank">
          {#each o.top_docs as d, i}
            <div class="rrow"><span class="ri">{i + 1}</span><span class="rl">{d.label}</span>
              <span class="rb"><span style="width:{(d.hits / dmax(o.top_docs, 'hits')) * 100}%"></span></span><span class="rv">{d.hits}×</span></div>
          {/each}
        </div>
      {:else}<div class="muted sm">No citations yet.</div>{/if}
    </div>
    <div class="card">
      <div class="ctitle">Blind spots <span class="cnote">answered, no source</span></div>
      {#if o.blind_spots?.length}
        <div class="blist">
          {#each o.blind_spots as b}
            <div class="brow"><span class="bq">{b.question || b.q || b.text}</span>{#if b.count}<span class="bc">{b.count}×</span>{/if}</div>
          {/each}
        </div>
      {:else}<div class="muted sm">No blind spots — every answer was sourced.</div>{/if}
    </div>
  </div>
  {#if o.stale_facts?.length}
    <div class="card">
      <div class="ctitle">Stale facts <span class="cnote">never cited</span></div>
      <div class="blist">
        {#each o.stale_facts.slice(0, 12) as f}
          <div class="brow"><span class="bq">{f.value || f.text || f.key}</span></div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- ░ document scorecard — which docs earn their keep ░ -->
  {#if dp?.docs?.length}
    <div class="card">
      <div class="ctitle">Document scorecard
        <span class="cnote">{dp.summary.cited_win} active · {dp.summary.orphans} orphan · {dp.summary.cold} cold · {dp.summary.downvoted} downvoted</span>
        <span style="float:right; display:flex; gap:4px">
          <button class="sortb" class:on={sort === 'cites'} onclick={() => (sort = 'cites')}>Most used</button>
          <button class="sortb" class:on={sort === 'helpful'} onclick={() => (sort = 'helpful')}>Helpful</button>
          <button class="sortb" class:on={sort === 'age'} onclick={() => (sort = 'age')}>Oldest</button>
        </span>
      </div>
      <table class="dtab">
        <thead><tr><th>Document</th><th>Used</th><th>Helpful</th><th>Up/Down</th><th>Indexed</th><th>Age</th><th>Last cited</th></tr></thead>
        <tbody>
          {#each scored.slice(0, 30) as d}
            <tr class:dead={(d.cites_all || 0) === 0}>
              <td class="dname">{title(d.name)}{#if (d.cites_all || 0) === 0}<span class="tag orph">orphan</span>{:else if (d.cites_win || 0) === 0}<span class="tag cold">cold</span>{/if}{#if d.status !== 'ready'}<span class="tag warn">{d.status}</span>{/if}</td>
              <td><b>{d.cites_win}</b>{#if d.cites_all !== d.cites_win}<span class="cnote"> /{d.cites_all}</span>{/if}</td>
              <td style="color:{hue(d.helpful_pct)}">{d.helpful_pct == null ? '—' : d.helpful_pct + '%'}</td>
              <td class="muted">{d.up}/{d.down}</td>
              <td class="muted">{d.indexed_pct}%</td>
              <td class="muted">{d.age_days}d</td>
              <td class="muted">{d.last_cited_days == null ? 'never' : d.last_cited_days === 0 ? 'today' : d.last_cited_days + 'd ago'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="fw2">
      <div class="card">
        <div class="ctitle"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Orphan documents <span class="cnote">never cited — dead weight, consider removing</span></div>
        {#if dp.orphans.length}
          <div class="blist">
            {#each dp.orphans.slice(0, 12) as d}
              <div class="brow"><span class="bq">{title(d.name)}</span><span class="bc">{d.page_count}p · {d.age_days}d</span></div>
            {/each}
          </div>
          {#if dp.orphans.length > 12}<div class="cnote" style="margin-top:6px">+{dp.orphans.length - 12} more</div>{/if}
        {:else}<div class="muted sm">Every document has been cited. ✓</div>{/if}
      </div>
      <div class="card">
        <div class="ctitle"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg> Cold documents <span class="cnote">used before, not in this window</span></div>
        {#if dp.cold.length}
          <div class="blist">
            {#each dp.cold.slice(0, 12) as d}
              <div class="brow"><span class="bq">{title(d.name)}</span><span class="bc">last {d.last_cited_days}d ago</span></div>
            {/each}
          </div>
        {:else}<div class="muted sm">No cold docs — everything cited recently.</div>{/if}
      </div>
    </div>
  {/if}
  <!-- ░ corpus hygiene: conflicting + duplicate facts ░ -->
  {#if cp && (cp.conflict_count || cp.duplicate_count)}
    <div class="fw2">
      <div class="card">
        <div class="ctitle"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><line x1="12" y1="2" x2="12" y2="22"/><polyline points="7 7 12 2 17 7"/><polyline points="7 17 12 22 17 17"/></svg> Conflicting facts <span class="cnote">same key, different active values — resolve</span></div>
        {#if cp.conflicts.length}
          <div class="qlist">
            {#each cp.conflicts as c}
              <div class="frow2" style="gap:4px">
                <span class="ft"><b>{c.key}</b> · {c.variants} versions</span>
                {#each c.values as v}<span class="cnote">• {v}</span>{/each}
              </div>
            {/each}
          </div>
        {:else}<div class="muted sm">No conflicts. ✓</div>{/if}
      </div>
      <div class="card">
        <div class="ctitle">⧉ Duplicate facts <span class="cnote">identical value stored more than once</span></div>
        {#if cp.duplicates.length}
          <div class="blist">
            {#each cp.duplicates as d}
              <div class="brow"><span class="bq">{d.value}</span><span class="bc">{d.n}×</span></div>
            {/each}
          </div>
        {:else}<div class="muted sm">No duplicates. ✓</div>{/if}
      </div>
    </div>
  {:else if cp}
    <div class="card"><div class="muted sm">Corpus clean — no conflicting or duplicate facts. ✓ ({cp.active} active facts)</div></div>
  {/if}
  <!-- ░ topics — demand vs coverage (folded from Topics tab) ░ -->
  {#if kw}
    <div class="card">
      <div class="ctitle">Topics — demand vs coverage
        <span class="cnote">questions asked vs answers sourced · privacy-safe aggregate · k-anon MIN 2</span>
      </div>
      {#if kw.demand?.length}
        <EChart
          option={hbarOpt(kw.demand.slice(0, 8).map((d: any) => ({ label: d.term, value: d.asked })), { color: C.blue })}
          height={kw.demand.slice(0, 8).length * 34}
        />
        <div class="cnote" style="font-size:11px; margin-bottom:4px; margin-top:4px">Coverage: green ≥70% · amber 40-69% · red &lt;40%</div>
      {:else}
        <div class="muted sm">No demand data yet — ask questions in Chat to populate.</div>
      {/if}

      {#if kw.intents?.length}
        <div class="ctitle" style="margin-top:16px; font-size:12px; font-weight:600; color:var(--muted)">Intent mix</div>
        <EChart
          option={barOpt(kw.intents.slice(0, 8).map((it: any) => ({ label: INTENT_LABEL[it.intent] ?? it.intent, n: it.count })), { color: C.teal })}
          height={140}
        />
      {/if}

      {#if kw.lang}
        <div class="ctitle" style="margin-top:16px; font-size:12px; font-weight:600; color:var(--muted)">Language split</div>
        <div style="display:flex; align-items:center; gap:8px; margin-top:6px">
          {#if (kw.lang.en + kw.lang.my) > 0}
            {@const total = kw.lang.en + kw.lang.my}
            {@const enPct = Math.round((kw.lang.en / total) * 100)}
            <span class="cnote" style="min-width:28px; font-size:12px">EN</span>
            <div style="flex:1; height:10px; background:#e8e5e0; border-radius:99px; overflow:hidden; display:flex">
              <div style="width:{enPct}%; background:#3f7fb0; border-radius:99px 0 0 99px"></div>
              <div style="flex:1; background:#7b6bd6; border-radius:0 99px 99px 0"></div>
            </div>
            <span class="cnote" style="min-width:28px; font-size:12px; text-align:right">MY</span>
            <span class="cnote" style="min-width:72px; font-size:11px">{enPct}% EN / {100 - enPct}% MY</span>
          {:else}
            <span class="muted sm">No language data.</span>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

{:else}<div class="muted pad">Loading…</div>{/if}

<style>
  .sortb{font-size:11px; padding:3px 9px; border-radius:99px; border:1px solid var(--line); background:#fff; color:var(--muted); cursor:pointer;}
  .sortb.on{background:#f0efed; color:var(--clay); border-color:transparent;}
  .dtab{width:100%; border-collapse:collapse; font-size:12.5px; margin-top:8px;}
  .dtab th{text-align:left; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; padding:5px 8px; border-bottom:1px solid var(--line);}
  .dtab td{padding:7px 8px; border-bottom:1px solid var(--line); color:var(--ink); white-space:nowrap;}
  .dtab td.dname{white-space:normal; max-width:320px;}
  .dtab tr:last-child td{border-bottom:none;}
  .dtab tr.dead td{opacity:.6;}
  .dtab .muted, .cnote{color:var(--muted);}
  .tag{display:inline-block; margin-left:6px; font-size:9.5px; text-transform:uppercase; letter-spacing:.03em; padding:1px 6px; border-radius:99px; vertical-align:middle;}
  .tag.orph{background:#f6e1da; color:#c0492f;}
  .tag.cold{background:#e7eef4; color:#3f7fb0;}
  .tag.warn{background:#f6efda; color:#a9742a;}
</style>
