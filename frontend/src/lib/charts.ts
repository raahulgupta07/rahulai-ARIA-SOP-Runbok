// ECharts option builders — one shared LIGHT theme (Power-BI / Tableau feel):
// gradient fills, rounded bars, smooth easing, soft gridlines, tabular numerics.
// Palette is NON-CORAL (coral is reserved for Chat). Colors:
export const C = {
  blue: '#3f7fb0',
  teal: '#2f8f83',
  violet: '#7b6bd6',
  amber: '#c98a2e',
  green: '#3f8f5f',
  red: '#c0492f',
  ink: '#46443f',
  muted: '#8a8578',
  grid: '#ece8df',
  track: '#eee9df'
};
export const SERIES = [C.blue, C.teal, C.violet, C.amber, C.green, C.red];

const FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, sans-serif';

// vertical gradient (solid top → faint bottom) for area/bar fills
function grad(hex: string, topA = 0.32, botA = 0.02) {
  return {
    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
    colorStops: [
      { offset: 0, color: hexA(hex, topA) },
      { offset: 1, color: hexA(hex, botA) }
    ]
  };
}
function barGrad(hex: string) {
  return {
    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
    colorStops: [
      { offset: 0, color: hex },
      { offset: 1, color: hexA(hex, 0.55) }
    ]
  };
}
function hexA(hex: string, a: number) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

const ANIM = { animation: true, animationDuration: 950, animationEasing: 'cubicOut', animationDelay: (i: number) => i * 32 };
// flat tooltip — hairline border, tabular numbers, NO drop shadow (matches app)
const BASE_TT = {
  trigger: 'axis',
  backgroundColor: 'rgba(255,255,255,.97)',
  borderColor: C.grid,
  borderWidth: 1,
  padding: [8, 12],
  textStyle: { color: C.ink, fontFamily: FONT, fontSize: 12 },
  extraCssText: 'border-radius:10px; box-shadow:none;',
  axisPointer: { lineStyle: { color: C.grid, type: 'dashed' }, crossStyle: { color: C.grid } }
};

function axisLabel() { return { color: C.muted, fontFamily: FONT, fontSize: 10.5 }; }
// faint dashed horizontal gridlines only — gridless feel
const SPLIT = { lineStyle: { color: C.grid, type: [3, 4] as any, width: 1 } };
// dashed target/reference line for area charts (opts.target)
function targetMark(target?: number, label?: string) {
  if (target == null) return undefined;
  return {
    silent: true, symbol: 'none',
    lineStyle: { color: C.muted, type: 'dashed', width: 1 },
    label: { show: !!label, formatter: label ?? '', color: C.muted, fontFamily: FONT, fontSize: 10, position: 'insideEndTop' },
    data: [{ yAxis: target }]
  };
}

// ---- smooth gradient AREA line (volume, cost, latency, WAU…) ----
export function areaOpt(rows: any[], opts: { xKey?: string; yKey?: string; color?: string; name?: string; target?: number; targetLabel?: string } = {}) {
  const xKey = opts.xKey ?? 'label';
  const yKey = opts.yKey ?? 'n';
  const color = opts.color ?? C.blue;
  return {
    ...ANIM,
    grid: { left: 6, right: 12, top: 16, bottom: 4, containLabel: true },
    tooltip: { ...BASE_TT },
    xAxis: {
      type: 'category', boundaryGap: false,
      data: rows.map((r) => r[xKey]),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { ...axisLabel(), hideOverlap: true },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: axisLabel(),
      splitLine: SPLIT
    },
    series: [{
      name: opts.name ?? '', type: 'line', smooth: 0.45, showSymbol: false,
      symbolSize: 8, symbol: 'circle',
      lineStyle: { width: 2.4, color, cap: 'round' },
      itemStyle: { color, borderColor: '#fff', borderWidth: 2 },
      areaStyle: { color: grad(color), origin: 'start' },
      emphasis: { focus: 'series', scale: 1.4 },
      markLine: targetMark(opts.target, opts.targetLabel),
      data: rows.map((r) => r[yKey])
    }]
  };
}

// ---- rounded gradient BAR (volume, role, counts…) ----
export function barOpt(rows: any[], opts: { xKey?: string; yKey?: string; color?: string } = {}) {
  const xKey = opts.xKey ?? 'label';
  const yKey = opts.yKey ?? 'n';
  const color = opts.color ?? C.blue;
  return {
    ...ANIM,
    grid: { left: 6, right: 10, top: 14, bottom: 4, containLabel: true },
    tooltip: { ...BASE_TT, axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category', data: rows.map((r) => r[xKey]),
      axisLine: { lineStyle: { color: C.grid } }, axisTick: { show: false },
      axisLabel: { ...axisLabel(), hideOverlap: true }
    },
    yAxis: {
      type: 'value', axisLine: { show: false }, axisTick: { show: false },
      axisLabel: axisLabel(), splitLine: SPLIT
    },
    series: [{
      type: 'bar', barMaxWidth: 26, barCategoryGap: '38%',
      itemStyle: { color: barGrad(color), borderRadius: [5, 5, 0, 0] },
      emphasis: { itemStyle: { color } },
      data: rows.map((r) => r[yKey])
    }]
  };
}

// ---- up/down split bar (feedback trend) ----
export function trendOpt(rows: any[], opts: { xKey?: string } = {}) {
  const xKey = opts.xKey ?? 'label';
  return {
    ...ANIM,
    grid: { left: 6, right: 10, top: 16, bottom: 4, containLabel: true },
    tooltip: { ...BASE_TT, axisPointer: { type: 'shadow' } },
    legend: { show: false },
    xAxis: {
      type: 'category', data: rows.map((r) => r[xKey]),
      axisLine: { lineStyle: { color: C.grid } }, axisTick: { show: false },
      axisLabel: { ...axisLabel(), hideOverlap: true }
    },
    yAxis: { type: 'value', axisLine: { show: false }, axisTick: { show: false }, axisLabel: axisLabel(), splitLine: SPLIT },
    series: [
      { name: 'up', type: 'bar', stack: 'f', barMaxWidth: 22, itemStyle: { color: barGrad(C.green), borderRadius: [4, 4, 0, 0] }, data: rows.map((r) => r.up || 0) },
      { name: 'down', type: 'bar', stack: 'f', barMaxWidth: 22, itemStyle: { color: barGrad(C.red) }, data: rows.map((r) => -(r.down || 0)) }
    ]
  };
}

// ---- animated DONUT ring with center value (coverage, trust, helpful…) ----
export function donutOpt(value: number | null, opts: { color?: string; label?: string; suffix?: string } = {}) {
  const color = opts.color ?? C.teal;
  const v = value ?? 0;
  return {
    ...ANIM, animationDuration: 1100,
    tooltip: { show: false },
    series: [{
      type: 'pie', radius: ['72%', '92%'], center: ['50%', '50%'],
      avoidLabelOverlap: false, silent: true,
      label: { show: true, position: 'center',
        formatter: () => `{v|${value == null ? '—' : v}${opts.suffix ?? ''}}\n{l|${opts.label ?? ''}}`,
        rich: {
          v: { fontSize: 26, fontWeight: 700, color: C.ink, fontFamily: FONT, lineHeight: 30 },
          l: { fontSize: 10.5, color: C.muted, fontFamily: FONT, padding: [3, 0, 0, 0] }
        } },
      itemStyle: { borderRadius: 8, borderColor: '#fff', borderWidth: 2 },
      data: [
        { value: v, itemStyle: { color: barGrad(color) } },
        { value: Math.max(0, 100 - v), itemStyle: { color: C.track }, silent: true, label: { show: false } }
      ]
    }]
  };
}

// ---- animated FUNNEL (adoption: registered→activated→weekly→power) ----
export function funnelOpt(steps: { name: string; value: number }[]) {
  return {
    ...ANIM, animationDuration: 1000,
    tooltip: { ...BASE_TT, trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'funnel', left: 8, right: 8, top: 8, bottom: 8,
      minSize: '24%', maxSize: '100%', sort: 'descending', gap: 4,
      funnelAlign: 'center',
      label: { show: true, position: 'inside', color: '#fff', fontFamily: FONT, fontWeight: 600, fontSize: 12, formatter: '{b}  {c}' },
      labelLine: { show: false },
      itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 6 },
      color: SERIES,
      data: steps.map((s, i) => ({ ...s, itemStyle: { color: barGrad(SERIES[i % SERIES.length]) } }))
    }]
  };
}

// ---- horizontal gradient progress bars (role adoption, pillars…) ----
export function hbarOpt(rows: { label: string; value: number }[], opts: { color?: string; max?: number } = {}) {
  const color = opts.color ?? C.blue;
  return {
    ...ANIM,
    grid: { left: 6, right: 36, top: 6, bottom: 6, containLabel: true },
    tooltip: { ...BASE_TT, axisPointer: { type: 'shadow' }, formatter: '{b}: {c}' },
    xAxis: { type: 'value', max: opts.max, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: { type: 'category', inverse: true, data: rows.map((r) => r.label), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.ink, fontFamily: FONT, fontSize: 12 } },
    series: [{
      type: 'bar', barWidth: 14,
      showBackground: true, backgroundStyle: { color: C.track, borderRadius: 8 },
      itemStyle: { color: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0, colorStops: [{ offset: 0, color: hexA(color, 0.7) }, { offset: 1, color }] }, borderRadius: 8 },
      label: { show: true, position: 'right', color: C.muted, fontFamily: FONT, fontSize: 11, formatter: '{c}' },
      data: rows.map((r) => r.value)
    }]
  };
}

// ---- activity heatmap (question volume: weekday × hour) ----
export function heatmapOpt(points: number[][], max: number) {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const hours = Array.from({ length: 24 }, (_, i) => (i % 3 === 0 ? String(i) : ''));
  return {
    animation: true,
    animationDuration: 700,
    animationEasing: 'cubicOut',
    tooltip: {
      position: 'top',
      backgroundColor: '#fff',
      borderColor: C.grid,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: C.ink, fontFamily: FONT, fontSize: 12 },
      extraCssText: 'border-radius:10px; box-shadow:none;',
      formatter: (p: any) => `${days[p.value[1]]} ${p.value[0]}:00 — ${p.value[2]} question${p.value[2] === 1 ? '' : 's'}`
    },
    grid: { left: 38, right: 12, top: 8, bottom: 22 },
    xAxis: {
      type: 'category', data: hours, splitArea: { show: false },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 10 }
    },
    yAxis: {
      type: 'category', data: days, splitArea: { show: false },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 10.5 }
    },
    visualMap: {
      min: 0, max: Math.max(1, max), show: false, calculable: false,
      inRange: { color: ['#f3f1ea', C.teal, C.blue] }
    },
    series: [{
      type: 'heatmap', data: points,
      itemStyle: { borderColor: '#fff', borderWidth: 1.5, borderRadius: 3 },
      emphasis: { itemStyle: { borderColor: C.ink, borderWidth: 1.5 } }
    }]
  };
}
