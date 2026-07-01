<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { range, tick } from '$lib/dashstore';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, barOpt, funnelOpt, hbarOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  let e = $state<any>(null);
  let roi = $state<any>(null);
  let saving = $state(false);

  function load(days: number) { api.analyticsManagement(days).then((r) => { e = r; roi = { ...r.config }; }).catch(() => {}); }
  async function saveRoi() { saving = true; try { const c = await api.analyticsConfigSave(roi); roi = { ...c }; load($range); } finally { saving = false; } }
  function delta(d: number | null) {
    if (d === null || d === undefined) return '';
    if (d > 0) return `▲ ${d}%`;
    if (d < 0) return `▼ ${Math.abs(d)}%`;
    return '· 0%';
  }
  function dcolor(d: number | null) { return d == null ? 'var(--muted)' : d > 0 ? '#3f8f5f' : d < 0 ? '#c0492f' : 'var(--muted)'; }
  function fmax(f: any) { return Math.max(1, f.registered || 1); }
  $effect(() => { $tick; if (isAdmin) load($range); });
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else if e}
  <div class="hero violet">
    <div class="hero-main">
      <div class="hero-num">{e.value?.hours_saved ?? '—'}<small>h</small></div>
      <div class="hero-lbl">staff hours saved this window</div>
      {#if e.deltas?.answered != null}
        <div class="hero-delta {e.deltas.answered >= 0 ? 'up' : 'dn'}">{delta(e.deltas.answered)} answered vs prior</div>
      {/if}
    </div>
    {#if e.wau_weeks?.length}
      {@const wmax = Math.max(1, ...e.wau_weeks.map((d: any) => d.n ?? 0))}
      <div class="hero-spark">
        <div class="csspark">
          {#each e.wau_weeks as d}<i style="height:{Math.max(8, ((d.n ?? 0) / wmax) * 100)}%"></i>{/each}
        </div>
      </div>
    {/if}
    <div class="hero-side">
      <div class="hs"><b>{e.engagement?.qs_per_active ?? '—'}</b>Qs / active user</div>
      <div class="hs"><b>{e.quality?.helpful_pct ?? '—'}%</b>helpful</div>
      <div class="hs"><b>${e.value?.llm_cost ?? '—'}</b>LLM cost</div>
    </div>
  </div>

  <div class="exgrid">
    <div class="excard">
      <div class="exh">Adoption <span style="float:right; color:{dcolor(e.deltas?.active)}; font-weight:600">{delta(e.deltas?.active)}</span></div>
      <div class="exbig">{e.adoption.activated}<span class="exden">/ {e.adoption.registered}</span></div>
      <div class="exsub">{e.adoption.activation_pct}% activated</div>
      <div class="exrow"><span>DAU {e.adoption.dau}</span><span>WAU {e.adoption.wau}</span><span>MAU {e.adoption.mau}</span></div>
      <div class="exmini">Stickiness {e.adoption.stickiness_pct}%</div>
    </div>
    <div class="excard">
      <div class="exh">Engagement <span style="float:right; color:{dcolor(e.deltas?.questions)}; font-weight:600">{delta(e.deltas?.questions)}</span></div>
      <div class="exbig">{e.engagement.qs_per_active}<span class="exden">Qs / user</span></div>
      <div class="exsub">{e.engagement.questions} questions · {e.engagement.convos} chats</div>
      <div class="exrow"><span>Multi-turn {e.engagement.multi_pct}%</span><span>Retention {e.engagement.retention_pct}%</span></div>
    </div>
    <div class="excard">
      <div class="exh">Quality <span style="float:right; color:{dcolor(e.deltas?.helpful)}; font-weight:600">{delta(e.deltas?.helpful)}</span></div>
      <div class="exbig">{e.quality.helpful_pct}%<span class="exden">helpful</span></div>
      <div class="exsub">{e.quality.sourced_pct}% sourced · {e.quality.blind_pct}% blind</div>
      <div class="exrow"><span>{e.quality.votes} votes</span><span>{e.quality.downvotes} down</span></div>
    </div>
    <div class="excard val">
      <div class="exh">Value <span style="float:right; color:{dcolor(e.deltas?.answered)}; font-weight:600">{delta(e.deltas?.answered)}</span></div>
      <div class="exbig">{e.value.answered}<span class="exden">answered</span></div>
      <div class="exsub">≈ {e.value.hours_saved} hrs saved</div>
      <div class="exrow"><span>${e.value.llm_cost} LLM</span><span>${e.value.cost_per_question}/Q</span></div>
    </div>
  </div>

  <div class="card" style="background:#1f1d1a; border-color:#2c2925">
    <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px">
      <div style="color:#ece8df; font-size:14px">
        <b style="color:#ffba7d; font-size:18px">{e.value.answered}</b> answers ≈
        <b style="color:#8fd0a6; font-size:18px">{e.value.hours_saved} hrs</b> saved for
        <b style="color:#ffba7d; font-size:18px">${e.value.llm_cost}</b>
        <span style="color:#b7b1a4"> · ~${e.value.monthly_run_rate}/mo run-rate</span>
      </div>
      <button class="roisave" onclick={() => window.print()}>⤓ Export / Print</button>
    </div>
  </div>

  <div class="fw2">
    <div class="card">
      <div class="ctitle">Adoption funnel</div>
      {#if e.funnel}
        <EChart option={funnelOpt([
          { name: 'Registered', value: e.funnel.registered ?? 0 },
          { name: 'Activated', value: e.funnel.activated ?? 0 },
          { name: 'Weekly active', value: e.funnel.weekly_active ?? 0 },
          { name: 'Power users', value: e.funnel.power_users ?? 0 }
        ])} height={200} />
      {/if}
    </div>
    <div class="card">
      <div class="ctitle">Weekly active users <span class="cnote">last 8 weeks</span></div>
      {#if e.wau_weeks?.length}
        <EChart option={areaOpt(e.wau_weeks, { yKey: 'n', color: C.teal })} height={170} />
      {/if}
    </div>
  </div>

  <div class="fw2">
    <div class="card">
      <div class="ctitle">Question volume <span class="cnote">last {$range} days</span></div>
      {#if e.trend?.length}
        <EChart option={barOpt(e.trend, { yKey: 'n', color: C.blue })} height={150} />
      {/if}
    </div>
    <div class="card">
      <div class="ctitle">Adoption by role</div>
      {#if e.by_role?.length}
        <EChart option={hbarOpt(
          e.by_role.map((r: any) => ({
            label: r.role || '(unknown)',
            value: r.registered ? Math.round((r.activated / r.registered) * 100) : 0
          })),
          { color: C.violet, max: 100 }
        )} height={Math.max(120, e.by_role.length * 36)} />
        <div class="cnote2">activated / registered per role (%)</div>
      {/if}
    </div>
  </div>

  <div class="card">
    <div class="ctitle">Value assumptions <span class="cnote">tune the ROI model</span></div>
    {#if roi}
      <div class="roi">
        <label>Minutes saved / answer <input type="number" min="0" step="0.5" bind:value={roi.minutes_saved_per_answer} /></label>
        <label>LLM $ / 1M tokens <input type="number" min="0" step="0.05" bind:value={roi.llm_price_per_mtok} /></label>
        <label>Tokens / answer <input type="number" min="0" step="100" bind:value={roi.tokens_per_answer} /></label>
        <button class="roisave" onclick={saveRoi} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
    {/if}
  </div>
  <div class="privnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Aggregate metrics only — no chat content. Hours saved is an estimate from your minutes-per-answer assumption.</div>
{:else}
  <div class="muted pad">Loading scorecard…</div>
{/if}
