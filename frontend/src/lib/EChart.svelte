<script lang="ts">
  // Lazy-loaded ECharts wrapper — light theme, smooth animation, auto-resize.
  // Pass an `option` object (build it with $lib/charts helpers). Re-renders on
  // option change, disposes on destroy. Same echarts module the graphs use.
  import { onMount } from 'svelte';

  let { option = {}, height = 200 } = $props<{ option?: any; height?: number | string }>();

  let el: HTMLDivElement | null = null;
  let chart: any = null;
  let ec: any = null;
  let ro: ResizeObserver | null = null;

  onMount(() => {
    let alive = true;
    (async () => {
      ec = await import('echarts');
      if (!alive || !el) return;
      chart = ec.init(el, null, { renderer: 'svg' });
      chart.setOption(option);
      ro = new ResizeObserver(() => chart && chart.resize());
      ro.observe(el);
    })();
    return () => {
      alive = false;
      ro?.disconnect();
      chart?.dispose();
      chart = null;
    };
  });

  // reactive: push new option whenever it changes. Smart-merge so live data
  // updates TWEEN smoothly (values animate to new positions) instead of a hard
  // redraw. Only fall back to notMerge when the series *count* changes (e.g. a
  // chart gains/loses a line) so stale series can't linger.
  let prevLen = -1;
  $effect(() => {
    const opt = option;
    if (!chart) return;
    const len = Array.isArray(opt?.series) ? opt.series.length : opt?.series ? 1 : 0;
    const notMerge = len !== prevLen;
    prevLen = len;
    chart.setOption(opt, { notMerge, lazyUpdate: true });
  });
</script>

<div bind:this={el} style="width:100%; height:{typeof height === 'number' ? height + 'px' : height};"></div>
