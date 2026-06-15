<script lang="ts">
  // Animated "deep-learning brain" graph (echarts force). Nodes glow, synapses
  // fire along random edges, the layout breathes, and brand-new nodes (knowledge
  // just learned) flash + announce in a live ticker. Toggle animation off for perf
  // / reduced-motion. Lazy-loads echarts (same module the Brain page uses).
  import { onMount } from 'svelte';

  let { data = { nodes: [], links: [] }, fill = false, height = 460, onpick = (n: any) => {} }
    = $props<{ data?: any; fill?: boolean; height?: number; onpick?: (n: any) => void }>();

  let el: HTMLDivElement | null = null;
  let chart: any = null;
  let ec: any = null;
  let animated = $state(true);
  let ticker = $state('');           // live "learning…" line
  let pulseTimer: any = null;
  let tickTimer: any = null;

  // per-node / per-link transient "energy" (decays each tick) → glow + firing
  let energy = new Map<string, number>();   // node id -> 0..1
  let linkHot = new Map<number, number>();   // link index -> 0..1
  let seenIds = new Set<string>();           // node ids we've already shown
  let labelPool: string[] = [];

  const BASE: Record<string, string> = {
    me: '#c2683f', user: '#c2683f', doc: '#5b8fb0', page: '#8a7fc0', fact: '#cf7fa6'
  };
  const FIRE = [255, 209, 138];   // warm synapse flash

  function lerp(hex: string, e: number) {
    if (e <= 0.02) return hex;
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    const t = Math.min(1, e);
    const mix = (a: number, c: number) => Math.round(a + (c - a) * t * 0.8);
    return `rgb(${mix(r, FIRE[0])},${mix(g, FIRE[1])},${mix(b, FIRE[2])})`;
  }

  function buildOption() {
    const nodes = (data.nodes || []).map((n: any) => {
      const e = energy.get(n.id) || 0;
      const base = BASE[n.type] || '#9aa';
      const size = Math.max(7, Math.min(52, (n.val || 10))) * (1 + e * 0.7);
      return {
        id: n.id, name: n.label || n.id, value: n.val || 8,
        symbolSize: size, category: n.type,
        itemStyle: { color: lerp(base, e), shadowBlur: 6 + e * 26, shadowColor: e > 0.05 ? 'rgba(255,200,140,.9)' : base, borderColor: 'rgba(255,255,255,.18)', borderWidth: e > 0.4 ? 1.4 : 0 },
        label: { show: (n.val || 0) >= 22 || n.type === 'me' || n.type === 'user' || e > 0.5, color: e > 0.4 ? '#fff' : '#cfc9bd', fontSize: 11 },
        _raw: n
      };
    });
    const links = (data.links || []).map((l: any, i: number) => {
      const e = linkHot.get(i) || 0;
      const fire = e > 0.04;
      return {
        source: l.source, target: l.target,
        lineStyle: {
          width: 0.6 + e * 3,
          color: fire ? `rgba(255,196,128,${0.35 + e * 0.55})` : (l.kind === 'cocite' ? 'rgba(185,138,94,.22)' : l.kind === 'taught' ? 'rgba(207,127,166,.3)' : 'source'),
          opacity: fire ? 0.9 : 0.22, curveness: 0.12
        }
      };
    });
    return {
      backgroundColor: 'transparent',
      tooltip: { confine: true, formatter: (p: any) => p.dataType === 'node' ? p.data.name : '' },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        data: nodes, links,
        edgeSymbol: ['none', 'none'],
        emphasis: { focus: 'adjacency', lineStyle: { width: 3, opacity: 1 } },
        force: { repulsion: 130, edgeLength: [40, 130], gravity: 0.11, friction: animated ? 0.16 : 0.6 },
        lineStyle: { color: 'source', opacity: 0.22, curveness: 0.12 },
        scaleLimit: { min: 0.25, max: 4 }
      }]
    };
  }

  function paint() {
    if (!chart) return;
    chart.setOption(buildOption(), { lazyUpdate: true, silent: true });
  }

  // one animation tick: decay energy, fire a few random synapses
  function pulse() {
    if (!animated) return;
    for (const [k, v] of energy) { const nv = v * 0.84; nv < 0.02 ? energy.delete(k) : energy.set(k, nv); }
    for (const [k, v] of linkHot) { const nv = v * 0.8; nv < 0.03 ? linkHot.delete(k) : linkHot.set(k, nv); }
    const ns = data.nodes || [], ls = data.links || [];
    if (ns.length) {
      // fire ~3 random nodes + their incident edges
      for (let f = 0; f < 3; f++) {
        const n = ns[(Math.random() * ns.length) | 0];
        if (n) energy.set(n.id, 1);
      }
    }
    if (ls.length) {
      const k = Math.min(22, Math.ceil(ls.length * 0.05));
      for (let f = 0; f < k; f++) {
        const idx = (Math.random() * ls.length) | 0;
        linkHot.set(idx, 1);
        const l = ls[idx];
        if (l) { energy.set(typeof l.source === 'object' ? l.source.id : l.source, Math.max(energy.get(l.source) || 0, 0.7)); }
      }
    }
    paint();
  }

  // detect brand-new nodes (just learned) → flash them + announce
  function ingest(newData: any) {
    const ids = new Set<string>((newData.nodes || []).map((n: any) => n.id));
    if (seenIds.size) {
      const fresh = (newData.nodes || []).filter((n: any) => !seenIds.has(n.id));
      for (const n of fresh) { energy.set(n.id, 1.4); ticker = `＋ learned · ${n.label || n.id}`; }
    }
    seenIds = ids;
    labelPool = (newData.nodes || []).map((n: any) => n.label).filter(Boolean);
  }

  // ambient ticker — cycles what the brain is "consolidating"
  function rollTicker() {
    if (ticker.startsWith('＋')) { setTimeout(() => { if (ticker.startsWith('＋')) ticker = ''; }, 2600); return; }
    if (labelPool.length) ticker = `● consolidating · ${labelPool[(Math.random() * labelPool.length) | 0]}`;
  }

  $effect(() => { ingest(data); paint(); });

  function toggle() {
    animated = !animated;
    if (animated) startTimers(); else stopTimers();
    paint();
  }
  function startTimers() {
    stopTimers();
    pulseTimer = setInterval(pulse, 170);
    tickTimer = setInterval(rollTicker, 2600);
  }
  function stopTimers() {
    if (pulseTimer) clearInterval(pulseTimer);
    if (tickTimer) clearInterval(tickTimer);
    pulseTimer = tickTimer = null;
  }

  onMount(() => {
    let disposed = false;
    (async () => {
      ec = await import('echarts');
      if (disposed || !el) return;
      chart = ec.init(el, null, { renderer: 'canvas' });
      chart.on('click', (p: any) => { if (p.dataType === 'node' && p.data?._raw) onpick(p.data._raw); });
      ingest(data);
      paint();
      const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce) animated = false; else startTimers();
    })();
    const onResize = () => chart?.resize();
    window.addEventListener('resize', onResize);
    return () => { disposed = true; stopTimers(); window.removeEventListener('resize', onResize); chart?.dispose?.(); chart = null; };
  });
</script>

<div class="bgwrap" style="height:{fill ? '100%' : height + 'px'}">
  <div bind:this={el} class="bgcanvas"></div>
  <div class="bgbar">
    <span class="blive" class:on={animated}>{animated ? '● live' : '○ paused'}</span>
    {#if ticker}<span class="btick">{ticker}</span>{/if}
    <button class="btoggle" onclick={toggle}>{animated ? 'Pause' : 'Animate'}</button>
  </div>
</div>

<style>
  .bgwrap { position: relative; width: 100%; border-radius: 12px; overflow: hidden; background: radial-gradient(120% 120% at 50% 40%, #26221d 0%, #1a1815 70%, #141210 100%); }
  .bgcanvas { width: 100%; height: 100%; }
  .bgbar { position: absolute; left: 12px; bottom: 12px; display: flex; align-items: center; gap: 10px; font-size: 12px; pointer-events: none; }
  .blive { color: #8a8478; font-weight: 600; }
  .blive.on { color: #ffba7d; text-shadow: 0 0 8px rgba(255,160,90,.6); animation: blink 1.6s ease-in-out infinite; }
  @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: .45; } }
  .btick { color: #d8cfc0; background: rgba(0,0,0,.35); border: 1px solid rgba(255,255,255,.08); padding: 3px 10px; border-radius: 20px; max-width: 360px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .btoggle { pointer-events: auto; color: #cfc9bd; background: rgba(0,0,0,.4); border: 1px solid rgba(255,255,255,.12); padding: 4px 12px; border-radius: 8px; font-size: 12px; }
  .btoggle:hover { color: #fff; border-color: rgba(255,180,120,.5); }
</style>
