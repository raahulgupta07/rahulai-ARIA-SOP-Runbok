<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth, type User } from '$lib/auth';
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Preferences from './preferences/+page.svelte';

  let me = $state<User | null>(auth.cachedUser());
  let online = $state<boolean | null>(null);
  let ver = $state<any>(null);

  onMount(() => {
    if (!me) auth.me().then((u) => (me = u)).catch(() => {});
    api.health().then(() => (online = true)).catch(() => (online = false));
    api.version().then((v) => (ver = v)).catch(() => {});
  });

  let isAdmin = $derived(me?.role === 'admin');
  let initial = $derived((me?.name || me?.email || '?').trim().charAt(0).toUpperCase());

  // config map → grouped quick links (mirrors the rail, with descriptions)
  const LINKS = [
    { group: 'Workspace', items: [
      { href: '/settings/preferences', label: 'Preferences', desc: 'Density, motion, default page', admin: false },
      { href: '/settings/users',       label: 'Users',       desc: 'Members and roles',            admin: true },
      { href: '/settings/auth',        label: 'Authentication', desc: 'Local, LDAP, SSO',          admin: true }
    ]},
    { group: 'Integrations', items: [
      { href: '/settings/embed',  label: 'Embed Widget',    desc: 'Put Aria on any site',       admin: true },
      { href: '/settings/export', label: 'Knowledge (OKF)', desc: 'Import / export bundles',     admin: true }
    ]}
  ];
  let groups = $derived(LINKS.map((g) => ({ ...g, items: g.items.filter((i) => !i.admin || isAdmin) })).filter((g) => g.items.length));
</script>

<div class="px-7 py-6">
  <!-- account -->
  <Section title="Account" desc="You're signed in to City Agent Aria.">
    <div class="acct">
      <div class="avatar">{initial}</div>
      <div class="acct-meta">
        <div class="acct-name">{me?.name || me?.email || '—'}</div>
        <div class="acct-sub">{me?.email || ''}{#if me?.role} · <span class="role">{me.role}</span>{/if}</div>
      </div>
      <button class="signout" onclick={() => { auth.logout(); goto('/login'); }}>Sign out</button>
    </div>
  </Section>

  <!-- preferences (folded in) -->
  <Preferences embedded />

  <!-- system -->
  <Section title="System">
    <Row label="Status" hint="Backend connectivity from this browser.">
      <span class="stat" class:ok={online} class:bad={online === false}>
        <span class="dot"></span>{online == null ? 'checking…' : online ? 'Connected' : 'Offline'}
      </span>
    </Row>
    {#if ver}
      <Row label="Version"><code class="mono">{ver.version ?? ver.tag ?? '—'}</code></Row>
    {/if}
    <Row label="Backend"><code class="mono">{api.base}</code></Row>
  </Section>
</div>

<style>
  .acct { display: flex; align-items: center; gap: 14px; }
  .avatar { width: 46px; height: 46px; border-radius: 50%; background: #1a1a18; color: #fff; font-size: 19px; font-weight: 700; display: grid; place-items: center; flex: none; }
  .acct-meta { flex: 1; min-width: 0; }
  .acct-name { font-size: 15px; font-weight: 600; color: var(--ink); }
  .acct-sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
  .role { text-transform: capitalize; color: #1a1a18; font-weight: 600; }
  .signout { flex: none; font-size: 12.5px; padding: 7px 14px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: #46443f; cursor: pointer; }
  .signout:hover { border-color: #c0492f; color: #c0492f; }

  .bigjump { display: flex; align-items: center; gap: 13px; width: 100%; text-align: left; padding: 14px 16px; border: 1px solid var(--border); border-radius: 12px; background: linear-gradient(100deg, rgba(44,127,255,.06), #fff); cursor: pointer; transition: border-color .15s, transform .12s; }
  .bigjump:hover { border-color: #1a1a18; transform: translateY(-1px); }
  .bj-ic { font-size: 22px; flex: none; }
  .bj-txt { flex: 1; display: flex; flex-direction: column; }
  .bj-txt b { font-size: 14px; color: var(--ink); }
  .bj-txt span { font-size: 12px; color: var(--muted); }
  .bj-arrow { color: #1a1a18; font-size: 18px; font-weight: 700; }

  .lg-head { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; margin: 14px 0 7px; }
  .lg-head:first-child { margin-top: 0; }
  .links { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
  .link { display: flex; align-items: center; gap: 10px; text-align: left; padding: 11px 13px; border: 1px solid var(--border); border-radius: 10px; background: #fff; cursor: pointer; transition: border-color .14s, background .14s; }
  .link:hover { border-color: #1a1a18; background: #fbfdfc; }
  .l-txt { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .l-txt b { font-size: 13.5px; color: var(--ink); }
  .l-txt span { font-size: 11.5px; color: var(--muted); }
  .l-arrow { color: var(--muted); font-weight: 700; }
  .link:hover .l-arrow { color: #1a1a18; }

  .stat { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--muted); }
  .stat .dot { width: 8px; height: 8px; border-radius: 50%; background: #c9c4b8; }
  .stat.ok { color: #3f8f5f; } .stat.ok .dot { background: #5fa463; }
  .stat.bad { color: #c0492f; } .stat.bad .dot { background: #cf6a4c; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--ink); background: var(--sand); padding: 2px 8px; border-radius: 6px; }
</style>
