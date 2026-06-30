<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { onDestroy } from 'svelte';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived(me?.role === 'admin');

  let ev = $state<any>(null);          // latest doc-eval payload {running,started_at,error,result}
  let loaded = $state(false);
  let starting = $state(false);
  let poll: any = null;

  let running = $derived(!!(ev?.running) || starting);
  let result = $derived(ev?.result ?? null);

  function load() {
    api.docEval().then((r) => { ev = r; loaded = true; manage(); }).catch(() => { loaded = true; });
  }
  // poll every 3s while a run is in flight; stop once it settles.
  function manage() {
    if (ev?.running) {
      if (!poll) poll = setInterval(load, 3000);
    } else if (poll) {
      clearInterval(poll); poll = null;
    }
  }
  async function runEval() {
    if (running) return;
    starting = true;
    try { await api.docEvalRun(6); } catch {}
    starting = false;
    load();
  }
  onDestroy(() => { if (poll) clearInterval(poll); });
  // initial load (admins only)
  $effect(() => { if (isAdmin && !loaded) load(); });

  function fmtTime(s: string | null) {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }
  // green ≥80, amber ≥50, red <50 (dashboard palette, non-coral)
  function hue(pct: number) { return pct >= 80 ? '#3f8f5f' : pct >= 50 ? '#c98a2e' : '#c0492f'; }
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if !loaded}
  <div class="muted pad">Loading…</div>
{:else}
  <div class="ahead">
    <div class="atitle">
      <h2>Answer accuracy (UAT)</h2>
      <p class="muted">Per-document grounded / right-doc / page-hit / faithful scores over golden Q&amp;A.</p>
    </div>
    <button class="runbtn" disabled={running} onclick={runEval}>
      {running ? 'Running… (~1-3 min)' : 'Run eval'}
    </button>
  </div>

  {#if ev?.error}
    <div class="errnote">Last run failed: {ev.error}</div>
  {/if}

  {#if !result}
    <div class="muted pad">No eval run yet — click Run eval.</div>
  {:else}
    {@const ov = result.overall}
    <!-- overall metric cards -->
    <div class="exgrid">
      <div class="excard">
        <div class="exh">Grounded</div>
        <div class="exbig" style="color:{hue(ov.grounded)}">{ov.grounded}%</div>
        <div class="exsub">answer backed by sources</div>
      </div>
      <div class="excard">
        <div class="exh">Right-doc</div>
        <div class="exbig" style="color:{hue(ov.right_doc)}">{ov.right_doc}%</div>
        <div class="exsub">cited the correct document</div>
      </div>
      <div class="excard">
        <div class="exh">Page-hit</div>
        <div class="exbig" style="color:{hue(ov.page_hit)}">{ov.page_hit}%</div>
        <div class="exsub">cited the correct page</div>
      </div>
      <div class="excard">
        <div class="exh">Faithful</div>
        <div class="exbig" style="color:{hue(ov.faithful)}">{ov.faithful}%</div>
        <div class="exsub">no contradiction with source</div>
      </div>
    </div>

    <div class="meta">
      <span><b>{result.questions ?? 0}</b> questions evaluated</span>
      <span class="dot">·</span>
      <span>last run {fmtTime(result.created_at)}</span>
      {#if running}<span class="dot">·</span><span class="live">running…</span>{/if}
    </div>

    <!-- per-doc table -->
    {#if result.docs?.length}
      <div class="excard mt">
        <div class="exh">Per-document accuracy</div>
        <table class="ptab">
          <thead>
            <tr><th>Document</th><th>Qs</th><th>Grounded</th><th>Right-doc</th><th>Page-hit</th><th>Faithful</th></tr>
          </thead>
          <tbody>
            {#each result.docs as d}
              <tr>
                <td><b>{d.name}</b></td>
                <td class="muted">{d.n}</td>
                <td><b style="color:{hue(d.pct.grounded)}">{d.pct.grounded}%</b></td>
                <td><b style="color:{hue(d.pct.right_doc)}">{d.pct.right_doc}%</b></td>
                <td><b style="color:{hue(d.pct.page_hit)}">{d.pct.page_hit}%</b></td>
                <td><b style="color:{hue(d.pct.faithful)}">{d.pct.faithful}%</b></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- coverage gaps -->
    {#if result.coverage_gaps?.length}
      <div class="excard mt">
        <div class="exh">Coverage gaps</div>
        <p class="muted gapnote">These documents have no golden Q&amp;A — not evaluable yet.</p>
        <div class="gaps">
          {#each result.coverage_gaps as g}
            <span class="gap">{g.name}</span>
          {/each}
        </div>
      </div>
    {/if}
  {/if}
{/if}

<style>
  .ahead{display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:14px; flex-wrap:wrap;}
  .atitle h2{font-family:var(--serif); font-size:24px; font-weight:500; margin:0; color:var(--ink);}
  .atitle p{font-size:13px; margin:4px 0 0;}
  .runbtn{background:#3f7fb0; color:#fff; border:none; border-radius:9px; padding:9px 16px; font-size:13px; font-weight:600; cursor:pointer; flex:none; white-space:nowrap;}
  .runbtn:disabled{background:#9bb6cc; cursor:default;}
  .errnote{background:#fbecea; color:#c0492f; border:1px solid #e7c2bb; border-radius:9px; padding:9px 12px; font-size:12.5px; margin-bottom:12px;}
  .meta{display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:12.5px; color:var(--muted); margin-top:12px;}
  .meta b{color:var(--ink);}
  .meta .dot{opacity:.5;}
  .meta .live{color:#3f7fb0; font-weight:600;}
  .mt{margin-top:14px;}
  .ptab{width:100%; border-collapse:collapse; font-size:13px; margin-top:8px;}
  .ptab th{text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; padding:6px 8px; border-bottom:1px solid var(--line, var(--border));}
  .ptab td{padding:7px 8px; border-bottom:1px solid var(--line, var(--border)); color:var(--ink);}
  .ptab tr:last-child td{border-bottom:none;}
  .ptab .muted{color:var(--muted);}
  .gapnote{font-size:12px; margin:6px 0 10px;}
  .gaps{display:flex; gap:8px; flex-wrap:wrap;}
  .gap{background:#f3f2ef; border:1px solid var(--border); border-radius:8px; padding:5px 10px; font-size:12.5px; color:var(--ink);}
</style>
