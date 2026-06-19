<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from '$lib/api';
  import { pickNode } from '$lib/dashutil';
  import { wsItem } from '$lib/dashstore';
  import BrainGraph from '$lib/BrainGraph.svelte';

  let latest = $state<any>(null);
  let runs = $state<any[]>([]);
  let cfg = $state<any>(null);
  let brainData = $state<any>(null);
  let running = $state(false);

  function load() {
    api.dreamLatest().then((r) => (latest = r?.dream || null)).catch(() => {});
    api.dreamRuns(8).then((r) => (runs = r?.runs || [])).catch(() => {});
    api.dreamConfig().then((r) => (cfg = r)).catch(() => {});
    api.brainGraph().then((r) => (brainData = r)).catch(() => {});
  }
  load();

  const poll = setInterval(() => { api.brainGraph().then((r) => (brainData = r)).catch(() => {}); }, 30000);
  onDestroy(() => clearInterval(poll));

  async function runNow() {
    running = true;
    try { await api.dreamRun(); load(); } catch {} finally { running = false; }
  }
  async function saveCfg(patch: any) {
    cfg = { ...cfg, ...patch };
    try { cfg = await api.dreamSaveConfig(patch); } catch {}
  }

  const stats = $derived(latest?.stats || {});
  const linkedTonight = $derived(latest?.touched?.linked_docs?.length || 0);
  function ago(iso: string) {
    if (!iso) return '—';
    const d = new Date(iso); const s = (Date.now() - d.getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }
  function runline(r: any) {
    const s = r?.stats || {};
    return `merged ${s.merged_facts ?? 0} · promoted ${s.promoted_facts ?? 0} · retired ${s.retired_facts ?? 0} · scored ${s.scored_pages ?? 0} · linked ${s.linked_edges ?? 0}`;
  }
</script>

<div class="bh">
  <div class="bhhead">
    <div>
      <h2>Self-improvement</h2>
      <p class="sub">Nightly dream cycle that consolidates the brain · interval {cfg?.interval_h ?? 24}h</p>
    </div>
    <button class="runbtn" onclick={runNow} disabled={running}>{running ? 'Running…' : 'Run now'}</button>
  </div>

  <div class="laststrip">
    <span class="dot"></span>
    <span class="lbl">Last run</span>
    <span class="val">{latest ? ago(latest.created_at) : 'never'}</span>
    {#if latest}<span class="sep">·</span><span class="lbl">{runline(latest)}</span>{/if}
  </div>

  <div class="card">
    <div class="cardhead">
      <span class="ct">Knowledge graph — what the agent knows</span>
      {#if linkedTonight > 0}<span class="badge">{linkedTonight} doc(s) linked last run</span>{/if}
      <button class="mlink" onclick={() => wsItem.set('brain-graph')}>open full graph →</button>
    </div>
    <div class="gwrap">
      {#if brainData && (brainData.nodes?.length ?? 0) > 0}
        <BrainGraph data={brainData} height={300} onpick={pickNode} />
      {:else}
        <div class="muted pad">No graph yet — upload documents to grow the brain.</div>
      {/if}
    </div>
  </div>

  <div class="tiles">
    <div class="tile"><div class="tl">Facts merged</div><div class="tv">{stats.merged_facts ?? 0}</div></div>
    <div class="tile"><div class="tl">Promoted</div><div class="tv">{stats.promoted_facts ?? 0}</div></div>
    <div class="tile"><div class="tl">Retired stale</div><div class="tv">{stats.retired_facts ?? 0}</div></div>
    <div class="tile"><div class="tl">Conflicts</div><div class="tv">{stats.conflicts_detected ?? 0}<span class="tsub">· {stats.conflicts_resolved ?? 0} resolved</span></div></div>
    <div class="tile"><div class="tl">Pages scored</div><div class="tv">{stats.scored_pages ?? 0}</div></div>
    <div class="tile"><div class="tl">Edges linked</div><div class="tv">{stats.linked_edges ?? 0}</div></div>
    <div class="tile"><div class="tl">Gap Q&amp;A</div><div class="tv">{stats.gap_qa ?? 0}</div></div>
  </div>

  {#if cfg}
    <div class="card">
      <div class="ct" style="margin-bottom:12px;">Settings</div>
      <label class="row"><span>Auto-resolve conflicts <em>— pick newer-doc winner</em></span>
        <input type="checkbox" checked={cfg.auto_resolve} onchange={(e) => saveCfg({ auto_resolve: (e.target as HTMLInputElement).checked })} /></label>
      <label class="row"><span>Fill coverage gaps <em>— costs LLM</em></span>
        <input type="checkbox" checked={cfg.gap_fill} onchange={(e) => saveCfg({ gap_fill: (e.target as HTMLInputElement).checked })} /></label>
      <label class="row"><span>Zero-LLM entity auto-linking</span>
        <input type="checkbox" checked={cfg.autolink} onchange={(e) => saveCfg({ autolink: (e.target as HTMLInputElement).checked })} /></label>
      <label class="row"><span>Interval (hours)</span>
        <span style="display:flex; align-items:center; gap:10px;">
          <input type="range" min="1" max="72" step="1" value={cfg.interval_h} onchange={(e) => saveCfg({ interval_h: +(e.target as HTMLInputElement).value })} />
          <b style="min-width:24px;">{cfg.interval_h}</b>
        </span></label>
    </div>
  {/if}

  <div class="ct" style="margin:4px 0 8px;">Recent runs</div>
  {#if runs.length}
    <div class="runs">
      {#each runs as r}
        <div class="runrow"><span class="rtime">{ago(r.created_at)}</span><span class="rline">{runline(r)}</span></div>
      {/each}
    </div>
  {:else}
    <div class="muted pad">No runs yet — press Run now or wait for the nightly cycle.</div>
  {/if}
</div>

<style>
  .bh { display: flex; flex-direction: column; gap: 14px; padding: 4px 2px 24px; }
  .bhhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .bhhead h2 { font-size: 17px; font-weight: 600; margin: 0; color: var(--ink); }
  .sub { font-size: 13px; color: #8a857c; margin: 3px 0 0; }
  .runbtn { background: var(--clay); color: #fff; border: none; border-radius: 9px; padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer; white-space: nowrap; }
  .runbtn:disabled { opacity: .6; cursor: default; }
  .laststrip { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; background: var(--cream); border-radius: 9px; padding: 9px 13px; }
  .laststrip .dot { width: 8px; height: 8px; border-radius: 50%; background: #3f8f5f; }
  .laststrip .lbl { font-size: 13px; color: #8a857c; }
  .laststrip .val { font-size: 13px; font-weight: 600; color: var(--ink); }
  .laststrip .sep { color: #c8c3b8; }
  .card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .cardhead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .ct { font-size: 14px; font-weight: 600; color: var(--ink); }
  .badge { font-size: 12px; color: #185fa5; background: #e6f1fb; padding: 3px 10px; border-radius: 8px; }
  .mlink { margin-left: auto; font-size: 12px; color: var(--clay); text-decoration: none; background: none; border: none; cursor: pointer; padding: 0; }
  .gwrap { min-height: 300px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .tile { background: var(--cream); border-radius: 9px; padding: 14px 16px; }
  .tile .tl { font-size: 13px; color: #8a857c; }
  .tile .tv { font-size: 24px; font-weight: 600; margin-top: 6px; color: var(--ink); }
  .tile .tsub { font-size: 12px; font-weight: 400; color: #8a857c; margin-left: 6px; }
  .row { display: flex; align-items: center; justify-content: space-between; font-size: 13px; padding: 6px 0; color: var(--ink); }
  .row em { color: #b0aa9d; font-style: normal; }
  .runs { display: flex; flex-direction: column; gap: 6px; }
  .runrow { display: flex; align-items: center; gap: 12px; background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 9px 13px; font-size: 13px; }
  .runrow .rtime { min-width: 80px; color: #8a857c; }
  .muted.pad { padding: 24px; color: #b7b1a4; font-size: 14px; }
</style>
