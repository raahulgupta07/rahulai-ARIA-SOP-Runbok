<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { range, tick } from '$lib/dashstore';
  import { ago, INTENT_LABEL, STATUS_DOT } from '$lib/dashutil';
  import Bars from '$lib/Bars.svelte';
  import EChart from '$lib/EChart.svelte';
  import { barOpt, C } from '$lib/charts';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  let data = $state<any>(null);
  let q = $state(''); let status = $state('all'); let role = $state(''); let authF = $state('');
  let sort = $state('questions'); let order = $state('desc'); let pg = $state(1);
  let profile = $state<any>(null); let profileId = $state<number | null>(null);

  function load() {
    api.analyticsUsers({ days: $range, sort, order, q, status, role, auth: authF, page: pg }).then((r) => (data = r)).catch(() => {});
  }
  async function exportCsv() {
    const csv = await api.analyticsUsersExport({ days: $range, status, role, auth: authF });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'aria-users.csv'; a.click();
    URL.revokeObjectURL(url);
  }
  function sparkMax(s: number[]) { return Math.max(1, ...(s || [])); }
  function flags(u: any) {
    const f: { kind: string; title: string }[] = [];
    if (u.questions >= 10) f.push({ kind: 'power', title: 'Power user' });
    if (u.helpful_pct !== null && u.helpful_pct < 60) f.push({ kind: 'risk', title: 'At risk — low helpful rate' });
    if (u.status === 'dormant') f.push({ kind: 'dormant', title: 'Dormant' });
    return f;
  }
  function setSort(col: string) {
    if (sort === col) order = order === 'desc' ? 'asc' : 'desc'; else { sort = col; order = 'desc'; }
    pg = 1; load();
  }
  function openProfile(id: number) { profileId = id; profile = null; api.analyticsUserProfile(id, 90).then((r) => (profile = r)).catch(() => {}); }
  function closeProfile() { profileId = null; profile = null; }
  let eng = $state<any>(null);
  $effect(() => { $tick; if (isAdmin) { void $range; load(); api.analyticsEngagement($range).then((r) => (eng = r)).catch(() => {}); } });
</script>

{#if !isAdmin}
  <div class="muted pad">Admin only.</div>
{:else}
  {#if data}
    {@const _active = data.segments?.active ?? 0}
    {@const _total = data.total ?? 0}
    {@const _pct = _total ? Math.round((_active / _total) * 100) : 0}
    {@const _power = (data.users ?? []).filter((u: any) => (u.questions ?? 0) >= 10).length}
    <div class="hero">
      <div class="hero-main">
        <div class="hero-num">{_active}<small>/ {_total}</small></div>
        <div class="hero-lbl">active users this period</div>
        <div class="hero-delta up">{_pct}% of all users active</div>
      </div>
      <div class="hero-side">
        <div class="hs"><b>{_power}</b>power users (10+ Qs)</div>
        <div class="hs"><b>{data.segments?.dormant ?? '—'}</b>dormant</div>
        <div class="hs"><b>{eng?.retention?.d7 ?? '—'}{eng?.retention ? '%' : ''}</b>D7 retention</div>
      </div>
    </div>
  {/if}

  <!-- ░ engagement: depth + retention + dormant ░ -->
  {#if eng}
    <div class="engrow">
      <div class="card encard">
        <div class="ctitle">Session depth</div>
        <div class="enbig">{eng.sessions.avg_turns}<span class="enden">avg turns</span></div>
        <div class="cnote">{eng.sessions.followup_pct}% multi-turn · longest {eng.sessions.max_turns}</div>
        {#if eng.sessions.depth}
          <EChart option={barOpt([
            {label:'1', n: eng.sessions.depth.one ?? 0},
            {label:'2-3', n: eng.sessions.depth.short ?? 0},
            {label:'4-6', n: eng.sessions.depth.medium ?? 0},
            {label:'7+', n: eng.sessions.depth.deep ?? 0}
          ], {color: C.blue})} height={130} />
        {/if}
      </div>
      <div class="card encard">
        <div class="ctitle">Retention <span class="cnote">returned after signup</span></div>
        <div class="retrow">
          {#each [['D1', eng.retention.d1, eng.retention.elig.d1], ['D7', eng.retention.d7, eng.retention.elig.d7], ['D30', eng.retention.d30, eng.retention.elig.d30]] as [lab, pct, n]}
            <div class="ret"><div class="retn" style="color:{pct >= 40 ? '#3f8f5f' : pct >= 20 ? '#c98a2e' : '#c0492f'}">{pct}%</div><div class="retl">{lab}</div><div class="cnote" style="font-size:10px">of {n}</div></div>
          {/each}
        </div>
      </div>
      <div class="card encard">
        <div class="ctitle">💤 Dormant <span class="cnote">active before, quiet 14d+</span></div>
        {#if eng.dormant.length}
          <div class="dormlist">
            {#each eng.dormant.slice(0, 6) as u}
              <button class="dormrow" onclick={() => openProfile(u.id)}><span class="de">{u.email}</span><span class="cnote">{u.quiet_days}d quiet</span></button>
            {/each}
          </div>
        {:else}<div class="muted sm">No dormant users. ✓</div>{/if}
      </div>
    </div>
  {/if}

  <div class="card">
    <div class="ctitle">User monitor <span class="cnote">🔒 by id — no chat shown</span>
      <button class="roisave" style="margin-left:auto; padding:5px 13px" onclick={exportCsv}>⤓ Export CSV</button>
    </div>
    {#if data?.segments}
      <div class="segbar">
        <button class="segpill" class:on={status==='active'} onclick={() => { status = status==='active'?'all':'active'; pg=1; load(); }}><span class="udot" style="background:{STATUS_DOT.active}"></span>{data.segments.active} active</button>
        <button class="segpill" class:on={status==='dormant'} onclick={() => { status = status==='dormant'?'all':'dormant'; pg=1; load(); }}><span class="udot" style="background:{STATUS_DOT.dormant}"></span>{data.segments.dormant} dormant</button>
        <button class="segpill" class:on={status==='inactive'} onclick={() => { status = status==='inactive'?'all':'inactive'; pg=1; load(); }}><span class="udot" style="background:{STATUS_DOT.inactive}"></span>{data.segments.inactive} inactive</button>
        <button class="segpill" class:on={status==='never'} onclick={() => { status = status==='never'?'all':'never'; pg=1; load(); }}><span class="udot" style="background:{STATUS_DOT.never}"></span>{data.segments.never} never</button>
      </div>
    {/if}
    <div class="utools">
      <input class="usearch" placeholder="Search name / email…" bind:value={q}
        onkeydown={(ev) => { if (ev.key === 'Enter') { pg = 1; load(); } }} />
      <select bind:value={role} onchange={() => { pg = 1; load(); }}>
        <option value="">All roles</option><option value="user">User</option><option value="admin">Admin</option>
      </select>
      <select bind:value={authF} onchange={() => { pg = 1; load(); }}>
        <option value="">All auth</option><option value="local">Local</option><option value="ldap">LDAP</option><option value="oidc">SSO</option>
      </select>
    </div>
    {#if data}
      <div class="utable">
        <div class="uhead uhead2">
          <span class="uc-name">User</span>
          <button class="uc uc-num" onclick={() => setSort('questions')}>Qs {sort==='questions' ? (order==='desc'?'▾':'▴') : ''}</button>
          <button class="uc uc-num" onclick={() => setSort('active_days')}>Days {sort==='active_days' ? (order==='desc'?'▾':'▴') : ''}</button>
          <span class="uc uc-num">Multi</span>
          <span class="uc uc-num">Help</span>
          <span class="uc uc-num">Src</span>
          <button class="uc uc-num" onclick={() => setSort('blind')}>Blind {sort==='blind' ? (order==='desc'?'▾':'▴') : ''}</button>
          <span class="uc">14d</span>
          <button class="uc uc-last" onclick={() => setSort('last')}>Last {sort==='last' ? (order==='desc'?'▾':'▴') : ''}</button>
        </div>
        {#each data.users as u}
          <button class="urow urow2" onclick={() => openProfile(u.id)}>
            <span class="uc-name">
              <span class="udot" style="background:{STATUS_DOT[u.status]}"></span>
              <span class="uname">{u.name}</span><span class="uid">#{u.id}</span>
              {#each flags(u) as fl}
                <span class="uflag uflag-{fl.kind}" title={fl.title}>
                  {#if fl.kind === 'power'}<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2c2 4 5 5 5 9a5 5 0 0 1-10 0c0-1.5.6-2.6 1.4-3.6C9 9 9.5 10 10.5 10.2 9.8 8 11 4.5 12 2z"/></svg>{/if}
                  {#if fl.kind === 'risk'}<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>{/if}
                  {#if fl.kind === 'dormant'}<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>{/if}
                </span>
              {/each}
              {#if u.top_intent}<span class="utag">{INTENT_LABEL[u.top_intent] || u.top_intent}</span>{/if}
            </span>
            <span class="uc uc-num">{u.questions}</span>
            <span class="uc uc-num">{u.active_days}</span>
            <span class="uc uc-num">{u.multi_pct}%</span>
            <span class="uc uc-num" class:bad={u.helpful_pct !== null && u.helpful_pct < 60}>{u.helpful_pct === null ? '–' : u.helpful_pct + '%'}</span>
            <span class="uc uc-num">{u.sourced_pct === null ? '–' : u.sourced_pct + '%'}</span>
            <span class="uc uc-num" class:warn={u.blind > 0}>{u.blind}</span>
            <span class="uc"><span class="spark">{#each u.spark || [] as s}<i style="height:{Math.max(2, (s / sparkMax(u.spark)) * 20)}px"></i>{/each}</span></span>
            <span class="uc uc-last">{u.last_active ? ago(u.last_active) : '—'}</span>
          </button>
        {/each}
      </div>
      <div class="upager">
        <span class="muted sm">{data.total} users · page {data.page}/{data.pages || 1}</span>
        <span class="upgbtns">
          <button disabled={data.page <= 1} onclick={() => { pg = data.page - 1; load(); }}>‹ Prev</button>
          <button disabled={data.page >= data.pages} onclick={() => { pg = data.page + 1; load(); }}>Next ›</button>
        </span>
      </div>
    {:else}<div class="muted sm">Loading users…</div>{/if}
  </div>
{/if}

{#if profileId !== null}
  <div class="pscrim" onclick={closeProfile} role="presentation"></div>
  <aside class="pdrawer">
    {#if profile && !profile.error}
      {@const u = profile.user}
      <div class="phead">
        <div class="pav">{(u.name || '?').slice(0, 1).toUpperCase()}</div>
        <div class="pmeta">
          <div class="pname">{u.name}</div>
          <div class="psub">{u.email} · {u.role} · <span class="pst" style="color:{STATUS_DOT[u.status]}">{u.status}</span></div>
        </div>
        <button class="pclose" onclick={closeProfile}>✕</button>
      </div>
      <div class="pkpis">
        <div class="kcell"><div class="kcv">{profile.kpis.questions}</div><div class="kl">Questions</div></div>
        <div class="kcell"><div class="kcv">{profile.kpis.active_days}</div><div class="kl">Active days</div></div>
        <div class="kcell"><div class="kcv">{profile.kpis.helpful_pct === null ? '–' : profile.kpis.helpful_pct + '%'}</div><div class="kl">Helpful</div></div>
        <div class="kcell"><div class="kcv">{profile.kpis.blind}</div><div class="kl">Blind</div></div>
      </div>
      <div class="pcard">
        <div class="ctitle">Account</div>
        <div class="exrow" style="gap:16px">
          <span>Joined {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</span>
          <span>Last login {u.last_login ? ago(u.last_login) : '—'}</span>
          <span>Auth: {u.auth_source || 'local'}</span>
        </div>
        <div class="exrow" style="gap:16px; margin-top:4px">
          <span>{u.conversations ?? 0} conversations</span>
          <span>{u.feedback_given ?? 0} votes given</span>
          <span>{u.downvotes_given ?? 0} 👎</span>
        </div>
      </div>
      <div class="pcard">
        <div class="ctitle">Activity <span class="cnote">last 90 days</span></div>
        <Bars data={profile.activity} />
      </div>
      <div class="pcard">
        <div class="ctitle">Topic mix</div>
        {#if profile.intents?.length}
          {@const tot = profile.intents.reduce((s, x) => s + x.count, 0) || 1}
          <div class="intents">
            {#each profile.intents as it}
              <div class="introw">
                <span class="intl">{INTENT_LABEL[it.intent] || it.intent}</span>
                <span class="intbar"><span style="width:{(it.count / tot) * 100}%"></span></span>
                <span class="intp">{Math.round((it.count / tot) * 100)}%</span>
              </div>
            {/each}
          </div>
        {:else}<div class="muted sm">No activity yet.</div>{/if}
      </div>
      <div class="pcard">
        <div class="ctitle">Docs they rely on</div>
        {#if profile.top_docs?.length}
          <div class="rank">
            {#each profile.top_docs as d, i}
              <div class="rrow"><span class="ri">{i + 1}</span><span class="rl">{d.label}</span>
                <span class="rb"><span style="width:{(d.hits / profile.top_docs[0].hits) * 100}%"></span></span><span class="rv">{d.hits}×</span></div>
            {/each}
          </div>
        {:else}<div class="muted sm">No cited docs.</div>{/if}
      </div>
      <div class="pcard">
        <div class="ctitle">Facts taught</div>
        <div class="exrow"><span>{profile.facts.total} total</span><span>{profile.facts.active} active</span><span>{profile.facts.pending} pending</span></div>
      </div>
      <div class="privnote">🔒 Usage profile only — never shows {u.name}'s questions or answers.</div>
    {:else if profile?.error}
      <div class="phead"><div class="pname">User not found</div><button class="pclose" onclick={closeProfile}>✕</button></div>
    {:else}
      <div class="muted pad">Loading profile…</div>
    {/if}
  </aside>
{/if}

<style>
  .engrow{display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-bottom:14px;}
  @media (max-width:820px){ .engrow{grid-template-columns:1fr;} }
  .encard{min-width:0;}
  .enbig{font-size:30px; font-weight:700; font-variant-numeric:tabular-nums; color:var(--clay); line-height:1; margin:6px 0 4px;}
  .enden{font-size:12px; font-weight:500; color:var(--muted); margin-left:6px;}
  .retrow{display:flex; gap:8px; margin-top:8px;}
  .ret{flex:1; text-align:center; background:var(--sand); border-radius:10px; padding:10px 4px;}
  .retn{font-size:22px; font-weight:700; font-variant-numeric:tabular-nums;}
  .retl{font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-top:2px;}
  .dormlist{display:flex; flex-direction:column; gap:2px; margin-top:4px;}
  .dormrow{display:flex; align-items:center; justify-content:space-between; gap:8px; padding:5px 6px; border-radius:7px; background:none; border:none; cursor:pointer; text-align:left; width:100%;}
  .dormrow:hover{background:#f3f1ea;}
  .dormrow .de{font-size:12.5px; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
</style>
