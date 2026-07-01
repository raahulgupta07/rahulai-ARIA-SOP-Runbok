<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { range, tick } from '$lib/dashstore';
  import EChart from '$lib/EChart.svelte';
  import { hbarOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  let s = $state<any>(null);
  let sec = $state<any>(null);
  $effect(() => {
    $tick;
    if (!isAdmin) return;
    api.ops().then((r) => (s = r)).catch(() => {});
    api.analyticsSecurity($range).then((r) => (sec = r)).catch(() => {});
  });
  const EVT: Record<string, { lab: string; c: string }> = {
    login_ok: { lab: 'login', c: '#3f8f5f' },
    login_fail: { lab: 'failed login', c: '#c0492f' },
    role_change: { lab: 'role change', c: '#7b6bd6' },
    user_create: { lab: 'user created', c: '#3f7fb0' },
    user_delete: { lab: 'user deleted', c: '#c0492f' },
    user_deactivate: { lab: 'deactivated', c: '#c98a2e' },
  };
  function evt(e: string) { return EVT[e] || { lab: e, c: 'var(--muted)' }; }
  function evtMeta(m: any) { if (!m) return ''; if (m.from && m.to) return `${m.from} → ${m.to}`; if (m.reason) return m.reason; if (m.role) return m.role; return ''; }

  function mb(b: number) { return b == null ? '—' : b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB'; }
  function dur(sec: number) { return sec == null ? '—' : sec >= 60 ? (sec / 60).toFixed(1) + 'm' : Math.round(sec) + 's'; }
  function ago(iso: string | null) {
    if (!iso) return 'never';
    const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
    if (h < 1) return Math.max(1, Math.round(h * 60)) + 'm ago';
    if (h < 48) return Math.round(h) + 'h ago';
    return Math.round(h / 24) + 'd ago';
  }
  const STATUS_C: Record<string, string> = { ready: '#3f8f5f', processing: '#c98a2e', queued: '#3f7fb0', failed: '#c0492f' };
  let ing = $derived(s?.ingest);
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if !s}
  <div class="muted pad">Loading…</div>
{:else}
  {@const daemonsOk = (s.daemons ?? []).filter((d: any) => d.enabled && !d.stale).length}
  {@const daemonsTot = (s.daemons ?? []).length}
  <div class="hero green">
    <div class="hero-main">
      <div class="hero-num">{daemonsOk}<small>/ {daemonsTot} live</small></div>
      <div class="hero-lbl">background daemons healthy</div>
      <div class="hero-delta {daemonsOk === daemonsTot ? 'up' : 'dn'}">{mb(s.storage?.db_bytes)} database</div>
    </div>
    <div class="hero-side">
      <div class="hs"><b>{ing?.queue_depth ?? '—'}</b>in queue</div>
      <div class="hs"><b>{ing?.stuck?.length ?? '—'}</b>stuck</div>
      <div class="hs"><b>{ing?.failed?.length ?? '—'}</b>failed</div>
      <div class="hs"><b>{sec?.kpis?.logins_fail ?? '—'}</b>failed logins</div>
    </div>
  </div>

  <!-- ingest health -->
  <div class="exgrid mt">
    <div class="excard">
      <div class="exh">Ingest pipeline</div>
      <div class="exbig" style="color:{ing.fail_rate ? '#c0492f' : '#3f8f5f'}">{ing.fail_rate}%<span class="exden">fail rate</span></div>
      <div class="exsub">{ing.total} docs · {ing.queue_depth} in queue</div>
      <div class="statusbar">
        {#each Object.entries(ing.by_status) as [st, n]}
          <span style="flex:{n}; background:{STATUS_C[st] || '#bbb'}" title="{st}: {n}"></span>
        {/each}
      </div>
      <div class="exrow" style="margin-top:8px">
        {#each Object.entries(ing.by_status) as [st, n]}
          <span><span class="dot" style="background:{STATUS_C[st] || '#bbb'}"></span>{st} {n}</span>
        {/each}
      </div>
    </div>
    <div class="excard">
      <div class="exh">Throughput</div>
      <div class="exbig">{dur(ing.avg_ingest_sec)}<span class="exden">avg / doc</span></div>
      <div class="exsub">{ing.ingested} processed</div>
      <div class="exmini">stuck threshold {s.thresholds.stuck_min}m</div>
    </div>
    <div class="excard">
      <div class="exh">Storage</div>
      <div class="exbig">{mb(s.storage.db_bytes)}<span class="exden">database</span></div>
      <div class="exsub">{s.storage.tables.length} tables tracked</div>
    </div>
  </div>

  <!-- daemons -->
  <div class="excard mt">
    <div class="exh">Background daemons <span class="exmini" style="float:right">stale if quiet &gt; {s.thresholds.stale_hrs}h</span></div>
    <table class="ptab">
      <thead><tr><th>Daemon</th><th>State</th><th>Last activity</th><th>Health</th></tr></thead>
      <tbody>
        {#each s.daemons as d}
          <tr>
            <td><b>{d.name}</b></td>
            <td class="muted">{d.enabled ? 'enabled' : 'off'}</td>
            <td class="muted">{ago(d.last_activity)}</td>
            <td>
              {#if !d.enabled}<span class="pill off">off</span>
              {:else if d.stale}<span class="pill warn">⚠ stale</span>
              {:else}<span class="pill ok">● live</span>{/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <!-- failed + stuck -->
  {#if ing.failed.length || ing.stuck.length}
    <div class="fw2 mt">
      <div class="excard">
        <div class="exh">Failed documents</div>
        {#if ing.failed.length}
          <table class="ptab">
            <thead><tr><th>Doc</th><th>Error</th><th>Age</th></tr></thead>
            <tbody>
              {#each ing.failed as f}
                <tr><td class="dname">{f.name}</td><td class="muted err">{f.error || '—'}</td><td class="muted">{f.age_h}h</td></tr>
              {/each}
            </tbody>
          </table>
        {:else}<div class="muted sm">No failed documents. ✓</div>{/if}
      </div>
      <div class="excard">
        <div class="exh">Stuck in pipeline <span class="exmini">&gt; {s.thresholds.stuck_min}m</span></div>
        {#if ing.stuck.length}
          <table class="ptab">
            <thead><tr><th>Doc</th><th>Status</th><th>Stuck</th></tr></thead>
            <tbody>
              {#each ing.stuck as st}
                <tr><td class="dname">{st.name}</td><td class="muted">{st.status} {st.progress}%</td><td class="muted">{st.age_min}m</td></tr>
              {/each}
            </tbody>
          </table>
        {:else}<div class="muted sm">Nothing stuck. ✓</div>{/if}
      </div>
    </div>
  {:else}
    <div class="excard mt"><div class="muted sm">Pipeline clean — no failed or stuck documents. ✓</div></div>
  {/if}

  <!-- ░ security ░ -->
  {#if sec}
    <div class="exgrid mt">
      <div class="excard">
        <div class="exh">Failed logins</div>
        <div class="exbig" style="color:{sec.kpis.logins_fail ? '#c0492f' : '#3f8f5f'}">{sec.kpis.logins_fail}<span class="exden">{sec.kpis.fail_rate}% of attempts</span></div>
        <div class="exsub">{sec.kpis.logins_ok} ok · {sec.kpis.fail_accounts} accounts</div>
      </div>
      <div class="excard">
        <div class="exh">Access changes</div>
        <div class="exbig">{sec.kpis.role_changes}<span class="exden">role changes</span></div>
        <div class="exsub">{sec.kpis.deactivations} deactivations</div>
      </div>
      <div class="excard">
        <div class="exh">Repeated failures <span class="exmini">brute-force watch</span></div>
        {#if sec.top_fail.length}
          <div class="rank" style="margin-top:4px">
            {#each sec.top_fail.slice(0, 5) as f}
              <div class="rrow"><span class="rl">{f.email || '—'}</span><span class="rv" style="color:#c0492f">{f.n}×</span></div>
            {/each}
          </div>
        {:else}<div class="muted sm">No failed logins. ✓</div>{/if}
      </div>
    </div>

    {#if sec.recent.length}
      <div class="excard mt">
        <div class="exh">Recent auth events</div>
        <table class="ptab">
          <thead><tr><th>When</th><th>Event</th><th>Account</th><th>By / detail</th></tr></thead>
          <tbody>
            {#each sec.recent as e}
              <tr>
                <td class="muted">{e.at}</td>
                <td><span style="color:{evt(e.event).c}; font-weight:600">{evt(e.event).lab}</span></td>
                <td>{e.email || '—'}</td>
                <td class="muted">{e.actor_email ? e.actor_email + ' ' : ''}{evtMeta(e.meta)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}

  <!-- biggest tables -->
  {#if s.storage.tables.length}
    <div class="excard mt">
      <div class="exh">Biggest tables <span class="exmini" style="float:right">growth watch · MB</span></div>
      <EChart
        option={hbarOpt(s.storage.tables.slice(0, 8).map((t: any) => ({ label: t.table, value: Math.round(t.bytes / 1e6) })), { color: C.violet })}
        height={s.storage.tables.slice(0, 8).length * 30}
      />
    </div>
  {/if}
{/if}

<style>
  .mt{margin-top:14px;}
  .statusbar{display:flex; height:9px; border-radius:99px; overflow:hidden; margin-top:12px; background:var(--sand);}
  .statusbar span{display:block;}
  .dot{display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px;}
  .exrow span{display:inline-flex; align-items:center; text-transform:capitalize;}
  .ptab{width:100%; border-collapse:collapse; font-size:12.5px; margin-top:8px;}
  .ptab th{text-align:left; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; padding:5px 8px; border-bottom:1px solid var(--line);}
  .ptab td{padding:7px 8px; border-bottom:1px solid var(--line); color:var(--ink); vertical-align:top;}
  .ptab tr:last-child td{border-bottom:none;}
  .ptab .muted{color:var(--muted);}
  .ptab td.dname{white-space:normal; max-width:280px;}
  .ptab .err{font-family:ui-monospace,monospace; font-size:11.5px; max-width:340px; white-space:normal;}
  .pill{font-size:11px; padding:2px 9px; border-radius:99px; font-weight:600;}
  .pill.ok{background:#e6f1ea; color:#3f8f5f;}
  .pill.warn{background:#f6e1da; color:#c0492f;}
  .pill.off{background:#eee; color:var(--muted);}
</style>
