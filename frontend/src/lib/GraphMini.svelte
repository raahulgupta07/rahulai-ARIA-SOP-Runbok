<script lang="ts">
  // Reusable ECharts force-graph. Lazy-loads echarts (same module the Brain page
  // uses). Renders {nodes,links} where each node has {id,type,label,val,...}.
  // Emits onpick(node) on click. Used for the dashboard ego map, people map and
  // the embedded knowledge graph.
  let { data = { nodes: [], links: [] }, height = 320, fill = false, dark = true, onpick = (n: any) => {} }
    = $props<{ data?: any; height?: number; fill?: boolean; dark?: boolean; onpick?: (n: any) => void }>();

  let el: HTMLDivElement | null = null;
  let chart: any = null;
  let ec: any = null;

  const COLORS: Record<string, string> = {
    me: '#c2683f', user: '#c2683f', doc: '#5b8fb0', page: '#8a7fc0', fact: '#cf7fa6'
  };

  function option(d: any) {
    const nodes = (d.nodes || []).map((n: any) => ({
      id: n.id,
      name: n.label || n.id,
      value: n.val || 8,
      symbolSize: Math.max(8, Math.min(54, n.val || 10)),
      category: n.type,
      itemStyle: { color: COLORS[n.type] || '#9aa' },
      label: {
        show: (n.val || 0) >= 20 || n.type === 'me' || n.type === 'user' || n.type === 'fact',
        color: dark ? '#e8e4da' : '#46443f', fontSize: 11
      },
      _raw: n
    }));
    const links = (d.links || []).map((l: any) => ({
      source: l.source, target: l.target,
      lineStyle: {
        width: Math.max(1, Math.min(5, (l.value || 1))),
        color: l.kind === 'cocite' ? '#b98a5e' : l.kind === 'taught' ? '#cf7fa6' : 'source',
        opacity: 0.4, curveness: 0.08
      }
    }));
    return {
      backgroundColor: 'transparent',
      tooltip: { confine: true, formatter: (p: any) => p.dataType === 'node' ? p.data.name : '' },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        data: nodes, links, edgeSymbol: ['none', 'none'],
        emphasis: { focus: 'adjacency', label: { show: true } },
        force: { repulsion: 140, edgeLength: [40, 120], gravity: 0.12, friction: 0.25 },
        lineStyle: { color: 'source' }
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

<div bind:this={el} class="gmini" style="height:{fill ? '100%' : height + 'px'}; background:{dark ? '#211f1c' : 'transparent'}"></div>

<style>
  .gmini { width: 100%; border-radius: 12px; overflow: hidden; }
</style>
