<script lang="ts">
  import { page } from '$app/stores';
  import { auth } from '$lib/auth';

  let { children } = $props();
  let me = $state<any>(null);
  $effect(() => { auth.me().then((u) => (me = u)).catch(() => {}); });

  type Tab = { href: string; label: string; group: string; desc: string; admin?: boolean; exact?: boolean; d: string };
  const tabs: Tab[] = [
    { href: '/settings',             label: 'Overview',        group: 'Workspace',    desc: 'Your account, preferences and system info.', exact: true, d: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z' },
    { href: '/settings/users',       label: 'Users',           group: 'Workspace',    desc: 'Manage who can access the workspace and their roles.', admin: true, d: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75' },
    { href: '/settings/auth',        label: 'Authentication',  group: 'Workspace',    desc: 'Login methods — local, LDAP and single sign-on.', admin: true, d: 'M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5z' },
    { href: '/settings/governance',  label: 'Fact Approval',   group: 'Workspace',    desc: 'Which learned facts go live automatically vs need admin review.', admin: true, d: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11' },
    { href: '/settings/embed',       label: 'Embed Widget',    group: 'Integrations', desc: 'Drop the chat widget into any site and manage embed keys.', admin: true, d: 'M16 18l6-6-6-6M8 6l-6 6 6 6' },
    { href: '/settings/api',         label: 'API Access',      group: 'Integrations', desc: 'Call the agent from other systems over REST.', admin: true, d: 'M9 2v6M15 2v6M5 8h14v5a7 7 0 0 1-14 0zM12 20v2' },
    { href: '/settings/teams',       label: 'Microsoft Teams', group: 'Integrations', desc: 'Run Aria as a bot inside Microsoft Teams.', admin: true, d: 'M4 5h16v14H4zM4 9h16M9 9v10' },
    { href: '/settings/export',      label: 'Knowledge (OKF)', group: 'Integrations', desc: 'Import and export knowledge as Open Knowledge Format bundles.', admin: true, d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3' },
    { href: '/settings/storage',     label: 'Storage',         group: 'Integrations', desc: 'Where files live — local disk or S3/MinIO — and bulk-import from a bucket.', admin: true, d: 'M4 7c0 1.66 3.58 3 8 3s8-1.34 8-3-3.58-3-8-3-8 1.34-8 3zM4 7v5c0 1.66 3.58 3 8 3s8-1.34 8-3V7M4 12v5c0 1.66 3.58 3 8 3s8-1.34 8-3v-5' }
  ];

  let path = $derived($page.url.pathname);
  let q = $state('');
  let visible = $derived(tabs.filter((t) => (!t.admin || me?.role === 'admin') && (!q.trim() || t.label.toLowerCase().includes(q.toLowerCase()) || t.desc.toLowerCase().includes(q.toLowerCase()))));
  let groups = $derived([...new Set(visible.map((t) => t.group))]);
  function active(t: Tab) { return t.exact ? path === t.href : path.startsWith(t.href); }
  let current = $derived(tabs.find((t) => active(t)));
</script>

<div class="h-full flex" style="background:var(--cream)">
  <!-- left sub-rail -->
  <aside class="srail shrink-0 flex flex-col">
    <div class="srail-search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <input placeholder="Search settings…" bind:value={q} />
    </div>
    <nav class="srail-nav">
      {#each groups as g}
        <div class="srail-group">{g}</div>
        {#each visible.filter((t) => t.group === g) as t}
          {@const on = active(t)}
          <a href={t.href} class="srail-item" class:on title={t.desc}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d={t.d}/></svg>
            <span>{t.label}</span>
          </a>
        {/each}
      {/each}
      {#if !visible.length}<div class="srail-empty">No match.</div>{/if}
    </nav>
  </aside>

  <!-- content -->
  <div class="flex-1 min-h-0 overflow-y-auto">
    <div class="px-7 pt-6 pb-1">
      <h1 class="serif text-[21px] font-medium" style="color:var(--ink)">{current?.label ?? 'Settings'}</h1>
      {#if current?.desc}<p class="text-[13px] mt-1" style="color:var(--muted)">{current.desc}</p>{/if}
    </div>
    {@render children()}
  </div>
</div>

<style>
  .srail { width: 218px; background: var(--sand); border-right: 1px solid #efefec; padding: 16px 11px; gap: 12px; }
  .srail-search { display: flex; align-items: center; gap: 7px; background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 7px 10px; color: var(--muted); }
  .srail-search input { border: none; background: transparent; outline: none; font-size: 13px; color: var(--ink); width: 100%; }
  .srail-nav { display: flex; flex-direction: column; gap: 1px; overflow-y: auto; }
  .srail-group { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); padding: 14px 8px 5px; font-weight: 600; }
  .srail-group:first-child { padding-top: 4px; }
  .srail-item { display: flex; align-items: center; gap: 10px; height: 36px; padding: 0 11px; border-radius: 9px; font-size: 14px; color: #46443f; transition: background .14s, color .14s; }
  .srail-item:hover { background: #efefec; }
  .srail-item.on { background: #f0efed; color: var(--ink); font-weight: 600; }
  .srail-item svg { flex: none; opacity: .85; }
  .srail-empty { font-size: 12.5px; color: var(--muted); padding: 12px 8px; }
</style>
