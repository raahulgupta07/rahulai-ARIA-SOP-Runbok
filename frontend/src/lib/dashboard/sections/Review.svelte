<script lang="ts">
  import { api } from '$lib/api';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth';
  import { range, tick, navInsight } from '$lib/dashstore';
  import { ago, band } from '$lib/dashutil';
  import { parseDocName } from '$lib/docname';
  import EChart from '$lib/EChart.svelte';
  import { funnelOpt, hbarOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived(me?.role === 'admin');

  let o = $state<any>(null);          // dashboard admin (review counts + live)
  let audit = $state<any>(null);      // coverage_report (score/pillars/blind/stale)
  let pending = $state<any[]>([]);     // pending memory facts
  let corrections = $state<any[]>([]); // downvote corrections
  let digest = $state<any>(null);
  let trail = $state<any>(null);       // admin governance audit log
  let lrn = $state<any>(null);          // self-learning funnel
  let lowAcc = $state<any>(null);        // answers the citation judge scored low
  let busy = $state<number | null>(null);
  let pfFilter = $state<'all' | 'auto' | 'hand'>('all'); // pending-facts client filter
  let pfList = $derived(
    pfFilter === 'all'
      ? pending
      : pending.filter((f: any) => (pfFilter === 'auto' ? !!f.auto : !f.auto))
  );

  function loadCounts() { api.dashboardAdmin($range).then((r) => (o = r)).catch(() => {}); }
  function loadAudit() { api.auditCoverage($range).then((r) => (audit = r)).catch(() => {}); }
  function loadPending() { api.memory('pending').then((r) => (pending = r.memory || [])).catch(() => {}); }
  function loadCorr() { api.feedbackCorrections(20).then((r) => (corrections = r.corrections || [])).catch(() => {}); }
  function loadDigest() { api.digestLatest().then((r) => (digest = r)).catch(() => {}); }
  function loadTrail() { api.adminAudit({ days: $range, page_size: 40 }).then((r) => (trail = r)).catch(() => {}); }
  function loadLearn() { api.analyticsLearning($range).then((r) => (lrn = r)).catch(() => {}); }
  function loadLow() { api.lowAccuracy(70, $range).then((r) => (lowAcc = r)).catch(() => {}); }

  async function approve(id: number) { busy = id; try { await api.approveFact(id); loadPending(); loadCorr(); loadAudit(); loadTrail(); } finally { busy = null; } }
  async function reject(id: number) { busy = id; try { await api.rejectFact(id); loadPending(); loadCorr(); loadTrail(); } finally { busy = null; } }

  const ACT: Record<string, { lab: string; ico: string; c: string }> = {
    'fact.approve': { lab: 'approved fact', ico: '✓', c: '#3f8f5f' },
    'fact.reject': { lab: 'rejected fact', ico: '✕', c: '#c0492f' },
    'fact.edit': { lab: 'edited fact', ico: '✎', c: '#c98a2e' },
    'fact.delete': { lab: 'deleted fact', ico: '🗑', c: '#c0492f' },
    'doc.delete': { lab: 'deleted doc', ico: '🗑', c: '#c0492f' },
    'doc.retry': { lab: 'retried doc', ico: '↻', c: '#3f7fb0' },
    'config.save': { lab: 'changed config', ico: '⚙', c: '#7b6bd6' },
  };
  function actMeta(a: string) { return ACT[a] || { lab: a, ico: '•', c: 'var(--muted)' }; }
  function metaStr(m: any) { if (!m) return ''; if (m.name) return m.name; if (m.value) return '“' + m.value + '”'; const ks = Object.keys(m); return ks.length ? ks.map((k) => `${k}:${m[k]}`).join(' · ') : ''; }

  $effect(() => { $tick; if (isAdmin) { void $range; loadCounts(); loadAudit(); loadPending(); loadCorr(); loadDigest(); loadTrail(); loadLearn(); loadLow(); } });
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else}
  {#if lrn}
    {@const pend = lrn.funnel?.pending ?? pending.length ?? 0}
    {@const appr = lrn.funnel?.approved ?? 0}
    {@const cited = lrn.funnel?.cited ?? 0}
    {@const extr = lrn.funnel?.extracted ?? 0}
    <div class="hero amber">
      <div class="hero-main">
        <div class="hero-num">{pend}</div>
        <div class="hero-lbl">pending facts awaiting review</div>
        <div class="hero-delta {pend > 0 ? 'dn' : 'up'}">{pend > 0 ? "won't affect answers until approved" : 'all caught up'}</div>
      </div>
      {#if extr > 0}
        <div class="hero-spark">
          <div class="csspark">
            {#each [extr, lrn.funnel?.pending ?? 0, appr, cited] as v}<i style="height:{Math.max(8, (v / Math.max(1, extr)) * 100)}%"></i>{/each}
          </div>
        </div>
      {/if}
      <div class="hero-side">
        <div class="hs"><b>{appr}</b>approved</div>
        <div class="hs"><b>{lrn.reject_rate ?? '—'}%</b>reject rate</div>
        <div class="hs"><b>{cited}</b>cited</div>
      </div>
    </div>
  {/if}

  {#if audit}
    {@const b = band(audit.score)}
    <div class="card">
      <div style="display:flex; align-items:center; gap:18px; flex-wrap:wrap">
        <div class="kpi ring" style="border:0; padding:0">
          <svg viewBox="0 0 36 36" class="ringsvg">
            <path class="rbg" d="M18 2.5a15.5 15.5 0 1 1 0 31 15.5 15.5 0 0 1 0-31"/>
            <path class="rfg" style="stroke:{b.c}" stroke-dasharray="{audit.score}, 100" d="M18 2.5a15.5 15.5 0 1 1 0 31 15.5 15.5 0 0 1 0-31"/>
          </svg>
          <div class="rcenter"><b style="color:{b.c}">{audit.score}</b><span>Coverage · {b.t}</span></div>
        </div>
        <div style="flex:1; min-width:240px">
          <EChart option={hbarOpt(Object.entries(audit.pillars || {}).map(([k, v]) => ({ label: k, value: v as number })), { color: C.teal, max: 25 })} height={Object.keys(audit.pillars || {}).length * 34} />
        </div>
      </div>
    </div>
  {/if}

  <!-- ░ low-accuracy answers — the verify loop's fix-list ░ -->
  {#if lowAcc}
    <div class="card">
      <div class="ctitle">🎯 Low-accuracy answers
        <span class="cnote">{lowAcc.count} answer{lowAcc.count === 1 ? '' : 's'} scored below {lowAcc.threshold}% by the citation judge · fix the doc or teach a correction</span>
      </div>
      {#if lowAcc.answers.length}
        <div class="qlist">
          {#each lowAcc.answers as a}
            <div class="frow2" style="gap:6px">
              <div style="display:flex; align-items:center; gap:10px">
                <span class="bc" style="background:#fbeae6; color:#c0492f">{a.score}%</span>
                <span class="ft" style="flex:1">{a.question || '(question unavailable)'}</span>
                <span class="cnote">{ago(a.verify_at)}</span>
              </div>
              {#if a.failing.length}
                <div class="cnote">Unsupported citations: {#each a.failing as f, i}{parseDocName(f.doc_name || '').title} p.{f.page_no}{#if f.reason} — {f.reason}{/if}{#if i < a.failing.length - 1} · {/if}{/each}</div>
              {/if}
            </div>
          {/each}
        </div>
      {:else}<div class="muted sm">No low-accuracy answers — every verified answer is well-grounded. ✓</div>{/if}
    </div>
  {/if}

  <!-- ░ pending facts queue — approve/reject inline (B/W) ░ -->
  <div class="card pfcard">
    <div class="pfhead">
      <div class="ctitle" style="margin:0">Pending facts <span class="cnote">{pending.length} awaiting review · won't affect answers until approved</span></div>
      <div class="pfseg" role="group" aria-label="Filter pending facts">
        <button class="pfsg" class:on={pfFilter === 'all'} onclick={() => (pfFilter = 'all')}>All</button>
        <button class="pfsg" class:on={pfFilter === 'auto'} onclick={() => (pfFilter = 'auto')}>Auto</button>
        <button class="pfsg" class:on={pfFilter === 'hand'} onclick={() => (pfFilter = 'hand')}>Hand</button>
      </div>
    </div>
    {#if pfList.length}
      <div class="pfrows">
        {#each pfList as f (f.id)}
          <div class="pfrow">
            <div class="pfmain">
              <div class="pftext">{f.value || f.text}</div>
              <div class="pfmeta">
                {#if f.origin_question}<span class="pfsrc">from: {f.origin_question}</span>{/if}
                {#if f.source}<span class="pfsrc">{f.source}{f.created_by ? ' · ' + f.created_by : ''}</span>{/if}
              </div>
            </div>
            <span class="pfchip" class:auto={f.auto}>{f.auto ? 'AUTO ' + Math.round((f.confidence ?? 0) * 100) + '%' : 'Taught'}</span>
            <div class="pfact">
              <button class="pfbtn" disabled={busy === f.id} onclick={() => approve(f.id)}>Approve</button>
              <button class="pfbtn ghost" disabled={busy === f.id} onclick={() => reject(f.id)}>Reject</button>
            </div>
          </div>
        {/each}
      </div>
    {:else}
      <div class="muted sm" style="padding:14px 2px">{pending.length ? 'No facts match this filter.' : 'No pending facts — all caught up.'}</div>
    {/if}
  </div>

  <!-- ░ self-learning funnel ░ -->
  {#if lrn}
    <div class="card">
      <div class="ctitle">🧠 Self-learning funnel <span class="cnote">chat-learned facts: extracted → reviewed → approved → cited · reject {lrn.reject_rate}% · {lrn.funnel.taught} hand-taught</span></div>
      <EChart option={funnelOpt([
          { name: 'Extracted', value: lrn.funnel.extracted },
          { name: 'Pending',   value: lrn.funnel.pending   },
          { name: 'Approved',  value: lrn.funnel.approved  },
          { name: 'Cited',     value: lrn.funnel.cited     }
        ])} height={200} />
      {#if lrn.top_facts.length}
        <div class="cnote" style="margin:10px 0 4px">Most-cited learned facts</div>
        <div class="qlist">
          {#each lrn.top_facts.slice(0, 6) as f}
            <div class="frow2" style="flex-direction:row; align-items:center; gap:8px">
              <span class="ft" style="flex:1">{f.label}</span>
              <span class="cnote">{f.source === 'chat' ? '🤖 learned' : '✍ taught'}</span>
              <span class="bc">{f.cited_count}× cited</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  <div class="fw2">
    <!-- pending facts: approve/reject inline -->
    <div class="card">
      <div class="ctitle">⚠ Pending facts <span class="cnote">{pending.length} awaiting review · won't affect answers until approved</span></div>
      {#if pending.length}
        <div class="qlist">
          {#each pending as f}
            <div class="frow2" style="gap:8px">
              <span class="ft">{f.value || f.text}</span>
              {#if f.origin_question}<span class="cnote">from: “{f.origin_question}”</span>{/if}
              <div style="display:flex; gap:8px; align-items:center">
                <span class="cnote">{f.source === 'chat' ? '🤖 learned in chat' : '✍ taught'}{f.created_by ? ' · ' + f.created_by : ''}</span>
                <span style="margin-left:auto; display:flex; gap:6px">
                  <button class="roisave" style="padding:4px 12px; background:#3f8f5f" disabled={busy === f.id} onclick={() => approve(f.id)}>Approve</button>
                  <button class="roisave" style="padding:4px 12px; background:#fff; color:#c0492f; border:1px solid #e2c4bb" disabled={busy === f.id} onclick={() => reject(f.id)}>Reject</button>
                </span>
              </div>
            </div>
          {/each}
        </div>
      {:else}<div class="muted sm">No pending facts — all caught up. ✓</div>{/if}
    </div>

    <!-- downvote corrections -->
    <div class="card">
      <div class="ctitle">👎 Downvote corrections <span class="cnote">user-proposed fixes</span></div>
      {#if corrections.length}
        <div class="qlist">
          {#each corrections as c}
            <div class="frow2" style="gap:6px">
              {#if c.question}<span class="cnote">Q: {c.question}</span>{/if}
              <span class="ft">✎ {c.correction}</span>
              <div style="display:flex; align-items:center; gap:8px">
                <span class="cnote">{ago(c.created_at)}{c.fact_status ? ' · fact ' + c.fact_status : ''}</span>
                {#if c.learned_memory_id && c.fact_status === 'pending'}
                  <button class="roisave" style="margin-left:auto; padding:4px 12px; background:#3f8f5f" disabled={busy === c.learned_memory_id} onclick={() => approve(c.learned_memory_id)}>Approve fix</button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {:else}<div class="muted sm">No correction requests.</div>{/if}
    </div>
  </div>

  <div class="fw2">
    <!-- blind-spot questions -->
    <div class="card">
      <div class="ctitle">🕳 Blind-spot questions <span class="cnote">answered with no source — document these</span></div>
      {#if audit?.blind_spots?.length}
        <div class="blist">
          {#each audit.blind_spots as bspot}
            <div class="brow"><span class="bq">{bspot.question || bspot.q || bspot.text}</span>{#if bspot.count}<span class="bc">{bspot.count}×</span>{/if}</div>
          {/each}
        </div>
      {:else}<div class="muted sm">No blind spots — every answer was sourced. 🎉</div>{/if}
    </div>
    <!-- needs-review counts + live -->
    <div class="card">
      <div class="ctitle">Queue</div>
      {#if o}
        <div class="rev">
          <button class="ritem" onclick={() => navInsight('/brain')}><span class="rn">{o.review.pending_facts}</span><span class="rt">Pending facts</span><span class="rgo">→ Brain</span></button>
          <button class="ritem" onclick={() => navInsight('/brain')}><span class="rn">{o.review.corrections}</span><span class="rt">Downvote corrections</span><span class="rgo">→ Brain</span></button>
          <button class="ritem" onclick={() => navInsight('/dashboard/users')}><span class="rn">{o.review.pending_users}</span><span class="rt">Users awaiting approval</span><span class="rgo">→ Users</span></button>
        </div>
      {/if}
      {#if digest?.title}
        <div class="ctitle" style="margin-top:16px">Latest digest</div>
        <div class="brow"><span class="bq">{digest.title}</span><span class="bc">{digest.created_at ? ago(digest.created_at) : ''}</span></div>
      {/if}
    </div>
  </div>

  <!-- ░ governance: who did what (admin audit trail) ░ -->
  <div class="card">
    <div class="ctitle">🛡 Admin actions <span class="cnote">governance trail — every privileged change, who & when</span></div>
    {#if trail?.by_action?.length}
      <div style="display:flex; flex-wrap:wrap; gap:6px; margin:6px 0 12px">
        {#each trail.by_action as a}
          {@const m = actMeta(a.action)}
          <span class="achip" style="--ac:{m.c}">{m.ico} {m.lab}<b>{a.n}</b></span>
        {/each}
      </div>
    {/if}
    {#if trail?.rows?.length}
      <table class="atab">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th></tr></thead>
        <tbody>
          {#each trail.rows as r}
            {@const m = actMeta(r.action)}
            <tr>
              <td class="muted">{r.at}</td>
              <td>{r.actor_email || '—'}{#if r.actor_role}<span class="cnote"> · {r.actor_role}</span>{/if}</td>
              <td><span style="color:{m.c}; font-weight:600">{m.ico} {m.lab}</span></td>
              <td class="muted">{r.target_type ? r.target_type + ' #' + (r.target_id ?? '—') : ''}{#if metaStr(r.meta)} · {metaStr(r.meta)}{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if trail.total > trail.rows.length}<div class="cnote" style="margin-top:8px">showing {trail.rows.length} of {trail.total} in window</div>{/if}
    {:else}
      <div class="muted sm">No admin actions logged in this window.</div>
    {/if}
  </div>
{/if}

<style>
  /* pending-facts queue — black/white, hairline rows */
  .pfcard{background:#fff;}
  .pfhead{display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:6px;}
  .pfseg{display:inline-flex; border:1px solid var(--border); border-radius:8px; overflow:hidden;}
  .pfsg{appearance:none; background:#fff; border:0; border-left:1px solid var(--border); padding:5px 12px; font-size:12px; color:var(--muted); cursor:pointer;}
  .pfsg:first-child{border-left:0;}
  .pfsg:hover{background:var(--hover,#efefec);}
  .pfsg.on{background:var(--ink); color:#fff;}
  .pfrows{display:flex; flex-direction:column;}
  .pfrow{display:flex; align-items:center; gap:12px; padding:11px 2px; border-top:1px solid var(--border);}
  .pfrow:first-child{border-top:0;}
  .pfmain{flex:1; min-width:0;}
  .pftext{font-size:13.5px; color:var(--ink); line-height:1.4;}
  .pfmeta{display:flex; flex-wrap:wrap; gap:4px 10px; margin-top:3px;}
  .pfsrc{font-size:11px; color:var(--muted);}
  .pfchip{flex:0 0 auto; font-size:10.5px; font-weight:600; letter-spacing:.02em; padding:3px 8px; border-radius:6px; border:1px solid var(--ink); background:var(--ink); color:#fff; white-space:nowrap;}
  .pfchip.auto{background:#fff; color:var(--ink);}
  .pfact{flex:0 0 auto; display:flex; gap:6px;}
  .pfbtn{appearance:none; font-size:12px; padding:5px 14px; border-radius:7px; border:1px solid var(--ink); background:var(--ink); color:#fff; cursor:pointer;}
  .pfbtn:hover:not(:disabled){opacity:.85;}
  .pfbtn.ghost{background:#fff; color:var(--ink);}
  .pfbtn.ghost:hover:not(:disabled){background:var(--hover,#efefec);}
  .pfbtn:disabled{opacity:.45; cursor:default;}
  .achip{display:inline-flex; align-items:center; gap:5px; font-size:11.5px; padding:3px 9px; border-radius:99px; background:color-mix(in srgb, var(--ac) 9%, transparent); color:var(--ac); border:1px solid color-mix(in srgb, var(--ac) 28%, transparent);}
  .achip b{font-variant-numeric:tabular-nums;}
  .atab{width:100%; border-collapse:collapse; font-size:12.5px;}
  .atab th{text-align:left; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; padding:5px 8px; border-bottom:1px solid var(--line);}
  .atab td{padding:6px 8px; border-bottom:1px solid var(--line); color:var(--ink); vertical-align:top;}
  .atab tr:last-child td{border-bottom:none;}
  .atab .muted{color:var(--muted);}
</style>
