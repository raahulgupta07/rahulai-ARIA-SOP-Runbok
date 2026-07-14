<script lang="ts">
  import { api } from '$lib/api';

  let { compact = false } = $props();

  let v = $state<any>(null);
  let showAll = $state(false);

  $effect(() => {
    let alive = true;
    api.version().then((d) => { if (alive) v = d; }).catch(() => {});
    return () => { alive = false; };
  });

  function shortDate(s: string | undefined) {
    if (!s) return '';
    return String(s).slice(0, 10);
  }

  let changelog = $derived<any[]>(v?.changelog ?? []);
  let latest = $derived(changelog[0] ?? null);
</script>

<div class="wn">
  {#if !v}
    <div class="wn-loading">Loading…</div>
  {:else}
    <!-- header -->
    <div class="wn-head">
      <span class="wn-ver">v{v.version}</span>
      {#if v.up_to_date}
        <span class="wn-pill"><span class="wn-dot"></span>Up to date</span>
      {/if}
    </div>

    <!-- what's new heading -->
    <div class="wn-row">
      <span class="wn-h2" style="display:inline-flex;align-items:center;gap:6px"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z"/></svg> What's new</span>
      {#if changelog.length}
        <button class="wn-link" onclick={() => (showAll = !showAll)}>
          {showAll ? 'Show latest' : `See all (${changelog.length})`}
        </button>
      {/if}
    </div>

    {#if showAll}
      <div class="wn-list">
        {#each changelog as e}
          <div class="wn-entry">
            <div class="wn-etitle">
              <span class="wn-ev">v{e.version}</span>
              <span class="wn-etxt">{e.title}</span>
              {#if e.date}<span class="wn-edate">{shortDate(e.date)}</span>{/if}
            </div>
            <ul class="wn-lines">
              {#each (e.lines ?? []) as ln}<li>{ln}</li>{/each}
            </ul>
          </div>
        {/each}
      </div>
    {:else if latest}
      <div class="wn-entry">
        <div class="wn-etitle">
          <span class="wn-ev">v{latest.version}</span>
          <span class="wn-etxt">{latest.title}</span>
          {#if latest.date}<span class="wn-edate">{shortDate(latest.date)}</span>{/if}
        </div>
        <ul class="wn-lines">
          {#each (latest.lines ?? []) as ln}<li>{ln}</li>{/each}
        </ul>
      </div>
    {/if}
  {/if}
</div>

<style>
  /* --clay inherits the runtime-injected brand accent (no local override) */
  .wn { --ink:#211F1C; --muted:#73706A; --border:#E7E3D8;
    font-size:13px; color:var(--ink); }
  .wn-loading { color:var(--muted); font-size:13px; padding:8px 2px; }

  .wn-head { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
  .wn-ver { font-weight:700; color:var(--clay); font-size:14px; }
  .wn-sep { color:var(--muted); }
  .wn-sha { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:var(--muted); }
  .wn-built { font-size:12px; color:var(--muted); }
  .wn-pill { display:inline-flex; align-items:center; gap:5px; margin-left:4px;
    font-size:11px; font-weight:600; color:#3e7a44; background:#e8f2e6;
    border:1px solid #cfe4ca; border-radius:999px; padding:2px 9px; }
  .wn-dot { width:6px; height:6px; border-radius:999px; background:#5fa463; }

  .wn-row { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
  .wn-h2 { font-weight:600; font-size:13px; color:var(--ink); }
  .wn-link { background:none; border:0; cursor:pointer; color:var(--clay);
    font-size:12px; font-weight:500; }

  .wn-list { display:flex; flex-direction:column; gap:14px; }
  .wn-entry { border:1px solid var(--border); border-radius:11px; padding:11px 12px; background:#FAF9F5; }
  .wn-etitle { display:flex; align-items:center; gap:7px; margin-bottom:6px; flex-wrap:wrap; }
  .wn-ev { font-weight:700; color:var(--clay); font-size:12.5px; }
  .wn-etxt { font-weight:600; font-size:13px; }
  .wn-edate { margin-left:auto; font-size:11px; color:var(--muted); }
  .wn-lines { margin:0; padding-left:16px; display:flex; flex-direction:column; gap:3px; }
  .wn-lines li { font-size:12.5px; color:#46443f; line-height:1.45; }
</style>
