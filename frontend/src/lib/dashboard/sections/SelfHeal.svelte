<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { onDestroy, tick } from 'svelte';

  // reactive admin gate (cachedUser() is null at cold start → would stick on "Admin only")
  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));

  type Doc = { doc_id: number; name: string; status: string; accuracy: number; banked: number; review_n: number };
  type LogLine = { id: number; doc_id: number; tag: string; msg: string; ts: string };

  let data = $state<any>(null);
  let loaded = $state(false);
  let starting = $state(false);
  let logs = $state<LogLine[]>([]);
  let lastId = $state(0);

  let poll: any = null;        // overview poll (selfheal())
  let logPoll: any = null;     // log poll (selfhealLogs())
  let logBox: HTMLElement | null = null;

  let running = $derived(!!(data?.running) || starting);
  let docs = $derived<Doc[]>(data?.docs ?? []);
  let rounds = $derived<{ round: number; acc: number }[]>(data?.rounds ?? []);
  let review = $derived<{ doc: string; question: string; reason: string }[]>(data?.review ?? []);
  let curDoc = $derived(data?.current_doc ?? null);
  let hasRun = $derived(loaded && (docs.length > 0 || rounds.length > 0 || (data?.banked_total ?? 0) > 0));

  // colour per log tag (per the backend contract)
  const TAG_COLOR: Record<string, string> = {
    mine: '#3f7fb0', eval: '#7b6bd6', heal: '#2f8f83',
    judge: '#c98a2e', bank: '#3f8f5f', warn: '#c0492f', info: '#8a857c'
  };
  function tagColor(t: string) { return TAG_COLOR[t] ?? '#8a857c'; }

  function load() {
    api.selfheal().then((r) => { data = r; loaded = true; manage(); }).catch(() => { loaded = true; });
  }

  function loadLogs() {
    const docId = curDoc?.id ?? null;
    api.selfhealLogs(docId, lastId).then((rows: LogLine[]) => {
      if (!rows?.length) return;
      const fresh = rows.filter((r) => r.id > lastId);
      if (!fresh.length) return;
      logs = [...logs, ...fresh].slice(-300);   // cap to keep the DOM light
      lastId = Math.max(lastId, ...fresh.map((r) => r.id));
      scrollLog();
    }).catch(() => {});
  }

  async function scrollLog() {
    await tick();
    if (logBox) logBox.scrollTop = logBox.scrollHeight;
  }

  // poll overview every 3s + logs every 2.5s while running; stop when settled.
  function manage() {
    if (data?.running) {
      if (!poll) poll = setInterval(load, 3000);
      if (!logPoll) logPoll = setInterval(loadLogs, 2500);
    } else {
      if (poll) { clearInterval(poll); poll = null; }
      if (logPoll) { clearInterval(logPoll); logPoll = null; }
    }
  }

  async function runNow() {
    if (running) return;
    starting = true;
    try { await api.selfhealRun(null); } catch {}
    starting = false;
    load();
    loadLogs();
  }

  function pause() {
    // FE-only stop of the live polling; the backend run continues server-side.
    if (poll) { clearInterval(poll); poll = null; }
    if (logPoll) { clearInterval(logPoll); logPoll = null; }
    starting = false;
  }

  onDestroy(() => {
    if (poll) clearInterval(poll);
    if (logPoll) clearInterval(logPoll);
  });

  // initial load (admins only) + a first log pull
  $effect(() => { if (isAdmin && !loaded) { load(); loadLogs(); } });

  const maxAcc = $derived(Math.max(60, ...rounds.map((r) => r.acc)));
  function fmtTs(s: string) { return (s || '').slice(0, 8) || s; }
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if !loaded}
  <div class="muted pad">Loading…</div>
{:else}
  <div class="sh-head">
    <div class="sh-title">
      <h2>Self-Heal Agent</h2>
      <p class="muted">Mine golden Q&amp;A → re-answer from source → judge → bank verbatim. Accuracy climbs each round.</p>
    </div>
    {#if running}
      <span class="sh-pill"><span class="sh-dot"></span> Running{curDoc ? ` · ${curDoc.name}` : ''}</span>
    {/if}
  </div>

  <!-- agent strip: the three agents, self-heal active when running -->
  <div class="sh-agents">
    <div class="sh-agent"><span class="sh-ic" style="background:#c2683f"></span> <b>Answer agent</b> · serving chat</div>
    <div class="sh-agent"><span class="sh-ic" style="background:#7b6bd6"></span> <b>Dream cycle</b> · nightly · idle</div>
    <div class="sh-agent" class:on={running}>
      <span class="sh-ic" style="background:#2f8f83"></span> <b>Self-Heal agent</b> ·
      {running ? (curDoc ? `healing ${curDoc.name}` : 'healing…') : 'idle'}
    </div>
  </div>

  {#if !hasRun && !running}
    <div class="muted pad">No self-heal runs yet — click <b>Run self-heal now</b> to mine, heal and bank golden Q&amp;A.</div>
  {:else}
    <div class="sh-grid">

      <!-- LEFT: doc queue -->
      <div class="sh-card">
        <h3>Documents</h3>
        <div class="sh-q">
          {#each docs as d}
            {@const cur = curDoc && curDoc.id === d.doc_id}
            <div class="sh-qrow" class:cur={cur}>
              {#if d.status === 'running'}
                <span class="sh-spin"></span>
              {:else if d.status === 'queued' || d.status === 'pending'}
                <span class="sh-badge b-wait">queued</span>
              {:else if d.status === 'failed'}
                <span class="sh-badge b-fail">failed</span>
              {:else}
                <span class="sh-badge b-done">{d.accuracy}%</span>
              {/if}
              <div class="sh-qd">
                <b>{d.name}</b>
                <small>
                  {#if d.status === 'running'}healing…
                  {:else if d.status === 'pending' || d.status === 'queued'}waiting
                  {:else if d.review_n}{d.banked} banked · {d.review_n} review
                  {:else}healed · {d.banked} banked{/if}
                </small>
              </div>
            </div>
          {/each}
          {#if !docs.length}<div class="muted" style="padding:10px 8px;font-size:12.5px">No eligible documents.</div>{/if}
        </div>
      </div>

      <!-- CENTER: live log -->
      <div class="sh-card">
        <h3>Live activity{curDoc ? ` — ${curDoc.name}` : ''}</h3>
        <div class="sh-log" bind:this={logBox}>
          {#each logs as ln}
            <div class="sh-ln">
              <span class="sh-ts">{fmtTs(ln.ts)}</span>
              <span class="sh-tag" style="color:{tagColor(ln.tag)}">{ln.tag.toUpperCase()}</span>
              <span class="sh-msg">{ln.msg}</span>
            </div>
          {/each}
          {#if running}
            <div class="sh-curline"><span class="sh-caret"></span></div>
          {/if}
          {#if !logs.length && !running}
            <div class="muted" style="padding:10px 15px;font-size:12.5px">No activity logged yet.</div>
          {/if}
        </div>
      </div>

      <!-- RIGHT: metrics + review + controls -->
      <div class="sh-card">
        <h3>Accuracy climb</h3>
        <div class="sh-mwrap">
          {#if rounds.length}
            <div class="sh-climb">
              {#each rounds as r}
                <div class="sh-bar" style="height:{Math.max(8, (r.acc / maxAcc) * 100)}%">
                  <span>{r.acc}%</span><small>r{r.round}</small>
                </div>
              {/each}
            </div>
          {:else}
            <p class="muted" style="font-size:12.5px;margin:8px 0">No rounds yet.</p>
          {/if}

          <div class="sh-kpis">
            <div class="sh-kpi"><b style="color:#3f8f5f">{rounds.length ? rounds[rounds.length - 1].acc : 0}%</b><small>grounded</small></div>
            <div class="sh-kpi"><b style="color:#3f8f5f">{data?.banked_total ?? 0}</b><small>banked</small></div>
            <div class="sh-kpi"><b style="color:#c98a2e">{data?.review_total ?? 0}</b><small>review</small></div>
          </div>

          <h3 style="padding-left:0">Needs human check</h3>
          <div class="sh-rev">
            {#each review as rv}
              <div class="sh-revrow"><span class="sh-qm">?</span><div>{rv.doc} · "{rv.question}" — {rv.reason}</div></div>
            {/each}
            {#if !review.length}<div class="muted" style="font-size:12.5px;padding:8px 0">Nothing flagged.</div>{/if}
          </div>

          <div class="sh-ctrls">
            <button class="sh-btn" disabled={running} onclick={runNow}>
              {running ? 'Running…' : 'Run self-heal now'}
            </button>
            <button class="sh-btn ghost" disabled={!running} onclick={pause}>Pause</button>
          </div>
          <div class="sh-note">Auto-runs nightly (dream cycle) + as a parallel worker when a new doc reaches “ready”.</div>
        </div>
      </div>

    </div>
  {/if}
{/if}

<style>
  .sh-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:14px; flex-wrap:wrap; }
  .sh-title h2 { font-family:var(--serif); font-size:24px; font-weight:500; margin:0; color:var(--ink); }
  .sh-title p { font-size:13px; margin:4px 0 0; }
  .sh-pill { display:flex; align-items:center; gap:8px; background:#e4f1ef; color:#2f8f83; font-weight:600; font-size:12px; padding:6px 12px; border-radius:999px; flex:none; }
  .sh-dot { width:8px; height:8px; border-radius:50%; background:#2f8f83; position:relative; }
  .sh-dot::after { content:""; position:absolute; inset:-4px; border-radius:50%; background:#2f8f83; opacity:.35; animation:sh-pulse 1.4s ease-out infinite; }
  @keyframes sh-pulse { 0%{transform:scale(.6);opacity:.5} 100%{transform:scale(2.2);opacity:0} }

  .sh-agents { display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap; }
  .sh-agent { display:flex; align-items:center; gap:9px; background:#fff; border:1px solid var(--border); border-radius:12px; padding:9px 13px; font-size:12.5px; color:var(--muted); }
  .sh-agent b { color:var(--ink); font-weight:600; }
  .sh-agent.on { border-color:#2f8f83; background:#e4f1ef; }
  .sh-ic { width:9px; height:9px; border-radius:50%; }

  .sh-grid { display:grid; grid-template-columns:240px 1fr 300px; gap:16px; align-items:start; }
  .sh-card { background:#fff; border:1px solid var(--border); border-radius:14px; }
  .sh-card h3 { margin:0; padding:13px 15px 9px; font-size:12px; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); font-weight:600; }

  .sh-q { padding:0 8px 10px; }
  .sh-qrow { display:flex; align-items:center; gap:9px; padding:9px 8px; border-radius:9px; font-size:13px; }
  .sh-qrow:hover { background:var(--sand); }
  .sh-qrow.cur { background:#e4f1ef; }
  .sh-qd { flex:1; min-width:0; overflow:hidden; }
  .sh-qd b { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500; color:var(--ink); }
  .sh-qd small { display:block; color:var(--muted); font-size:11px; }
  .sh-badge { font-size:11px; font-weight:600; padding:2px 7px; border-radius:6px; flex:none; }
  .b-done { background:#e7f3ec; color:#3f8f5f; }
  .b-wait { background:#f1efe9; color:var(--muted); }
  .b-fail { background:#fbecea; color:#c0492f; }
  .sh-spin { width:13px; height:13px; border:2px solid #2f8f83; border-top-color:transparent; border-radius:50%; animation:sh-sp .8s linear infinite; flex:none; }
  @keyframes sh-sp { to { transform:rotate(360deg) } }

  .sh-log { padding:6px 0 12px; font:12.5px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace; max-height:430px; overflow:auto; }
  .sh-ln { display:flex; gap:10px; padding:2px 15px; }
  .sh-ln:hover { background:var(--sand); }
  .sh-ts { color:#b8b2a6; flex:none; }
  .sh-tag { flex:none; font-weight:700; width:58px; }
  .sh-msg { color:#3a372f; word-break:break-word; }
  .sh-curline { padding:4px 15px; }
  .sh-caret { width:7px; height:15px; background:#2f8f83; display:inline-block; animation:sh-blink 1s steps(1) infinite; vertical-align:middle; }
  @keyframes sh-blink { 50% { opacity:0 } }

  .sh-mwrap { padding:6px 15px 16px; }
  .sh-climb { display:flex; align-items:flex-end; gap:8px; height:92px; margin:18px 0 22px; }
  .sh-bar { flex:1; background:linear-gradient(#2f8f83,#7cc4ba); border-radius:6px 6px 0 0; position:relative; }
  .sh-bar span { position:absolute; top:-18px; left:0; right:0; text-align:center; font-size:11px; font-weight:700; color:#2f8f83; }
  .sh-bar small { position:absolute; bottom:-18px; left:0; right:0; text-align:center; font-size:10px; color:var(--muted); }
  .sh-kpis { display:flex; gap:8px; margin:10px 0 14px; }
  .sh-kpi { flex:1; background:var(--sand); border-radius:10px; padding:10px; text-align:center; }
  .sh-kpi b { display:block; font-size:20px; }
  .sh-kpi small { color:var(--muted); font-size:11px; }
  .sh-rev { font-size:12.5px; }
  .sh-revrow { display:flex; gap:8px; padding:8px 0; border-top:1px dashed var(--border); color:#4a463e; }
  .sh-qm { color:#c98a2e; font-weight:700; flex:none; }
  .sh-ctrls { margin-top:14px; display:flex; gap:8px; }
  .sh-btn { background:#2f8f83; color:#fff; border:none; border-radius:9px; padding:8px 14px; font-weight:600; font-size:13px; cursor:pointer; }
  .sh-btn.ghost { background:transparent; color:#2f8f83; border:1px solid #2f8f83; }
  .sh-btn:disabled { opacity:.5; cursor:default; }
  .sh-btn.ghost:disabled { opacity:.45; }
  .sh-note { font-size:11px; color:var(--muted); margin-top:9px; }

  @media (max-width:980px) { .sh-grid { grid-template-columns:1fr; } }
</style>
