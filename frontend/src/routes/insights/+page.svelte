<script lang="ts">
  // Insights — CEO productivity dashboard (admin only).
  // ONE endpoint: GET /api/insights/overview?days=30 | ?from=YYYY-MM-DD&to=YYYY-MM-DD
  // Fetched with the same Bearer-token pattern the rest of the app uses. Fail-soft:
  // a 404 (backend not wired yet) renders the whole layout with zeroes, never crashes.
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, barOpt, C } from '$lib/charts';

  // ---- admin gate (MUST be reactive — cachedUser() is null at first paint) ----
  let me = $state(auth.cachedUser());
  $effect(() => { auth.me().then((u) => (me = u)); });
  const isAdmin = $derived(me?.role === 'admin' || me?.role === 'superadmin');

  // ---- date range ----
  let days = $state(30);
  let fromD = $state('');
  let toD = $state('');
  let loading = $state(false);

  function preset(d: number) {
    days = d;
    fromD = '';
    toD = '';
    load();
  }
  function applyCustom() {
    if (fromD && toD) load();
  }

  // ---- default (all-zero) shape so the page renders before/without data ----
  const EMPTY = {
    totals: {
      questions: 0, answered: 0, hours_saved: 0, labour_value: 0, ai_cost: 0, roi: 0,
      resolved_rate: 0, deflection_rate: 0, tickets_avoided: 0, active_users: 0,
      registered_users: 0, avg_answer_s: 0, refused_rate: 0,
      lang: { en: 0, my: 0, mixed: 0 },
      prev: { questions: 0, hours_saved: 0, resolved_rate: 0, avg_answer_s: 0, refused_rate: 0 }
    },
    trend: [] as any[],
    hourly: [] as number[],
    domains: [] as any[],
    departments: [] as any[],
    funnel: { registered: 0, asked: 0, weekly: 0, power: 0, dormant: 0, d7: 0, d30: 0 },
    gaps: [] as any[],
    people: [] as any[],
    // platform usage (tokens · models · accounts · system) — all fail-soft zero-shapes
    platform: {
      tokens: { total: 0, tin: 0, tout: 0, per_answer: 0 },
      by_model: [] as any[],
      cache: { serves: 0, hit_rate: 0, avg_s: 0 },
      new_users: { count: 0, last: null as any },
      conversations: { count: 0, avg_turns: 0, one_turn_pct: 0, max_turns: 0 },
      kb: { docs: 0, pages: 0, qa_active: 0, last_doc: null as any }
    },
    // adoption (DAU/WAU/MAU/stickiness/retention/after-hours)
    adoption: {
      dau: 0, wau: 0, mau: 0, dau_trend: [] as number[],
      stickiness_pct: 0, after_hours: 0, d7: 0, d30: 0
    }
  };

  let data = $state<any>(EMPTY);

  async function load() {
    loading = true;
    try {
      const qs = fromD && toD ? `from=${fromD}&to=${toD}` : `days=${days}`;
      const r = await fetch(`${api.base}/insights/overview?${qs}`, {
        headers: { Authorization: `Bearer ${auth.token()}` }
      });
      if (r.ok) data = { ...EMPTY, ...(await r.json()) };
    } catch {
      /* fail-soft — keep whatever we have (zeroes on first load) */
    }
    loading = false;
  }

  onMount(load);

  // ---- formatters ----
  const nf = new Intl.NumberFormat('en-US');
  const n = (x: number) => nf.format(Math.round(x || 0));
  const money = (x: number) => '$' + nf.format(Math.round(x || 0));
  const pct = (x: number) => `${Math.round(x || 0)}%`;

  // compact token/count formatter → 4.2M / 381k / 512
  function compact(x: number) {
    const v = Math.round(x || 0);
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(v >= 10_000_000 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (v >= 1_000) return (v / 1_000).toFixed(v >= 10_000 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return String(v);
  }

  // short date "21 Jul" from an ISO/date string (fail-soft)
  function shortDate(s: string) {
    if (!s) return '';
    const d = new Date(s);
    if (isNaN(+d)) return String(s).slice(0, 10);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
  }

  // map a raw model string → { label, color } (cache green / quick blue / deep violet)
  function modelDot(model: string) {
    const m = (model || '').toLowerCase();
    if (m === 'cache' || m.includes('cache') || m.includes('qa-bank')) return { label: 'Cache · 0 tokens', color: C.green };
    if (m.includes('gemini') || m.includes('flash')) return { label: 'Quick', color: C.blue };
    return { label: 'Deep', color: C.violet };
  }

  // ---- people table: client-side search + flag filter ----
  let peopleSearch = $state('');
  let flagFilter = $state('all'); // all | power | active | new | dormant
  const FLAG_PILLS = ['all', 'power', 'active', 'new', 'dormant'];

  const peopleView = $derived(
    (data.people || []).filter((p: any) => {
      if (flagFilter !== 'all' && (p.flag || '') !== flagFilter) return false;
      const q = peopleSearch.trim().toLowerCase();
      if (!q) return true;
      return (
        (p.name || '').toLowerCase().includes(q) ||
        (p.email || '').toLowerCase().includes(q) ||
        (p.dept || '').toLowerCase().includes(q)
      );
    })
  );

  // status pill: power/active green · new/at-risk amber · dormant red (+ icon)
  function statusPill(f: string) {
    switch (f) {
      case 'power': return { cls: 'pg', txt: '🔥 power' };
      case 'active': return { cls: 'pg', txt: 'active' };
      case 'at-risk': return { cls: 'pa', txt: '⚠ at-risk' };
      case 'new': return { cls: 'pa', txt: 'new' };
      case 'dormant': return { cls: 'pr', txt: '💤 dormant' };
      default: return { cls: 'pa', txt: f || '—' };
    }
  }

  // Excel download — authed blob (mirrors api.ts fetch→blob→a.download w/ Bearer)
  let xlsxBusy = $state(false);
  async function downloadPeopleXlsx() {
    xlsxBusy = true;
    try {
      const qs = fromD && toD ? `from=${fromD}&to=${toD}` : `days=${days}`;
      const r = await fetch(`${api.base}/insights/people.xlsx?${qs}`, {
        headers: { Authorization: `Bearer ${auth.token()}` }
      });
      if (!r.ok) throw new Error('export failed');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `aria-insights-people-${fromD && toD ? `${fromD}_${toD}` : `${days}d`}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      /* fail-soft — no crash if the endpoint isn't wired yet */
    }
    xlsxBusy = false;
  }

  // tiny sparkline path from a numeric series → SVG polyline points (0..100 × 0..24)
  function sparkPoints(arr: number[], h = 24) {
    const a = (arr || []).filter((v) => typeof v === 'number');
    if (a.length < 2) return '';
    const max = Math.max(1, ...a);
    const step = 100 / (a.length - 1);
    return a.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 2) - 1).toFixed(1)}`).join(' ');
  }
  const dauSpark = $derived(sparkPoints(data.adoption?.dau_trend || []));

  // delta vs a prior value → { txt, up }  (up=true is "good/green")
  function delta(cur: number, prev: number, goodUp = true, unit = '%') {
    if (!prev) return null;
    const diff = cur - prev;
    if (Math.abs(diff) < 0.5 && unit === '%') return null;
    const rel = unit === 'pct' ? Math.round(diff) : Math.round((diff / prev) * 100);
    const rising = diff > 0;
    return {
      txt: `${rising ? '▲' : '▼'} ${Math.abs(rel)}${unit === 'pct' ? 'pt' : '%'}`,
      up: rising === goodUp
    };
  }

  // resolved-rate pill class: green ≥85 / amber ≥70 / red
  function pill(v: number) {
    if (v >= 85) return 'pg';
    if (v >= 70) return 'pa';
    return 'pr';
  }
  // delta_pct on a table row → "▲ N%" span classes
  function trendCls(v: number) {
    return v >= 0 ? 'up' : 'dn';
  }
  function trendTxt(v: number) {
    return `${v >= 0 ? '▲' : '▼'} ${Math.abs(Math.round(v))}%`;
  }

  // people flag pill
  function flagCls(f: string) {
    if (f === 'power') return 'pg';
    if (f === 'dormant') return 'pr';
    return 'pa';
  }

  // ---- derived chart options ----
  const workingDays = $derived(Math.round((data.totals.hours_saved || 0) / 8));
  const hoursPerDay = $derived(() => {
    const span = fromD && toD
      ? Math.max(1, Math.round((+new Date(toD) - +new Date(fromD)) / 86400000) + 1)
      : days;
    return (data.totals.hours_saved || 0) / span;
  });

  const trendOption = $derived(
    areaOpt(
      (data.trend || []).map((t: any) => ({ label: (t.day || '').slice(5), n: t.hours || 0 })),
      { yKey: 'n', color: C.teal, name: 'Hours saved' }
    )
  );

  const hourlyOption = $derived(
    barOpt(
      (data.hourly || []).map((v: number, i: number) => ({
        label: i % 3 === 0 ? `${i}:00` : '',
        n: v || 0
      })),
      { color: C.teal }
    )
  );

  // domain/department bars scale to the busiest row
  const domainMax = $derived(Math.max(1, ...(data.domains || []).map((d: any) => d.hours || 0)));
  const deptMax = $derived(Math.max(1, ...(data.departments || []).map((d: any) => d.hours || 0)));

  // KPI deltas
  const dQuestions = $derived(delta(data.totals.questions, data.totals.prev.questions));
  const dHours = $derived(delta(data.totals.hours_saved, data.totals.prev.hours_saved));
  const dResolved = $derived(delta(data.totals.resolved_rate, data.totals.prev.resolved_rate, true, 'pct'));
  const dAnswer = $derived(delta(data.totals.avg_answer_s, data.totals.prev.avg_answer_s, false, '%'));
  const dRefused = $derived(delta(data.totals.refused_rate, data.totals.prev.refused_rate, false, 'pct'));
</script>

<div class="ins">
  {#if !isAdmin}
    <div class="wrap"><div class="card admincard">
      <h2>Admin only</h2>
      <p>Insights are available to administrators. Ask a super-admin for access.</p>
    </div></div>
  {:else}
    <!-- sticky header -->
    <div class="top">
      <h1>Insights</h1>
      <div class="dater">
        <div class="seg">
          {#each [7, 30, 90] as d}
            <button class:on={!fromD && days === d} onclick={() => preset(d)}>{d}d</button>
          {/each}
        </div>
        <input type="date" bind:value={fromD} max={toD || undefined} />
        <span class="arrow">→</span>
        <input type="date" bind:value={toD} min={fromD || undefined} />
        <button class="apply" onclick={applyCustom} disabled={!fromD || !toD}>Apply</button>
        {#if loading}<span class="ld">Loading…</span>{/if}
      </div>
    </div>

    <div class="wrap">
      <!-- CEO HERO -->
      <div class="hero">
        <div class="big">
          <div class="l">TOTAL PRODUCTIVITY</div>
          <div class="nn">{n(data.totals.hours_saved)} hours</div>
          <div class="s">
            ≈ <b>{n(workingDays)} working days</b> given back to staff ·
            <b>{money(data.totals.labour_value)}</b> labour value vs
            <b>{money(data.totals.ai_cost)}</b> AI cost =
            <b class="roi">{Math.round(data.totals.roi || 0)}× ROI</b>
          </div>
        </div>
        <div class="m">
          <div class="l">QUESTIONS ANSWERED</div>
          <div class="nn2">{n(data.totals.answered)}</div>
          <div class="d">
            {#if dQuestions}<span class={dQuestions.up ? 'gu' : 'gd'}>{dQuestions.txt}</span> vs prior{:else}vs prior period{/if}
          </div>
        </div>
        <div class="m">
          <div class="l">TICKETS AVOIDED</div>
          <div class="nn2">~{n(data.totals.tickets_avoided)}</div>
          <div class="d">{pct(data.totals.deflection_rate)} deflection rate</div>
        </div>
        <div class="m">
          <div class="l">STAFF USING ARIA</div>
          <div class="nn2">{n(data.totals.active_users)} / {n(data.totals.registered_users)}</div>
          <div class="d">
            {data.totals.registered_users
              ? pct((data.totals.active_users / data.totals.registered_users) * 100)
              : '0%'} adoption
          </div>
        </div>
      </div>

      <!-- PLATFORM USAGE — tokens · models · accounts · system -->
      <div class="card">
        <h2>Platform usage <small>tokens · models · accounts · system</small></h2>
        <div class="pgrid">
          <!-- tokens -->
          <div class="pcell">
            <div class="l">TOKENS USED</div>
            <div class="nn">{compact(data.platform.tokens.total)}</div>
            <div class="d">
              {compact(data.platform.tokens.tin)} in · {compact(data.platform.tokens.tout)} out ·
              <b>{n(data.platform.tokens.per_answer)}</b>/answer
            </div>
          </div>
          <!-- by model -->
          <div class="pcell">
            <div class="l">BY MODEL</div>
            <div class="models">
              {#each data.platform.by_model as m}
                {@const dot = modelDot(m.model)}
                <div class="mrow" title={m.model}>
                  <i style="background:{dot.color}"></i>
                  <span class="mn">{dot.label}</span>
                  <b>{pct(m.share_pct)}</b>
                </div>
              {/each}
              {#if !data.platform.by_model.length}
                <div class="mrow"><span class="mn empty">No model activity yet.</span></div>
              {/if}
            </div>
          </div>
          <!-- cache hit -->
          <div class="pcell">
            <div class="l">CACHE HIT</div>
            <div class="nn" style="color:{C.green}">{pct(data.platform.cache.hit_rate)}</div>
            <div class="d">
              {n(data.platform.cache.serves)} instant answers · $0 · avg {(data.platform.cache.avg_s || 0).toFixed(1)}s
            </div>
          </div>
          <!-- new users -->
          <div class="pcell">
            <div class="l">NEW USERS</div>
            <div class="nn">+{n(data.platform.new_users.count)}</div>
            <div class="d">
              {#if data.platform.new_users.last}
                last: <b>{data.platform.new_users.last.email || data.platform.new_users.last.name}</b><br />
                {shortDate(data.platform.new_users.last.created_at)}
                {#if data.platform.new_users.last.method}· via {data.platform.new_users.last.method}{/if}
              {:else}no new accounts this period{/if}
            </div>
          </div>
          <!-- conversations -->
          <div class="pcell">
            <div class="l">CONVERSATIONS</div>
            <div class="nn">{n(data.platform.conversations.count)}</div>
            <div class="d">
              avg <b>{(data.platform.conversations.avg_turns || 0).toFixed(1)}</b> turns ·
              {pct(data.platform.conversations.one_turn_pct)} one-and-done<br />
              longest {n(data.platform.conversations.max_turns)} turns
            </div>
          </div>
          <!-- knowledge base -->
          <div class="pcell">
            <div class="l">KNOWLEDGE BASE</div>
            <div class="nn">{n(data.platform.kb.docs)} docs</div>
            <div class="d">
              {n(data.platform.kb.pages)} pages · {n(data.platform.kb.qa_active)} Q&amp;A active
              {#if data.platform.kb.last_doc}
                <br />last: <b>{data.platform.kb.last_doc.name}</b> · {shortDate(data.platform.kb.last_doc.created_at)}
              {/if}
            </div>
          </div>
        </div>
      </div>

      <!-- ADOPTION KPI ROW -->
      <div class="krow">
        <div class="kc">
          <div class="l">DAU</div>
          <div class="nn" style="color:{C.blue}">{n(data.adoption.dau)}</div>
          {#if dauSpark}
            <svg class="spark" width="100%" height="24" viewBox="0 0 100 24" preserveAspectRatio="none">
              <polyline points={dauSpark} fill="none" stroke={C.blue} stroke-width="2" />
            </svg>
          {:else}<div class="d">daily active users</div>{/if}
        </div>
        <div class="kc">
          <div class="l">WAU</div>
          <div class="nn" style="color:{C.teal}">{n(data.adoption.wau)}</div>
          <div class="d">weekly active users</div>
        </div>
        <div class="kc">
          <div class="l">MAU</div>
          <div class="nn" style="color:{C.violet}">{n(data.adoption.mau)}</div>
          <div class="d">
            {data.totals.registered_users
              ? pct((data.adoption.mau / data.totals.registered_users) * 100)
              : '0%'} of {n(data.totals.registered_users)} registered
          </div>
        </div>
        <div class="kc">
          <div class="l">STICKINESS</div>
          <div class="nn">{pct(data.adoption.stickiness_pct)}</div>
          <div class="d">DAU/MAU — daily-habit share</div>
        </div>
        <div class="kc">
          <div class="l">RETENTION</div>
          <div class="nn">D7 {pct(data.adoption.d7)}</div>
          <div class="d">D30 <b>{pct(data.adoption.d30)}</b> · new users who return</div>
        </div>
        <div class="kc">
          <div class="l">AFTER-HOURS</div>
          <div class="nn" style="color:{C.violet}">{n(data.adoption.after_hours)}</div>
          <div class="d">answers outside 9-6 &amp; weekends</div>
        </div>
      </div>

      <!-- KPI STRIP -->
      <div class="krow">
        <div class="kc">
          <div class="l">HOURS SAVED / DAY</div>
          <div class="nn" style="color:{C.teal}">{(hoursPerDay()).toFixed(1)}</div>
          <div class="d">{#if dHours}<span class={dHours.up ? 'up' : 'dn'}>{dHours.txt}</span>{:else}—{/if}</div>
        </div>
        <div class="kc">
          <div class="l">RESOLVED RATE</div>
          <div class="nn" style="color:{C.green}">{pct(data.totals.resolved_rate)}</div>
          <div class="d">{#if dResolved}<span class={dResolved.up ? 'up' : 'dn'}>{dResolved.txt}</span>{:else}—{/if}</div>
        </div>
        <div class="kc">
          <div class="l">AVG ANSWER TIME</div>
          <div class="nn">{(data.totals.avg_answer_s || 0).toFixed(1)}s</div>
          <div class="d">{#if dAnswer}<span class={dAnswer.up ? 'up' : 'dn'}>{dAnswer.txt}</span>{:else}—{/if}</div>
        </div>
        <div class="kc">
          <div class="l">WRONG / REFUSED</div>
          <div class="nn" style="color:{C.amber}">{pct(data.totals.refused_rate)}</div>
          <div class="d">{#if dRefused}<span class={dRefused.up ? 'up' : 'dn'}>{dRefused.txt}</span>{:else}—{/if}</div>
        </div>
        <div class="kc">
          <div class="l">AI COST</div>
          <div class="nn">{money(data.totals.ai_cost)}</div>
          <div class="d">
            {data.totals.answered
              ? '$' + (data.totals.ai_cost / data.totals.answered).toFixed(3) + ' / answer'
              : '—'}
          </div>
        </div>
        <div class="kc">
          <div class="l">LANGUAGE</div>
          <div class="nn" style="font-size:18px">EN {pct(data.totals.lang.en)}</div>
          <div class="bar" style="margin-top:8px"><i style="width:{data.totals.lang.en}%;background:{C.blue}"></i></div>
          <div class="d">မြန်မာ {pct(data.totals.lang.my)} · mixed {pct(data.totals.lang.mixed)}</div>
        </div>
      </div>

      <!-- TREND + DOMAIN -->
      <div class="grid2">
        <div class="card">
          <h2>Productivity trend <small>daily hours saved</small></h2>
          <EChart option={trendOption} height={200} />
        </div>

        <div class="card">
          <h2>Usage by domain <small>email domain = organisation</small></h2>
          <div class="tscroll">
            <table>
              <thead>
                <tr><th>DOMAIN</th><th class="num">USERS</th><th class="num">QUESTIONS</th><th style="width:28%">HOURS</th><th class="num">TREND</th></tr>
              </thead>
              <tbody>
                {#each data.domains as d}
                  <tr>
                    <td><b>{d.domain}</b></td>
                    <td class="num">{n(d.users)}</td>
                    <td class="num">{n(d.questions)}</td>
                    <td><div class="bar"><i style="width:{Math.round((d.hours / domainMax) * 100)}%;background:{C.teal}"></i></div></td>
                    <td class="num {trendCls(d.delta_pct)}">{trendTxt(d.delta_pct)}</td>
                  </tr>
                {/each}
                {#if !data.domains.length}
                  <tr><td colspan="5" class="empty">No domain activity yet.</td></tr>
                {/if}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- HOURLY -->
      <div class="card">
        <h2>When people ask <small>questions by hour of day</small></h2>
        <EChart option={hourlyOption} height={170} />
      </div>

      <!-- DEPARTMENTS + FUNNEL + GAPS -->
      <div class="grid3">
        <div class="card">
          <h2>Departments <small>from LDAP · domain · manual</small></h2>
          <div class="tscroll">
            <table>
              <thead>
                <tr><th>DEPT</th><th class="num">USERS</th><th class="num">HOURS</th><th class="num">RESOLVED</th><th class="num">Δ</th></tr>
              </thead>
              <tbody>
                {#each data.departments as d}
                  <tr>
                    <td><b>{d.dept}</b></td>
                    <td class="num">{n(d.users)}</td>
                    <td class="num">{n(d.hours)} h</td>
                    <td class="num"><span class="pill {pill(d.resolved_rate)}">{pct(d.resolved_rate)}</span></td>
                    <td class="num {trendCls(d.delta_pct)}">{trendTxt(d.delta_pct)}</td>
                  </tr>
                {/each}
                {#if !data.departments.length}
                  <tr><td colspan="5" class="empty">No department data yet.</td></tr>
                {/if}
              </tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <h2>Adoption funnel <small>this period</small></h2>
          {#each [ ['Registered', data.funnel.registered], ['Asked ≥1', data.funnel.asked], ['Weekly habit', data.funnel.weekly], ['Power (P90)', data.funnel.power] ] as row}
            {@const top = Math.max(1, data.funnel.registered)}
            <div class="frow">
              <span class="fl">{row[0]}</span>
              <span class="fn">{n(row[1] as number)}</span>
              <div class="bar"><i style="width:{Math.round(((row[1] as number) / top) * 100)}%;background:{C.violet}"></i></div>
            </div>
          {/each}
          <div class="note">
            {n(data.funnel.dormant)} registered-but-silent users. Retention D7: {pct(data.funnel.d7)} · D30: {pct(data.funnel.d30)}.
          </div>
        </div>

        <div class="card">
          <h2>Knowledge gaps <small>refused / abandoned topics</small></h2>
          <div class="chips">
            {#each data.gaps as g}
              <span class="topic">{g.topic} <b>×{n(g.count)}</b></span>
            {/each}
            {#if !data.gaps.length}
              <span class="empty">No gaps flagged — coverage looks healthy.</span>
            {/if}
          </div>
          <div class="note">Ranked by distinct users hit. Top gaps = write these next → assign to a doc owner.</div>
        </div>
      </div>

      <!-- PEOPLE — per-user productivity -->
      <div class="card">
        <h2>People — per-user productivity <small>privacy-safe: counts + topics, never chat text</small></h2>
        <div class="ptools">
          <input class="psearch" type="text" placeholder="🔎 Search name / email / dept…" bind:value={peopleSearch} />
          <div class="fpills">
            {#each FLAG_PILLS as f}
              <button class:on={flagFilter === f} onclick={() => (flagFilter = f)}>{f === 'all' ? 'All' : f}</button>
            {/each}
          </div>
          <button class="xbtn" onclick={downloadPeopleXlsx} disabled={xlsxBusy}>
            {xlsxBusy ? 'Preparing…' : '⬇ Download Excel — all users'}
          </button>
        </div>
        <div class="tscroll">
          <table class="ptable">
            <thead>
              <tr>
                <th>USER</th><th>DEPT</th><th>CREATED</th><th>LAST ACTIVE</th>
                <th class="num">ACTIVE DAYS</th><th class="num">QUESTIONS</th><th class="num">HOURS SAVED</th>
                <th class="num">$ VALUE</th><th class="num">TOKENS</th><th class="num">HELPFUL</th><th class="num">RESOLVED</th>
                <th>LOGIN</th><th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {#each peopleView as p}
                {@const st = statusPill(p.flag)}
                <tr>
                  <td><b>{p.name}</b><div class="sub">{p.email}</div></td>
                  <td>{p.dept || '—'}</td>
                  <td>{shortDate(p.created_at) || '—'}</td>
                  <td>{p.last_active ? shortDate(p.last_active) : '—'}</td>
                  <td class="num">{n(p.active_days)}</td>
                  <td class="num">{n(p.questions)}</td>
                  <td class="num hrs">{n(p.hours)}</td>
                  <td class="num">{money(p.value_usd)}</td>
                  <td class="num">{compact(p.tokens)}</td>
                  <td class="num">{p.helpful != null ? pct(p.helpful) : '—'}</td>
                  <td class="num">{p.resolved != null ? pct(p.resolved) : '—'}</td>
                  <td>{(p.methods || []).join(' · ') || '—'}</td>
                  <td><span class="pill {st.cls}">{st.txt}</span></td>
                </tr>
              {/each}
              {#if !peopleView.length}
                <tr><td colspan="13" class="empty">No people match this filter.</td></tr>
              {/if}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  /* Local tokens — app.css :root does NOT carry dashboard colors, define here. */
  .ins {
    --cream: #faf9f5; --sand: #f0eee6; --ink: #1f1e1d; --mut: #8a8578; --line: #e0dfda;
    --blue: #3f7fb0; --teal: #2f8f83; --violet: #7b6bd6; --amber: #c98a2e; --green: #3f8f5f; --red: #c0492f;
    height: 100%;
    overflow-y: auto;
    background: #f4f3f0;
    color: var(--ink);
    font-family: -apple-system, 'Segoe UI', 'Noto Sans Myanmar', sans-serif;
    padding-bottom: 48px;
  }

  /* sticky header */
  .top {
    position: sticky; top: 0; z-index: 5;
    background: var(--sand); border-bottom: 1px solid var(--line);
    padding: 12px 26px; display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  }
  .top h1 { font-size: 19px; font-weight: 700; }
  .dater { margin-left: auto; display: flex; gap: 8px; align-items: center; font-size: 12px; flex-wrap: wrap; }
  .seg { display: flex; gap: 2px; background: #fff; border: 1px solid var(--line); border-radius: 99px; padding: 3px; }
  .seg button { padding: 4px 12px; border-radius: 99px; color: var(--mut); background: none; border: 0; font-size: 12px; cursor: pointer; }
  .seg button.on { background: var(--ink); color: #fff; }
  .dater input[type='date'] { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 5px 8px; color: var(--ink); font-size: 12px; font-family: inherit; }
  .dater .arrow { color: var(--mut); }
  .apply { background: var(--ink); color: #fff; border: 0; border-radius: 8px; padding: 6px 13px; font-size: 12px; cursor: pointer; }
  .apply:disabled { opacity: .4; cursor: default; }
  .ld { color: var(--mut); }

  .wrap { max-width: 1280px; margin: 14px auto 0; padding: 0 18px; display: flex; flex-direction: column; gap: 13px; }

  .card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
  .card h2 { font-size: 13.5px; margin-bottom: 11px; font-weight: 700; }
  .card h2 small { color: var(--mut); font-weight: 400; font-size: 11.5px; margin-left: 7px; }

  .admincard { max-width: 460px; margin: 40px auto; text-align: center; }
  .admincard p { color: var(--mut); font-size: 13px; margin-top: 8px; }

  /* CEO hero */
  .hero {
    background: linear-gradient(135deg, #1f2e26 0%, #28423a 60%, #1f3a44 100%);
    color: #fff; border-radius: 16px; padding: 22px 26px;
    display: grid; grid-template-columns: 1.15fr 1fr 1fr 1fr; gap: 22px; align-items: center;
  }
  .hero .l { font-size: 11px; letter-spacing: .1em; color: #9fc0b2; font-weight: 700; }
  .hero .big .nn { font-size: 42px; font-weight: 800; line-height: 1.05; margin-top: 4px; }
  .hero .big .s { font-size: 12.5px; color: #bcd6ca; margin-top: 6px; }
  .hero .m .nn2 { font-size: 24px; font-weight: 700; margin-top: 2px; }
  .hero .m .d { font-size: 11px; color: #bcd6ca; margin-top: 3px; }
  .hero .roi { color: #7ddba4; }
  .hero .gu { color: #7ddba4; }
  .hero .gd { color: #f0a58c; }

  /* KPI strip */
  .krow { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 11px; }
  .kc { background: #fff; border: 1px solid var(--line); border-radius: 13px; padding: 13px 14px; }
  .kc .l { font-size: 10px; letter-spacing: .07em; color: var(--mut); font-weight: 700; }
  .kc .nn { font-size: 21px; font-weight: 700; margin-top: 4px; }
  .kc .d { font-size: 10.5px; margin-top: 4px; color: var(--mut); }
  .up { color: var(--green); }
  .dn { color: var(--red); }

  .grid2 { display: grid; grid-template-columns: 1.5fr 1fr; gap: 13px; }
  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 13px; }

  .tscroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th { text-align: left; font-size: 10px; letter-spacing: .06em; color: var(--mut); padding: 5px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  td { padding: 8px 8px; border-bottom: 1px solid #efece6; }
  tbody tr:last-child td { border-bottom: 0; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .sub { font-size: 10.5px; color: var(--mut); margin-top: 1px; }
  .empty { color: var(--mut); font-style: italic; padding: 14px 8px; }

  .bar { height: 7px; border-radius: 99px; background: #eee9e2; overflow: hidden; min-width: 70px; }
  .bar i { display: block; height: 100%; border-radius: 99px; }

  .pill { font-size: 10px; font-weight: 700; border-radius: 99px; padding: 2px 9px; text-transform: capitalize; }
  .pg { background: #e2efe7; color: var(--green); }
  .pa { background: #f7edd8; color: var(--amber); }
  .pr { background: #f3e2de; color: var(--red); }

  /* funnel rows */
  .frow { display: grid; grid-template-columns: 92px 34px 1fr; align-items: center; gap: 8px; margin-bottom: 9px; font-size: 12.5px; }
  .frow .fl { color: var(--ink); }
  .frow .fn { font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
  .note { font-size: 10.5px; color: var(--mut); margin-top: 10px; line-height: 1.5; }

  /* gap chips */
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .topic { display: inline-block; background: #f6f5f1; border: 1px solid var(--line); border-radius: 99px; padding: 3px 10px; font-size: 11.5px; }
  .topic b { color: var(--red); margin-left: 4px; }

  /* platform usage grid */
  .pgrid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; }
  .pcell { padding: 2px 2px; }
  .pcell .l { font-size: 10px; letter-spacing: .07em; color: var(--mut); font-weight: 700; }
  .pcell .nn { font-size: 21px; font-weight: 700; margin-top: 4px; }
  .pcell .d { font-size: 10.5px; margin-top: 4px; color: var(--mut); line-height: 1.5; }
  .pcell .d b { color: var(--ink); }
  .models { margin-top: 8px; display: flex; flex-direction: column; gap: 5px; }
  .mrow { display: flex; align-items: center; font-size: 11.5px; }
  .mrow i { width: 8px; height: 8px; border-radius: 3px; margin-right: 6px; flex: none; }
  .mrow .mn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mrow .mn.empty { color: var(--mut); font-style: italic; }
  .mrow b { margin-left: 6px; }
  .spark { margin-top: 7px; display: block; }

  /* people tools */
  .ptools { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
  .psearch {
    flex: 1; min-width: 180px; background: #fff; border: 1px solid var(--line);
    border-radius: 9px; padding: 7px 11px; font-size: 12.5px; color: var(--ink); font-family: inherit;
  }
  .psearch:focus { outline: none; border-color: var(--teal); }
  .fpills { display: flex; gap: 2px; background: #f6f5f1; border: 1px solid var(--line); border-radius: 99px; padding: 3px; }
  .fpills button { padding: 4px 12px; border-radius: 99px; border: 0; background: none; color: var(--mut); font-size: 11.5px; cursor: pointer; text-transform: capitalize; }
  .fpills button.on { background: var(--ink); color: #fff; }
  .xbtn { background: #1f2e26; color: #fff; border: 0; border-radius: 9px; padding: 8px 14px; font-size: 12px; font-weight: 600; cursor: pointer; }
  .xbtn:disabled { opacity: .5; cursor: default; }

  .ptable { min-width: 1120px; }
  .ptable .hrs { color: var(--teal); font-weight: 700; }

  @media (max-width: 900px) {
    .hero { grid-template-columns: 1fr 1fr; }
    .krow { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .grid2, .grid3 { grid-template-columns: 1fr; }
    .pgrid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }
  @media (max-width: 560px) {
    .hero { grid-template-columns: 1fr; }
    .krow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .pgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
</style>
