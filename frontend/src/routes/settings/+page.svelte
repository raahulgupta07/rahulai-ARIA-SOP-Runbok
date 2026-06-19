<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import Preferences from './preferences/+page.svelte';

  let me = $state<User | null>(auth.cachedUser());
  let online = $state<boolean | null>(null);
  let ver = $state<any>(null);
  let stats = $state<{ docs?: number; pages?: number; sections?: number; facts?: number } | null>(null);
  let userCount = $state<number | null>(null);

  onMount(() => {
    if (!me) auth.me().then((u) => (me = u)).catch(() => {});
    api.health().then(() => (online = true)).catch(() => (online = false));
    api.version().then((v) => (ver = v)).catch(() => {});
    // public corpus counts — no auth header needed
    fetch('/api/stats/public')
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => { if (s) stats = s; })
      .catch(() => {});
  });

  let isAdmin = $derived(me?.role === 'admin');
  let initial = $derived((me?.name || me?.email || '?').trim().charAt(0).toUpperCase());

  // admin-only: load member count for the Users KPI
  $effect(() => {
    if (isAdmin && userCount === null) {
      api.adminUsers().then((u: any) => { userCount = Array.isArray(u) ? u.length : (u?.length ?? null); }).catch(() => {});
    }
  });

  // config map → grouped quick links (mirrors the rail, with descriptions)
  const LINKS = [
    { group: 'Workspace', items: [
      { href: '/settings/preferences', label: 'Preferences', desc: 'Density, motion, default page', admin: false, icon: 'sliders' },
      { href: '/settings/users',       label: 'Users',       desc: 'Members and roles',            admin: true,  icon: 'users' },
      { href: '/settings/auth',        label: 'Authentication', desc: 'Local, LDAP, SSO',          admin: true,  icon: 'shield' },
      { href: '/settings/governance',  label: 'Fact Approval',  desc: 'Auto / review policy',      admin: true,  icon: 'shield' }
    ]},
    { group: 'Integrations', items: [
      { href: '/settings/embed',     label: 'Embed Widget',    desc: 'Put Aria on any site',     admin: true, icon: 'code' },
      { href: '/settings/microsoft', label: 'Microsoft 365',   desc: 'SharePoint & OneDrive',    admin: true, icon: 'box' },
      { href: '/settings/storage',   label: 'Storage',         desc: 'Local or S3 backend',      admin: true, icon: 'box' },
      { href: '/settings/export',    label: 'Knowledge (OKF)', desc: 'Import / export bundles',  admin: true, icon: 'box' }
    ]}
  ];
  let groups = $derived(LINKS.map((g) => ({ ...g, items: g.items.filter((i) => !i.admin || isAdmin) })).filter((g) => g.items.length));
</script>

{#snippet jumpIcon(name: string)}
  {#if name === 'sliders'}
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>
  {:else if name === 'users'}
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
  {:else if name === 'shield'}
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" aria-hidden="true"><path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z"/><circle cx="12" cy="11" r="2"/></svg>
  {:else if name === 'code'}
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" aria-hidden="true"><path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/></svg>
  {:else}
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" aria-hidden="true"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="m3.27 6.96 8.73 5.05 8.73-5.05M12 22.08V12"/></svg>
  {/if}
{/snippet}

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <p class="ttlsub">Your account, system status and quick links to every setting.</p>

    <div class="grid">
      <!-- LEFT column -->
      <div class="col-main">
        <!-- KPI strip -->
        <div class="kpis">
          <div class="kpi">
            <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg></div>
            <div class="k-num">{stats?.docs ?? '—'}</div>
            <div class="k-lbl">Runbooks</div>
          </div>
          <div class="kpi">
            <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg></div>
            <div class="k-num">{stats?.pages ?? '—'}</div>
            <div class="k-lbl">Pages indexed</div>
          </div>
          <div class="kpi">
            <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M12 2l2.4 7.4H22l-6 4.5 2.3 7.1L12 16.6 5.7 21l2.3-7.1-6-4.5h7.6z"/></svg></div>
            <div class="k-num">{stats?.facts ?? '—'}</div>
            <div class="k-lbl">Facts</div>
          </div>
          <div class="kpi">
            <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg></div>
            <div class="k-num">{isAdmin ? (userCount ?? '—') : '—'}</div>
            <div class="k-lbl">Users</div>
          </div>
        </div>

        <!-- preferences (folded in, unchanged behavior) -->
        <Preferences embedded />

        <!-- jump to -->
        <div class="lbl">Jump to</div>
        {#each groups as g (g.group)}
          <div class="grp-head">{g.group}</div>
          <div class="links">
            {#each g.items as it (it.href)}
              <a class="link" href={it.href}>
                <span class="l-ic">{@render jumpIcon(it.icon)}</span>
                <span class="l-txt">
                  <b>{it.label}</b>
                  <span>{it.desc}</span>
                </span>
                <span class="l-arrow">→</span>
              </a>
            {/each}
          </div>
        {/each}
      </div>

      <!-- RIGHT rail -->
      <div class="col-rail">
        <!-- account -->
        <div class="acct">
          <div class="avatar">{initial}</div>
          <div class="acct-meta">
            <div class="acct-name">{me?.name || me?.email || '—'}</div>
            <div class="acct-sub">{me?.email || ''}</div>
            {#if me?.role}<span class="rolepill">{me.role}</span>{/if}
          </div>
          <button class="btn ghost signout" onclick={() => { auth.logout(); goto('/login'); }}>Sign out</button>
        </div>

        <!-- system status -->
        <div class="syscard">
          <div class="sys-head">
            <span class="stat" class:ok={online} class:bad={online === false}>
              <span class="dot"></span>{online == null ? 'Checking…' : online ? 'Healthy' : 'Offline'}
            </span>
            <span class="sys-title">System status</span>
          </div>
          {#if ver}
            <div class="sysrow">
              <span class="sys-k">Version</span>
              <code class="mono">{ver.version ?? ver.tag ?? '—'}</code>
            </div>
          {/if}
          <div class="sysrow">
            <span class="sys-k">Database</span>
            <span class="stat" class:ok={online} class:bad={online === false}>
              <span class="dot"></span>{online == null ? 'checking…' : online ? 'Connected' : 'Offline'}
            </span>
          </div>
          <div class="sysrow">
            <span class="sys-k">Backend</span>
            <code class="mono">{api.base}</code>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-bottom:18px;}

  /* full-width 2-col layout */
  .grid{display:grid; grid-template-columns:2fr 1fr; gap:16px; align-items:start;}
  .col-main{min-width:0;}
  .col-rail{min-width:0; display:flex; flex-direction:column; gap:16px;}

  /* account card (right rail) */
  .acct{display:flex; align-items:flex-start; gap:11px; border:1px solid var(--border); border-radius:13px; background:#fff; padding:14px;}
  .avatar{width:42px; height:42px; border-radius:50%; background:#1a1a18; color:#fff; font-size:17px; font-weight:700; display:grid; place-items:center; flex:0 0 auto;}
  .acct-meta{min-width:0; line-height:1.3; flex:1;}
  .acct-name{font-size:14px; font-weight:600; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .acct-sub{font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .rolepill{display:inline-block; margin-top:6px; font-size:11px; font-weight:600; text-transform:capitalize; background:#eaf0f7; color:#426693; padding:3px 9px; border-radius:999px;}

  /* KPI strip */
  .kpis{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:18px;}
  .kpi{border:1px solid var(--border); border-radius:13px; background:#fff; padding:15px;}
  .k-ic{width:34px; height:34px; border-radius:9px; background:#f4f3f0; display:grid; place-items:center; margin-bottom:11px;}
  .k-num{font-size:24px; font-weight:700; color:var(--ink); line-height:1; min-height:24px; display:flex; align-items:center;}
  .k-lbl{font-size:11.5px; color:var(--muted); margin-top:6px;}

  /* section labels */
  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin:22px 0 10px;}
  .grp-head{font-size:11.5px; font-weight:600; color:var(--ink); margin:10px 0 8px;}

  /* jump-to cards — 3 cols */
  .links{display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:11px; margin-bottom:6px;}
  .link{display:flex; align-items:center; gap:12px; text-decoration:none; padding:13px 15px; border:1px solid var(--border); border-radius:13px; background:#fff; transition:border-color .12s, box-shadow .12s;}
  .link:hover{border-color:#cfcabf;}
  .l-ic{width:38px; height:38px; border-radius:10px; background:#f4f3f0; display:grid; place-items:center; flex:0 0 auto;}
  .l-txt{flex:1; min-width:0; display:flex; flex-direction:column;}
  .l-txt b{font-size:14px; font-weight:600; color:var(--ink);}
  .l-txt span{font-size:12px; color:var(--muted); margin-top:2px;}
  .l-arrow{color:var(--muted); font-weight:700; font-size:15px; flex:0 0 auto;}
  .link:hover .l-arrow{color:var(--ink);}

  /* system card */
  .syscard{border:1px solid var(--border); border-radius:13px; background:#fff; overflow:hidden;}
  .sys-head{display:flex; align-items:center; gap:9px; padding:13px 15px; border-bottom:1px solid var(--border);}
  .sys-title{font-size:13px; font-weight:600; color:var(--ink); margin-left:auto;}
  .sysrow{display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 15px;}
  .sysrow + .sysrow{border-top:1px solid var(--border);}
  .sys-k{font-size:13px; color:var(--ink); font-weight:500;}
  .stat{display:inline-flex; align-items:center; gap:7px; font-size:13px; color:var(--muted);}
  .stat .dot{width:8px; height:8px; border-radius:50%; background:#c9c4b8;}
  .stat.ok{color:#2f8f5f;} .stat.ok .dot{background:#2f8f5f;}
  .stat.bad{color:#c0492f;} .stat.bad .dot{background:#c0492f;}
  .mono{font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; color:var(--ink); background:var(--sand); padding:3px 9px; border-radius:6px;}

  /* buttons (match auth) */
  .btn{height:34px; padding:0 14px; border-radius:9px; font-size:12.5px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff;}
  .btn.ghost{border:none; background:transparent; color:var(--muted); flex:0 0 auto;}
  .signout:hover{color:#c0492f;}

  @media (max-width:900px){
    .grid{grid-template-columns:1fr;}
  }
  @media (max-width:640px){
    .kpis{grid-template-columns:1fr 1fr;}
    .links{grid-template-columns:1fr;}
  }
</style>
