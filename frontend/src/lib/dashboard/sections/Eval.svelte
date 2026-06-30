<script lang="ts">
  // Eval Agent — offline answer-quality scoring dashboard.
  // Nightly + on-demand. Shows corpus health, run history, per-doc scores, and a
  // drill-down with every golden question, the agent's answer, and judge reasoning.
  import { api } from '$lib/api';
  import { tick } from '$lib/dashstore';
  import { onMount } from 'svelte';

  let st = $state<any>(null);          // /eval/status
  let docs = $state<any[]>([]);        // /eval/docs
  let loading = $state(true);
  let busy = $state(false);            // force-run in flight
  let openDoc = $state<number | null>(null);
  let detail = $state<any>(null);      // /eval/doc/{id}
  let detailLoading = $state(false);
  let detailTab = $state<'q' | 'how'>('q');

  // left-rail view + doc filter
  let view = $state<'overview' | 'runs' | 'questions'>('overview');
  let docFilter = $state<'all' | 'review' | 'lo' | 'halluc' | 'never'>('all');
  function railView(v: 'overview' | 'runs' | 'questions', f: typeof docFilter = 'all') {
    view = v; docFilter = f; closeDrill();
  }
  const filteredDocs = $derived(docs.filter((d) => {
    if (docFilter === 'review') return d.needs_review;
    if (docFilter === 'lo') return d.score_pct != null && d.score_pct < 70;
    if (docFilter === 'halluc') return d.halluc_count > 0;
    if (docFilter === 'never') return d.evaluated_at == null;
    return true;
  }));

  async function load() {
    try {
      const [s, d] = await Promise.all([api.evalStatus(), api.evalDocs()]);
      st = s;
      docs = d.docs || [];
    } catch (e) { /* fail-soft */ } finally { loading = false; }
  }
  onMount(load);
  // refresh on the dashboard heartbeat (faster while a run is active)
  $effect(() => { $tick; load(); });

  async function runNow() {
    if (busy) return;
    busy = true;
    try { await api.evalRun(); await load(); } catch (e) {} finally { busy = false; }
  }
  async function togglePause() {
    if (!st) return;
    try { st.paused ? await api.evalResume() : await api.evalPause(); await load(); } catch (e) {}
  }
  async function setConc(n: number) {
    try { await api.evalConcurrency(n); await load(); } catch (e) {}
  }

  async function drill(docId: number) {
    openDoc = docId;
    detail = null;
    detailTab = 'q';
    detailLoading = true;
    try { detail = await api.evalDoc(docId); } catch (e) {} finally { detailLoading = false; }
  }
  function closeDrill() { openDoc = null; detail = null; }

  const kpi = $derived(st?.kpis || {});
  const run = $derived(st?.last_run || null);
  const runActive = $derived(st?.run_active || null);
  function sclass(s: number | null) {
    if (s == null) return 'mid';
    return s >= 80 ? 'hi' : s >= 70 ? 'mid' : 'lo';
  }
  function delta(cur: number | null, prev: number | null) {
    if (cur == null || prev == null) return null;
    return Math.round((cur - prev) * 10) / 10;
  }
  function ago(ts: string | null) {
    if (!ts) return '—';
    const d = (Date.now() - new Date(ts).getTime()) / 1000;
    if (d < 90) return 'just now';
    if (d < 5400) return Math.round(d / 60) + 'm ago';
    if (d < 129600) return Math.round(d / 3600) + 'h ago';
    return Math.round(d / 86400) + 'd ago';
  }
  function dur(a: string | null, b: string | null) {
    if (!a || !b) return '—';
    const s = Math.max(0, (new Date(b).getTime() - new Date(a).getTime()) / 1000);
    return s < 60 ? Math.round(s) + 's' : Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
  }
</script>

<div class="eval-wrap">
<!-- left rail -->
<aside class="erail">
  <div class="rgrp">Eval</div>
  <button class="ritem" class:on={view === 'overview' && docFilter === 'all'} onclick={() => railView('overview')}>
    <span class="ic">📊</span> Overview <span class="rn">{kpi.docs ?? 0}</span></button>
  <button class="ritem" class:on={view === 'runs'} onclick={() => railView('runs')}>
    <span class="ic">🌙</span> Nightly runs <span class="rn">{st?.runs?.length ?? 0}</span></button>
  <button class="ritem" class:on={docFilter === 'review'} onclick={() => railView('overview', 'review')}>
    <span class="ic">⚠️</span> Needs review <span class="rn">{kpi.needs_review ?? 0}</span></button>
  <button class="ritem" class:on={view === 'questions'} onclick={() => railView('questions')}>
    <span class="ic">❓</span> Golden questions <span class="rn">{kpi.golden_questions ?? 0}</span></button>
  <button class="ritem" class:on={docFilter === 'halluc'} onclick={() => railView('overview', 'halluc')}>
    <span class="ic">🟣</span> Hallucinations <span class="rn">{kpi.halluc ?? 0}</span></button>
  <div class="rgrp" style="margin-top:14px">Filter docs</div>
  <button class="ritem sm" class:on={view === 'overview' && docFilter === 'all'} onclick={() => railView('overview', 'all')}>All docs</button>
  <button class="ritem sm" class:on={docFilter === 'lo'} onclick={() => railView('overview', 'lo')}>Score &lt; 70%</button>
  <button class="ritem sm" class:on={docFilter === 'halluc'} onclick={() => railView('overview', 'halluc')}>Hallucinated</button>
  <button class="ritem sm" class:on={docFilter === 'never'} onclick={() => railView('overview', 'never')}>Never evaluated</button>
</aside>

<div class="eval-shell">
  <div class="chead">
    <div>
      <h1>Eval Agent</h1>
      <div class="sub">Offline answer-quality scoring — runs nightly, no upload / answer-time cost, no user thumbs needed.</div>
    </div>
    <button class="ghost" onclick={togglePause} disabled={!st}>{st?.paused ? '▶ Resume' : '⏸ Pause'}</button>
    <button class="runbtn" onclick={runNow} disabled={busy || !!runActive}>
      {busy || runActive ? '⏳ Running…' : '▶ Run eval now'}
    </button>
  </div>

  <!-- schedule + models banner -->
  <div class="sched">
    <span class="dot" class:off={!st?.running}></span>
    {#if runActive}
      <span><b>Eval running now</b> · {runActive.trigger} pass · {runActive.done}/{runActive.total} docs scored</span>
    {:else}
      <span><b>{st?.enabled ? 'Nightly eval ON' : 'Eval disabled'}</b>
        · next run {String(st?.nightly_hour ?? 2).padStart(2, '0')}:00
        · nightly re-scores only docs changed since last run (watermark)</span>
    {/if}
    <span class="when">
      {#if run}last run {ago(run.finished_at)} · {run.n_docs} docs · {dur(run.started_at, run.finished_at)}{/if}
    </span>
  </div>

  {#if view === 'overview'}
  <!-- which LLMs are working -->
  <div class="models">
    <div class="ml"><span class="mk">Answering model</span><span class="mv">{st?.answer_model || '—'}</span></div>
    <div class="ml"><span class="mk">Judge model</span><span class="mv">{st?.judge_model || '—'}</span></div>
    <div class="ml">
      <span class="mk">Parallel docs</span>
      <span class="seg">
        {#each [1, 2, 4] as n}
          <button class:on={st?.concurrency === n} onclick={() => setConc(n)}>{n}</button>
        {/each}
      </span>
    </div>
    {#if st?.scoring?.length}
      <div class="ml grow"><span class="mk">Scoring now</span>
        <span class="mv live">{st.scoring.map((d: any) => d.name).join(' · ')}</span></div>
    {/if}
  </div>

  <!-- KPIs -->
  <div class="kpis">
    <div class="kpi"><div class="k">Corpus score</div>
      <div class="v">{kpi.corpus != null ? kpi.corpus + '%' : '—'}</div>
      {#if delta(kpi.corpus, st?.prev_score) != null}
        <div class="d {delta(kpi.corpus, st?.prev_score)! >= 0 ? 'up' : 'down'}">
          {delta(kpi.corpus, st?.prev_score)! >= 0 ? '▲' : '▼'} {Math.abs(delta(kpi.corpus, st?.prev_score)!)}% vs last run</div>
      {/if}
    </div>
    <div class="kpi"><div class="k">Grounded answers</div>
      <div class="v">{kpi.grounded != null ? kpi.grounded + '%' : '—'}</div>
      <div class="d muted">{kpi.docs || 0} docs evaluated</div></div>
    <div class="kpi"><div class="k">Hallucinations</div>
      <div class="v">{kpi.halluc ?? 0}</div>
      <div class="d {(kpi.halluc ?? 0) > 0 ? 'down' : 'muted'}">negatives the agent answered</div></div>
    <div class="kpi"><div class="k">Negatives caught</div>
      <div class="v">{kpi.neg_caught ?? 0}/{kpi.neg_total ?? 0}</div>
      <div class="d muted">{kpi.golden_questions ?? 0} golden questions</div></div>
  </div>
  {/if}

  {#if view === 'overview' || view === 'runs'}
  <!-- run history -->
  <div class="sectitle">Nightly run history</div>
  {#if !st?.runs?.length}
    <div class="empty">No eval runs yet. Hit <b>Run eval now</b> to score all ready docs, or wait for the {String(st?.nightly_hour ?? 2).padStart(2, '0')}:00 nightly pass.</div>
  {:else}
    <div class="runs">
      {#each st.runs as r}
        <div class="runrow">
          <div class="cal">{r.status === 'running' ? '…' : (r.score_pct != null ? Math.round(r.score_pct) + '%' : '—')}</div>
          <div>
            <div class="rt">{r.trigger === 'manual' ? 'Manual run' : 'Nightly run'} <span class="rid">#{r.id}</span></div>
            <div class="rm">{r.n_docs} docs · {r.n_total} questions · {r.status === 'running' ? 'running…' : dur(r.started_at, r.finished_at)} · judge {r.judge_model}</div>
          </div>
          <div class="rspacer"></div>
          {#if r.status !== 'running'}
            <span class="scorepill s-{sclass(r.score_pct)}">{r.score_pct != null ? r.score_pct + '%' : '—'}</span>
          {:else}<span class="scorepill s-mid">running</span>{/if}
        </div>
      {/each}
    </div>
  {/if}
  {/if}

  {#if view !== 'runs'}
  <!-- per-doc health -->
  <div class="sectitle">
    {view === 'questions' ? 'Golden questions by document' : 'Per-document health'}
    <span class="hint">— click to drill into every question + judge reasoning</span>
    {#if docFilter !== 'all'}<span class="filterchip">filter: {docFilter === 'lo' ? 'score < 70%' : docFilter === 'review' ? 'needs review' : docFilter === 'halluc' ? 'hallucinated' : 'never evaluated'} · {filteredDocs.length} <button class="clr" onclick={() => railView('overview', 'all')}>clear</button></span>{/if}
  </div>
  {#if !filteredDocs.length}
    <div class="empty">{docs.length ? 'No documents match this filter.' : 'No documents scored yet.'}</div>
  {:else}
    <div class="docgrid">
      {#each filteredDocs as d}
        <button class="dh" class:flag={d.needs_review} onclick={() => drill(d.doc_id)}>
          <div class="top2">
            <div class="thumb"></div>
            <div><div class="dn">{d.name}</div><div class="dm">{d.n_total} questions · {ago(d.evaluated_at)}</div></div>
            <div class="ring {sclass(d.score_pct)}">{d.score_pct != null ? d.score_pct + '%' : '—'}</div>
          </div>
          <div class="metrics">
            <div class="m"><b>{d.n_pass}/{d.n_total}</b><span>passed</span></div>
            <div class="m"><b>{d.grounded_pct != null ? d.grounded_pct + '%' : '—'}</b><span>grounded</span></div>
            <div class="m" class:bad={d.halluc_count > 0}><b>{d.halluc_count}</b><span>hallucinated</span></div>
            <div class="m"><b>{d.neg_caught}/{d.neg_total}</b><span>negatives caught</span></div>
          </div>
          {#if d.needs_review}
            <div class="needs">⚠️ Needs review{d.halluc_count > 0 ? ` — agent answered ${d.halluc_count} question${d.halluc_count > 1 ? 's' : ''} not in doc` : ' — score below 70%'}</div>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
  {/if}
</div>
</div>

<!-- DRILL-DOWN PANEL -->
{#if openDoc != null}
  <div class="panel-scrim" onclick={closeDrill} role="presentation"></div>
  <div class="panel">
    <div class="ph">
      <button class="x" onclick={closeDrill}>✕</button>
      <div class="t">{detail?.head?.name || 'Document'}</div>
      <div class="s">
        {#if detail?.head}
          Score {detail.head.score_pct}% · {detail.head.n_pass}/{detail.head.n_total} passed · evaluated {ago(detail.head.evaluated_at)}
          · judge {st?.judge_model}{detail.head.needs_review ? ' · ⚠️ needs review' : ''}
        {:else}loading…{/if}
      </div>
    </div>
    <div class="pbody">
      <div class="tabs">
        <button class="tab" class:on={detailTab === 'q'} onclick={() => (detailTab = 'q')}>Questions {detail?.results?.length ? `(${detail.results.length})` : ''}</button>
        <button class="tab" class:on={detailTab === 'how'} onclick={() => (detailTab = 'how')}>How scored</button>
      </div>

      {#if detailTab === 'how'}
        <div class="how">
          <p><b>The Eval Agent runs a separate nightly pass</b> — never at upload or answer time, so users feel zero cost.</p>
          <ol>
            <li><b>Golden questions</b> are auto-generated once per doc from its text by the judge model, then reused.</li>
            <li>Each question is asked through the <b>real retrieval + answer path</b> (same as a live user).</li>
            <li>An <b>LLM judge</b> ({st?.judge_model}) grades the answer → pass / partial / fail with a reason.</li>
          </ol>
          <p class="kinds">
            <span class="kind k-pos">positive</span> answer is in the doc → must answer correctly.
            <span class="kind k-neg">negative</span> NOT in the doc → must refuse (else = hallucination).
            <span class="kind k-adv">adversarial</span> false premise → must correct it.
          </p>
          <p class="muted">Score = (pass + ½·partial) / total. A doc is flagged <b>needs review</b> when its score &lt; 70% or it answered any negative question.</p>
        </div>
      {:else if detailLoading}
        <div class="empty">Loading questions…</div>
      {:else if !detail?.results?.length}
        <div class="empty">No question results for this doc's latest run yet.</div>
      {:else}
        {#each detail.results as q}
          <div class="q">
            <div class="qh">
              <span class="kind k-{q.kind === 'negative' ? 'neg' : q.kind === 'adversarial' ? 'adv' : 'pos'}">{q.kind}</span>
              <span class="scoremini">judge {q.judge_score != null ? q.judge_score.toFixed(2) : '—'}</span>
              <span class="verdict v-{q.verdict === 'pass' ? 'pass' : q.verdict === 'partial' ? 'part' : 'fail'}">{q.verdict?.toUpperCase()}</span>
            </div>
            <div class="qq">{q.question}</div>
            {#if q.expected}<div class="exp"><span class="lbl">Expected</span>{q.expected}</div>{/if}
            <div class="got" class:bad={q.verdict === 'fail'}><span class="lbl">Agent answered{q.retrieved_n != null ? ` · ${q.retrieved_n} pages retrieved` : ''}</span>{q.got || '(no answer)'}</div>
            {#if q.judge_reason}<div class="why"><span class="lbl">Judge</span>{q.judge_reason}</div>{/if}
          </div>
        {/each}
      {/if}
    </div>
  </div>
{/if}

<style>
  /* scoped semantic tokens — NOT in app.css :root, must declare locally */
  .eval-wrap, .panel {
    --line: #e0dfda; --coral: var(--brand, #d97757); --blue: #3f7fb0;
    --green: #3f8f5f; --amber: #c98a2e; --red: #c0492f; --violet: #7b6bd6;
    --hover: #f1f0ec; --navpill: #efeae1;
  }
  .eval-wrap { display: flex; align-items: flex-start; gap: 0; height: 100%; }
  /* left rail */
  .erail { width: 216px; flex: none; align-self: stretch; border-right: 1px solid var(--line); padding: 16px 11px; background: var(--sand); overflow: auto; }
  .rgrp { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 700; padding: 6px 8px; }
  .ritem { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; padding: 8px 12px; border-radius: 9px; font-size: 13.5px; font-weight: 500; color: #46443f; cursor: pointer; background: none; border: none; }
  .ritem:hover { background: var(--hover); }
  .ritem.on { background: var(--navpill); color: var(--ink); font-weight: 600; }
  .ritem .ic { width: 16px; text-align: center; }
  .ritem .rn { margin-left: auto; font-size: 11px; font-weight: 700; color: var(--muted); background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 0 6px; }
  .ritem.sm { font-size: 13px; padding: 7px 12px 7px 14px; color: #5a5852; }
  .filterchip { font-weight: 500; text-transform: none; letter-spacing: 0; font-size: 11.5px; color: var(--coral); margin-left: 8px; }
  .filterchip .clr { border: none; background: none; color: var(--muted); text-decoration: underline; cursor: pointer; font-size: 11px; margin-left: 4px; }
  .eval-shell { flex: 1; min-width: 0; padding: 22px 26px 48px; color: var(--ink); overflow: auto; height: 100%; }
  h1 { font-size: 19px; margin: 0; }
  .chead { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 16px; }
  .chead > div:first-child { flex: 1; }
  .sub { color: var(--muted); font-size: 12.5px; margin-top: 3px; }
  .runbtn { background: var(--coral); color: #fff; border: none; border-radius: 9px; padding: 8px 15px; font-weight: 600; font-size: 13px; cursor: pointer; }
  .runbtn:disabled { opacity: .6; cursor: default; }
  .ghost { background: #fff; border: 1px solid var(--line); color: var(--ink); border-radius: 9px; padding: 8px 14px; font-weight: 600; font-size: 13px; cursor: pointer; }
  .sched { display: flex; align-items: center; gap: 10px; background: #fdf6f1; border: 1px solid #eccdbd; border-radius: 11px; padding: 11px 15px; margin-bottom: 14px; font-size: 13px; }
  .sched .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); flex: none; }
  .sched .dot.off { background: var(--muted); }
  .sched b { color: var(--ink); }
  .sched .when { margin-left: auto; color: var(--muted); font-size: 12px; white-space: nowrap; }
  .models { display: flex; gap: 22px; flex-wrap: wrap; align-items: center; padding: 0 4px 18px; }
  .ml { display: flex; flex-direction: column; gap: 3px; }
  .ml.grow { flex: 1; min-width: 180px; }
  .mk { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; }
  .mv { font-size: 12.5px; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }
  .mv.live { color: var(--coral); font-family: var(--font); }
  .seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; width: max-content; }
  .seg button { border: none; background: #fff; padding: 4px 11px; font-size: 12px; color: var(--muted); cursor: pointer; }
  .seg button.on { background: var(--navpill); color: var(--ink); font-weight: 700; }
  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
  .kpi { border: 1px solid var(--line); border-radius: 13px; padding: 14px 15px; }
  .kpi .k { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }
  .kpi .v { font-size: 26px; font-weight: 700; margin-top: 5px; }
  .kpi .d { font-size: 11.5px; margin-top: 3px; }
  .up { color: var(--green); } .down { color: var(--red); } .muted { color: var(--muted); }
  .sectitle { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: 6px 0 12px; }
  .hint { font-weight: 500; text-transform: none; letter-spacing: 0; }
  .empty { border: 1px dashed var(--line); border-radius: 12px; padding: 22px; text-align: center; color: var(--muted); font-size: 13px; margin-bottom: 24px; }
  .runs { border: 1px solid var(--line); border-radius: 13px; overflow: hidden; margin-bottom: 26px; }
  .runrow { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border-bottom: 1px solid var(--line); }
  .runrow:last-child { border-bottom: none; }
  .runrow .cal { width: 40px; height: 34px; border-radius: 8px; background: #eef2f6; border: 1px solid #dde6ee; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 800; color: var(--blue); flex: none; }
  .runrow .rt { font-weight: 600; }
  .runrow .rid { color: var(--muted); font-weight: 500; font-size: 11px; }
  .runrow .rm { font-size: 11.5px; color: var(--muted); margin-top: 1px; }
  .rspacer { flex: 1; }
  .scorepill { font-size: 13px; font-weight: 800; padding: 3px 11px; border-radius: 999px; white-space: nowrap; }
  .s-hi { background: #e6f3ec; color: var(--green); }
  .s-mid { background: #fbf3e3; color: var(--amber); }
  .s-lo { background: #f8e9e6; color: var(--red); }
  .docgrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 26px; }
  .dh { text-align: left; border: 1px solid var(--line); border-radius: 13px; padding: 14px 15px; cursor: pointer; background: #fff; font: inherit; }
  .dh:hover { border-color: var(--coral); }
  .dh.flag { border-color: #eccdbd; background: #fdf8f5; }
  .dh .top2 { display: flex; align-items: center; gap: 10px; }
  .dh .thumb { width: 28px; height: 34px; border-radius: 6px; background: #eef2f6; border: 1px solid #dde6ee; flex: none; }
  .dh .dn { font-weight: 650; font-size: 13.5px; }
  .dh .dm { font-size: 11px; color: var(--muted); }
  .dh .ring { margin-left: auto; font-size: 16px; font-weight: 800; }
  .ring.hi { color: var(--green); } .ring.mid { color: var(--amber); } .ring.lo { color: var(--red); }
  .metrics { display: flex; gap: 16px; margin-top: 11px; font-size: 11.5px; }
  .metrics .m b { display: block; font-size: 15px; font-weight: 700; }
  .metrics .m span { color: var(--muted); }
  .metrics .m.bad b { color: var(--red); }
  .needs { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; color: var(--red); margin-top: 9px; }
  /* drill-down */
  .panel-scrim { position: fixed; inset: 0; background: rgba(0,0,0,.16); z-index: 40; }
  .panel { position: fixed; top: 0; right: 0; width: 560px; max-width: 94vw; height: 100vh; background: #fff; border-left: 1px solid var(--line); box-shadow: -14px 0 44px rgba(0,0,0,.12); display: flex; flex-direction: column; z-index: 41; }
  .ph { padding: 16px 20px; border-bottom: 1px solid var(--line); }
  .ph .x { float: right; border: none; background: none; color: var(--muted); font-size: 16px; cursor: pointer; }
  .ph .t { font-size: 17px; font-weight: 700; padding-right: 24px; }
  .ph .s { font-size: 12px; color: var(--muted); margin-top: 3px; }
  .pbody { flex: 1; overflow: auto; padding: 14px 18px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid var(--line); }
  .tab { padding: 7px 13px; font-size: 12.5px; font-weight: 600; color: var(--muted); cursor: pointer; border: none; background: none; border-bottom: 2px solid transparent; }
  .tab.on { color: var(--ink); border-bottom-color: var(--coral); }
  .how { font-size: 13px; line-height: 1.6; }
  .how ol { padding-left: 18px; } .how li { margin-bottom: 6px; }
  .how .kinds { display: flex; flex-direction: column; gap: 6px; background: var(--sand); border-radius: 10px; padding: 12px; }
  .q { border: 1px solid var(--line); border-radius: 11px; padding: 12px 13px; margin-bottom: 10px; }
  .qh { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .kind { font-size: 9.5px; font-weight: 800; padding: 2px 7px; border-radius: 5px; text-transform: uppercase; letter-spacing: .04em; }
  .k-pos { background: #e6f3ec; color: var(--green); }
  .k-neg { background: #ede9f7; color: var(--violet); }
  .k-adv { background: #fbf3e3; color: var(--amber); }
  .scoremini { font-size: 11px; color: var(--muted); }
  .verdict { margin-left: auto; font-size: 11px; font-weight: 800; padding: 2px 9px; border-radius: 999px; }
  .v-pass { background: #e6f3ec; color: var(--green); }
  .v-fail { background: #f8e9e6; color: var(--red); }
  .v-part { background: #fbf3e3; color: var(--amber); }
  .qq { font-weight: 600; font-size: 13px; }
  .exp, .got, .why { font-size: 12px; margin-top: 7px; padding: 8px 10px; border-radius: 8px; background: var(--sand); }
  .lbl { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); display: block; margin-bottom: 2px; }
  .got.bad { background: #f8e9e6; }
  .why { background: #fbfaf7; border: 1px dashed var(--line); color: #55534d; }
</style>
