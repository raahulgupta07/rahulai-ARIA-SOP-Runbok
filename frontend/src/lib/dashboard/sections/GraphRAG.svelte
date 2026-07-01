<script lang="ts">
  // GraphRAG explorer — left force-graph canvas + right panel (Entity / Path / Global).
  // Mirrors mockups/mockup-graphrag.html. Graph = lazy echarts force layout from
  // GET /graphrag/graph; node-click → entity profile. Right tabs do path reasoning
  // (A→B chain) and whole-corpus community queries.
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { onMount, onDestroy } from 'svelte';

  // reactive admin gate (cachedUser() is null at cold start → would stick on "Admin only")
  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));

  // ── palette (matches the mockup tokens) ──
  const KIND_COLOR: Record<string, string> = {
    system: '#3f7fb0', code: '#c2683f', menu: '#7b6bd6', screen: '#2f8f83',
    field: '#c98a2e', table: '#3f8f5f'
  };
  function kindColor(k: string) { return KIND_COLOR[k] || '#8a857c'; }
  const REL_COLOR: Record<string, string> = {
    depends_on: '#c2683f', runs_on: '#3f7fb0', part_of: '#3f8f5f', accessed_via: '#7b6bd6'
  };
  function relColor(r: string) { return REL_COLOR[r] || '#cfcabf'; }
  const REL_CLASS: Record<string, string> = {
    depends_on: 'r-dep', runs_on: 'r-run', part_of: 'r-part', accessed_via: 'r-acc'
  };
  function relClass(r: string) { return REL_CLASS[r] || 'r-other'; }

  // ── graph state ──
  let graph = $state<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] });
  let stats = $state<any>(null);
  let loaded = $state(false);
  let rebuilding = $state(false);

  // ── right-panel state ──
  let tab = $state<'entity' | 'path' | 'global'>('entity');

  // entity
  let entity = $state<any>(null);
  let entityLoading = $state(false);

  // path
  let pathA = $state('');
  let pathB = $state('');
  let pathRes = $state<any>(null);
  let pathLoading = $state(false);

  // global
  let globalQ = $state('');
  let globalRes = $state<any>(null);
  let globalLoading = $state(false);

  // ── echarts ──
  let el: HTMLDivElement | null = null;
  let chart: any = null;
  let ec: any = null;
  let ro: ResizeObserver | null = null;

  function load() {
    api.graphragGraph(300).then((r) => { graph = r || { nodes: [], edges: [] }; loaded = true; paint(); }).catch(() => { loaded = true; });
    api.graphragStats().then((r) => (stats = r)).catch(() => {});
  }

  function buildOption() {
    const nodes = (graph.nodes || []).map((n: any) => {
      const c = kindColor(n.kind);
      const size = Math.max(10, Math.min(46, 10 + (n.docs || 0) * 6));
      return {
        id: String(n.id), name: n.name, value: n.docs || 0,
        symbolSize: size,
        itemStyle: { color: '#fff', borderColor: c, borderWidth: 2 },
        label: { show: true, color: '#1f1e1d', fontSize: 11, fontWeight: 600 },
        _raw: n
      };
    });
    const links = (graph.edges || []).map((e: any) => ({
      source: String(e.src), target: String(e.dst),
      value: e.rel,
      lineStyle: { color: relColor(e.rel), width: 1.6, opacity: 0.75, curveness: 0.08 },
      label: { show: false, formatter: e.rel, fontSize: 9, color: '#8a857c' },
      emphasis: { label: { show: true } }
    }));
    return {
      backgroundColor: 'transparent',
      tooltip: {
        confine: true,
        formatter: (p: any) => p.dataType === 'node'
          ? `${p.data.name}<br/><span style="color:#8a857c">${p.data._raw.kind} · ${p.data.value} docs</span>`
          : p.data.value
      },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        data: nodes, links,
        edgeSymbol: ['none', 'none'],
        emphasis: { focus: 'adjacency', lineStyle: { width: 3, opacity: 1 } },
        force: { repulsion: 220, edgeLength: [60, 160], gravity: 0.08, friction: 0.4 },
        label: { position: 'right' },
        scaleLimit: { min: 0.3, max: 4 }
      }]
    };
  }

  function paint() {
    if (!chart) return;
    chart.setOption(buildOption(), { notMerge: true, lazyUpdate: true });
  }

  function pickNode(raw: any) {
    tab = 'entity';
    loadEntity({ id: raw.id });
  }

  function loadEntity(arg: { id?: number; name?: string }) {
    entityLoading = true;
    api.graphragEntity(arg.id ?? null, arg.name ?? null)
      .then((r) => { entity = r; })
      .catch(() => { entity = null; })
      .finally(() => { entityLoading = false; });
  }

  async function tracePath() {
    if (!pathA.trim() || !pathB.trim()) return;
    pathLoading = true; pathRes = null;
    try { pathRes = await api.graphragPath(pathA.trim(), pathB.trim()); }
    catch { pathRes = { found: false, reason: 'Trace failed.' }; }
    pathLoading = false;
  }

  async function askGlobal() {
    if (!globalQ.trim()) return;
    globalLoading = true; globalRes = null;
    try { globalRes = await api.graphragGlobal(globalQ.trim()); }
    catch { globalRes = { answer: 'Query failed.', clusters: [] }; }
    globalLoading = false;
  }

  async function rebuild() {
    if (rebuilding) return;
    rebuilding = true;
    try { await api.graphragBuildCommunities(); } catch {}
    rebuilding = false;
    api.graphragStats().then((r) => (stats = r)).catch(() => {});
  }

  const clusterColors = ['#3f7fb0', '#c2683f', '#2f8f83', '#7b6bd6', '#c98a2e', '#3f8f5f'];
  function clusterColor(i: number) { return clusterColors[i % clusterColors.length]; }

  onMount(() => {
    let alive = true;
    (async () => {
      ec = await import('echarts');
      if (!alive || !el) return;
      chart = ec.init(el, null, { renderer: 'svg' });
      chart.on('click', (p: any) => { if (p.dataType === 'node' && p.data?._raw) pickNode(p.data._raw); });
      ro = new ResizeObserver(() => chart && chart.resize());
      ro.observe(el);
      if (loaded) paint();
    })();
    return () => { alive = false; };
  });

  onDestroy(() => { ro?.disconnect(); chart?.dispose?.(); chart = null; });

  $effect(() => { if (isAdmin && !loaded) load(); });
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else}
  <div class="gr-wrap">
    <!-- ── GRAPH CANVAS ── -->
    <div class="gr-canvas">
      <div class="gr-head">
        <div class="gr-htxt">
          <h2>Knowledge Graph</h2>
          {#if stats}
            <small>{stats.entities} entities · {stats.edges} typed relationships · {stats.rel_types} types · {stats.communities} communities</small>
          {:else}
            <small class="muted">loading…</small>
          {/if}
        </div>
        <button class="gr-rebuild" onclick={rebuild} disabled={rebuilding}>
          {rebuilding ? 'Rebuilding…' : 'Rebuild communities'}
        </button>
      </div>
      <div class="gr-legend">
        <span class="lg"><i style="background:#c2683f"></i>depends_on</span>
        <span class="lg"><i style="background:#3f7fb0"></i>runs_on</span>
        <span class="lg"><i style="background:#3f8f5f"></i>part_of</span>
        <span class="lg"><i style="background:#7b6bd6"></i>accessed_via</span>
      </div>
      <div bind:this={el} class="gr-chart"></div>
      {#if loaded && !graph.nodes.length}
        <div class="gr-empty muted">No entities indexed yet.</div>
      {/if}
    </div>

    <!-- ── RIGHT PANEL ── -->
    <aside class="gr-side">
      <div class="gr-tabs">
        <button class="gr-tab" class:on={tab === 'entity'} onclick={() => (tab = 'entity')}>Entity</button>
        <button class="gr-tab" class:on={tab === 'path'} onclick={() => (tab = 'path')}>Path</button>
        <button class="gr-tab" class:on={tab === 'global'} onclick={() => (tab = 'global')}>Global</button>
      </div>
      <div class="gr-body">

        <!-- ENTITY -->
        {#if tab === 'entity'}
          {#if entityLoading}
            <div class="muted pad">Loading entity…</div>
          {:else if !entity}
            <div class="gr-emptytab">Click a node in the graph to see its profile.</div>
          {:else}
            <div class="ehead">
              <span class="eh-dot" style="background:{kindColor(entity.entity?.kind)}"></span>
              <h3>{entity.entity?.name}</h3>
            </div>
            <div class="ekind">{entity.entity?.kind}{entity.docs?.length ? ` · mentioned in ${entity.docs.length} document${entity.docs.length > 1 ? 's' : ''}` : ''}</div>

            <div class="pt">Relationships</div>
            {#if entity.relationships?.length}
              {#each entity.relationships as r}
                <div class="rel">
                  <span class="r {relClass(r.rel)}">{r.rel}</span>
                  <span class="e">{r.other}</span>
                  <span class="dir">{r.dir === 'out' ? '→' : r.dir === 'in' ? '←' : ''}</span>
                </div>
              {/each}
            {:else}
              <div class="muted small">No typed relationships.</div>
            {/if}

            <div class="pt">Appears in</div>
            {#if entity.docs?.length}
              {#each entity.docs as d}<span class="doctag">{d.name}</span>{/each}
            {:else}
              <div class="muted small">No documents.</div>
            {/if}
          {/if}
        {/if}

        <!-- PATH -->
        {#if tab === 'path'}
          <div class="pt">How does X connect to Y?</div>
          <div class="pin">
            <input placeholder="Entity A" bind:value={pathA} />
            <span class="ar">→</span>
            <input placeholder="Entity B" bind:value={pathB} />
            <button class="go" onclick={tracePath} disabled={pathLoading}>{pathLoading ? '…' : 'Trace'}</button>
          </div>

          {#if pathLoading}
            <div class="muted pad">Tracing…</div>
          {:else if pathRes}
            {#if pathRes.found}
              <div class="pt">Shortest path</div>
              <div class="chain">
                {#each pathRes.steps as s, i}
                  <div class="cstep"><span class="cnode">{s.entity}</span></div>
                  {#if s.rel && i < pathRes.steps.length - 1}
                    <div class="crel" style="color:{relColor(s.rel)};background:{relColor(s.rel)}1a">{s.rel}</div>
                    <div class="cline"></div>
                  {/if}
                {/each}
              </div>
              {#if pathRes.explanation}
                <div class="expl">{pathRes.explanation}</div>
              {/if}
            {:else}
              <div class="gr-emptytab">No path found{pathRes.reason ? ` — ${pathRes.reason}` : '.'}</div>
            {/if}
          {/if}
        {/if}

        <!-- GLOBAL -->
        {#if tab === 'global'}
          <div class="pt">Whole-corpus question</div>
          <div class="gbox">
            <input placeholder="Summarize everything about…" bind:value={globalQ} />
            <button class="go" onclick={askGlobal} disabled={globalLoading}>{globalLoading ? '…' : 'Ask'}</button>
          </div>

          {#if globalLoading}
            <div class="muted pad">Synthesizing across the corpus…</div>
          {:else if globalRes}
            {#if globalRes.answer}<div class="gans">{globalRes.answer}</div>{/if}
            {#if globalRes.clusters?.length}
              <div class="pt">Clusters used</div>
              {#each globalRes.clusters as c, i}
                <div class="cluster">
                  <div class="ct"><span class="cd" style="background:{clusterColor(i)}"></span>{c.label}</div>
                  {#if c.summary}<p>{c.summary}</p>{/if}
                </div>
              {/each}
            {/if}
          {/if}
        {/if}

      </div>
    </aside>
  </div>
{/if}

<style>
  .gr-wrap { display: flex; min-height: 0; height: calc(100vh - 170px); gap: 0; }

  /* graph canvas */
  .gr-canvas { flex: 1; position: relative; overflow: hidden; min-width: 0;
    background: radial-gradient(circle at 50% 40%, #fff, #f6f4ee); border: 1px solid #e9e6dd; border-radius: 12px; }
  .gr-head { position: absolute; top: 14px; left: 18px; z-index: 5; display: flex; align-items: flex-start; gap: 16px; }
  .gr-htxt h2 { margin: 0; font-size: 17px; }
  .gr-htxt small { color: #8a857c; font-size: 11.5px; }
  .gr-rebuild { font-size: 11.5px; padding: 6px 11px; border: 1px solid #cfe0ee; background: #f1f7fb;
    color: #3f7fb0; border-radius: 8px; font-weight: 600; cursor: pointer; }
  .gr-rebuild:hover:not(:disabled) { background: #e6f0f8; }
  .gr-rebuild:disabled { opacity: .55; cursor: default; }
  .gr-legend { position: absolute; top: 16px; right: 18px; z-index: 5; display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; }
  .lg { display: flex; align-items: center; gap: 5px; color: #8a857c; }
  .lg i { width: 18px; height: 3px; border-radius: 2px; display: inline-block; }
  .gr-chart { position: absolute; inset: 0; width: 100%; height: 100%; }
  .gr-empty, .gr-emptytab { color: #8a857c; }
  .gr-empty { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
  .gr-emptytab { padding: 30px 6px; text-align: center; font-size: 13px; }

  /* right panel */
  .gr-side { width: 380px; flex: none; background: #fff; border: 1px solid #e9e6dd; border-left: none;
    border-radius: 0 12px 12px 0; display: flex; flex-direction: column; }
  .gr-tabs { display: flex; gap: 4px; padding: 12px 14px 0; }
  .gr-tab { flex: 1; text-align: center; font-size: 12px; padding: 8px; border: none;
    border-radius: 9px 9px 0 0; cursor: pointer; color: #8a857c; font-weight: 600; background: transparent; }
  .gr-tab.on { background: #eaf2f8; color: #3f7fb0; }
  .gr-body { flex: 1; overflow: auto; padding: 16px 18px; }
  .pt { font-size: 11px; text-transform: uppercase; color: #8a857c; font-weight: 600; margin: 14px 0 8px; }
  .pt:first-child { margin-top: 0; }
  .small { font-size: 12.5px; }

  /* entity profile */
  .ehead { display: flex; align-items: center; gap: 10px; }
  .eh-dot { width: 12px; height: 12px; border-radius: 50%; }
  .ehead h3 { margin: 0; font-size: 18px; }
  .ekind { font-size: 11px; color: #8a857c; margin-top: 2px; }
  .rel { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px dashed #e9e6dd; font-size: 13px; }
  .rel:last-child { border-bottom: none; }
  .rel .r { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 5px; flex: none; }
  .r-dep { background: #fbe9e3; color: #c2683f; }
  .r-run { background: #e7f1f8; color: #3f7fb0; }
  .r-part { background: #eef2e9; color: #3f8f5f; }
  .r-acc { background: #efe9fb; color: #7b6bd6; }
  .r-other { background: #f0eee6; color: #6a655c; }
  .rel .e { font-weight: 600; }
  .rel .dir { color: #b8b3a8; margin-left: auto; }
  .doctag { display: inline-block; font-size: 11px; background: #f0eee6; border-radius: 6px; padding: 3px 9px; margin: 3px 4px 0 0; color: #4a463e; }

  /* path */
  .pin { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
  .pin input { flex: 1; min-width: 0; border: 1px solid #e9e6dd; border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px; }
  .ar { color: #8a857c; flex: none; }
  .go { background: #3f7fb0; color: #fff; border: none; border-radius: 8px; padding: 8px 12px; font-weight: 600; cursor: pointer; flex: none; }
  .go:disabled { opacity: .6; cursor: default; }
  .chain { display: flex; flex-direction: column; gap: 0; margin: 8px 0; }
  .cstep { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
  .cnode { background: #eaf2f8; border: 1px solid #cfe0ee; border-radius: 8px; padding: 7px 12px; font-weight: 600; font-size: 13px; }
  .crel { font-size: 10px; font-weight: 700; border-radius: 5px; padding: 2px 7px; margin-left: 30px; width: fit-content; }
  .cline { margin-left: 18px; width: 2px; height: 14px; background: #cfe0ee; }
  .expl { background: #f7fbfd; border: 1px solid #e1eef6; border-radius: 10px; padding: 11px 13px; font-size: 13px; line-height: 1.55; color: #3a372f; margin-top: 10px; }

  /* global */
  .gbox { display: flex; gap: 8px; margin-bottom: 12px; }
  .gbox input { flex: 1; min-width: 0; border: 1px solid #e9e6dd; border-radius: 8px; padding: 9px 11px; font: inherit; font-size: 13px; }
  .gans { background: #f4faf6; border: 1px solid #dceee2; border-radius: 11px; padding: 13px 14px; font-size: 13.5px; line-height: 1.6; color: #3a372f; }
  .cluster { border: 1px solid #e9e6dd; border-radius: 11px; padding: 12px 13px; margin-bottom: 10px; }
  .cluster .ct { font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 7px; }
  .cluster .ct .cd { width: 9px; height: 9px; border-radius: 50%; }
  .cluster p { margin: 7px 0 0; font-size: 12.5px; color: #4a463e; line-height: 1.5; }

  @media (max-width: 820px) {
    .gr-wrap { flex-direction: column; height: auto; }
    .gr-canvas { height: 420px; border-radius: 12px; }
    .gr-side { width: auto; border-left: 1px solid #e9e6dd; border-radius: 12px; margin-top: 12px; }
  }
</style>
