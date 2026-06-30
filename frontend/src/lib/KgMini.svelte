<script lang="ts">
  // Answer-scoped REAL knowledge subgraph for the Sources drawer. Renders the same
  // entity/typed-relationship style as the full Knowledge Graph (GraphRAG.svelte):
  // white nodes with kind-coloured borders, rel-coloured typed edges, always-on
  // labels. Takes {nodes:[{id,name,kind,docs,seed}], edges:[{src,dst,rel}]}.
  let { data = { nodes: [], edges: [] }, height = 220, fill = false, onpick = (n: any) => {} }
    = $props<{ data?: any; height?: number; fill?: boolean; onpick?: (n: any) => void }>();

  // palette mirrors GraphRAG.svelte so the mini matches the full view exactly
  const KIND_COLOR: Record<string, string> = {
    system: '#3f7fb0', code: '#c2683f', menu: '#7b6bd6', screen: '#2f8f83',
    field: '#c98a2e', table: '#3f8f5f'
  };
  const kindColor = (k: string) => KIND_COLOR[k] || '#8a857c';
  const REL_COLOR: Record<string, string> = {
    depends_on: '#c2683f', runs_on: '#3f7fb0', part_of: '#3f8f5f', accessed_via: '#7b6bd6'
  };
  const relColor = (r: string) => REL_COLOR[r] || '#cfcabf';

  let el: HTMLDivElement | null = null;
  let chart: any = null;
  let ec: any = null;

  // legend = only the rel types actually present
  let rels = $derived([...new Set((data?.edges || []).map((e: any) => e.rel))] as string[]);

  function option(d: any) {
    const nodes = (d.nodes || []).map((n: any) => {
      const c = kindColor(n.kind);
      const size = Math.max(9, Math.min(34, 9 + (n.docs || 0) * 5)) + (n.seed ? 4 : 0);
      return {
        id: String(n.id), name: n.name, value: n.docs || 0,
        symbolSize: size,
        itemStyle: { color: '#fff', borderColor: c, borderWidth: n.seed ? 3 : 2,
                     shadowColor: n.seed ? c : 'transparent', shadowBlur: n.seed ? 8 : 0 },
        label: { show: true, color: '#1f1e1d', fontSize: 10, fontWeight: n.seed ? 700 : 500 },
        _raw: n
      };
    });
    const links = (d.edges || []).map((e: any) => ({
      source: String(e.src), target: String(e.dst), value: e.rel,
      lineStyle: { color: relColor(e.rel), width: 1.5, opacity: 0.75, curveness: 0.08 },
      emphasis: { lineStyle: { width: 3, opacity: 1 } }
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
        data: nodes, links, edgeSymbol: ['none', 'none'],
        emphasis: { focus: 'adjacency', lineStyle: { width: 3, opacity: 1 } },
        force: { repulsion: 160, edgeLength: [40, 120], gravity: 0.1, friction: 0.35 },
        label: { position: 'right' },
        scaleLimit: { min: 0.4, max: 4 }
      }]
    };
  }

  async function render() {
    if (!el) return;
    if (!ec) ec = await import('echarts');
    if (!chart) {
      chart = ec.init(el, null, { renderer: 'canvas' });
      chart.on('click', (p: any) => { if (p.dataType === 'node' && p.data?._raw) onpick(p.data._raw); });
    }
    chart.setOption(option(data), true);
    chart.resize();
  }

  $effect(() => { void data; void height; void fill; render(); });
  $effect(() => {
    const onResize = () => chart?.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart?.dispose?.(); chart = null; };
  });
</script>

<div class="kgmini-wrap">
  <div bind:this={el} class="kgmini" style="height:{fill ? '100%' : height + 'px'}"></div>
  {#if rels.length}
    <div class="kg-legend">
      {#each rels as r}
        <span class="kg-leg"><i style="background:{relColor(r)}"></i>{r}</span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .kgmini-wrap { width: 100%; }
  .kgmini { width: 100%; border-radius: 12px; overflow: hidden; background: #faf8f3; border: 1px solid #efece4; }
  .kg-legend { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 7px; }
  .kg-leg { display: inline-flex; align-items: center; gap: 5px; font-size: 10px; color: #8a857c; }
  .kg-leg i { width: 14px; height: 2.5px; border-radius: 2px; display: inline-block; }
</style>
