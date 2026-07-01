<script lang="ts">
  import '../app.css';
  import '$lib/prefs';   // boot: load + apply density / reduced-motion to <html>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { auth, type User } from '$lib/auth';
  import Burst from '$lib/Burst.svelte';
  import Bell from '$lib/Bell.svelte';
  import IngestRobot from '$lib/IngestRobot.svelte';
  import { convs, activeConvId, reloadConvs, openConvId, triggerNewChat } from '$lib/chatstore';
  import { mobileNav } from '$lib/dashstore';
  import { brand, loadBrand } from '$lib/brand';
  import { onMount } from 'svelte';

  // ===== runtime white-label theming =====
  // fetch the public brand config once; the reactive blocks below inject the
  // accent CSS vars + swap the favicon when it arrives (null = baked defaults).
  onMount(() => { loadBrand(); });

  // accent vars injected into <svelte:head> as a :root <style> — comes after
  // app.css so it overrides --clay / --clay-dk (both are plain custom props).
  let accentStyle = $derived(
    $brand ? `:root{--clay:${$brand.accent};--clay-dk:${$brand.accent_dk};}` : ''
  );

  // swap the favicon + apple-touch-icon to the brand assets when present
  $effect(() => {
    if (typeof document === 'undefined' || !$brand) return;
    const set = (rel: string, href?: string) => {
      if (!href) return;
      let link = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
      if (!link) { link = document.createElement('link'); link.rel = rel; document.head.appendChild(link); }
      link.href = href;
    };
    set('icon', $brand.favicon_url);
    set('apple-touch-icon', $brand.icon192_url);
  });

  // header brand strings + logo with sensible fallbacks to today's defaults
  let brandName = $derived($brand?.name || 'City Agent Aria');
  let brandLogo = $derived($brand?.logo_url || '/brand-logo.png');

  // PWA: register the service worker so the app is installable on phones
  onMount(() => {
    if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
  });

  // pull-to-refresh (touch): drag down from the top of any scroll area to reload
  let ptr = $state(0);                 // current pull distance (px, damped)
  const PTR_TRIGGER = 70;
  onMount(() => {
    let startY = 0, pulling = false, sc: any = null;
    const findScroller = (el: any) => {
      while (el && el !== document.body) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 2) return el;
        el = el.parentElement;
      }
      return null;
    };
    const ts = (e: TouchEvent) => {
      sc = findScroller(e.target);
      pulling = (sc ? sc.scrollTop <= 0 : (window.scrollY || 0) <= 0);
      startY = e.touches[0].clientY; ptr = 0;
    };
    const tm = (e: TouchEvent) => {
      if (!pulling) return;
      const dy = e.touches[0].clientY - startY;
      const atTop = sc ? sc.scrollTop <= 0 : (window.scrollY || 0) <= 0;
      if (dy > 0 && atTop) { ptr = Math.min(110, dy * 0.5); if (e.cancelable) e.preventDefault(); }
      else { ptr = 0; pulling = false; }
    };
    const te = () => {
      if (pulling && ptr > PTR_TRIGGER) { ptr = 56; setTimeout(() => location.reload(), 150); }
      else ptr = 0;
      pulling = false;
    };
    document.addEventListener('touchstart', ts, { passive: true });
    document.addEventListener('touchmove', tm, { passive: false });
    document.addEventListener('touchend', te, { passive: true });
    return () => {
      document.removeEventListener('touchstart', ts);
      document.removeEventListener('touchmove', tm);
      document.removeEventListener('touchend', te);
    };
  });

  let { children } = $props();

  // ===== conversation rail (history lives here now) =====
  let search = $state('');
  let renaming = $state<number | null>(null);
  let renameText = $state('');
  $effect(() => { if (!isLogin && !isEmbed && auth.isAuthed()) reloadConvs(); });

  // chat-history rail overlay shares the global mobileNav toggle (header hamburger)
  function railNewChat() { mobileNav.set(false); triggerNewChat(); if ($page.url.pathname !== '/') goto('/'); }
  function railOpen(id: number) { mobileNav.set(false); openConvId(id); if ($page.url.pathname !== '/') goto('/'); }
  // close any open mobile overlay when the route changes
  $effect(() => { $page.url.pathname; mobileNav.set(false); });
  async function railDelete(c: any, e: Event) {
    e.stopPropagation();
    if (!confirm(`Delete "${c.title}"?`)) return;
    try { await api.deleteConversation(c.id); if (get_active() === c.id) triggerNewChat(); await reloadConvs(); } catch {}
  }
  function railStartRename(c: any, e: Event) { e.stopPropagation(); renaming = c.id; renameText = c.title; }
  async function railCommitRename(c: any) {
    const t = renameText.trim(); renaming = null;
    if (!t || t === c.title) return;
    try { await api.renameConversation(c.id, t); await reloadConvs(); } catch {}
  }
  let activeCid = $state<number | null>(null);
  activeConvId.subscribe((v) => (activeCid = v));
  function get_active() { return activeCid; }

  function bucket(ts: string): string {
    const d = new Date(ts), now = new Date(), day = 86400000;
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const t = d.getTime();
    if (t >= startToday) return 'Today';
    if (t >= startToday - day) return 'Yesterday';
    if (t >= startToday - 7 * day) return 'Previous 7 days';
    return 'Older';
  }
  let grouped = $derived.by(() => {
    const order = ['Today', 'Yesterday', 'Previous 7 days', 'Older'];
    const map: Record<string, any[]> = {};
    const nd = search.trim().toLowerCase();
    for (const c of $convs) {
      if (nd && !(c.title || '').toLowerCase().includes(nd)) continue;
      (map[bucket(c.updated_at)] ||= []).push(c);
    }
    return order.filter((g) => map[g]?.length).map((g) => ({ g, items: map[g] }));
  });
  let onChat = $derived($page.url.pathname === '/');

  let me = $state<User | null>(null);
  let menuOpen = $state(false);
  // single source of truth for role: admin manages the knowledge base, everyone
  // else is chat-only (no Workspace/Brain/Settings, no upload/teach/edit).
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  // per-group feature gating for the top nav. admins see all; others see only the
  // tabs enabled for their group. features undefined (not loaded) => fail open to all.
  const ALL_FEAT = ['chat', 'sources', 'workspace', 'eval', 'wiki'];
  let feats = $derived(new Set((me?.features ?? ALL_FEAT) as string[]));
  let navList = $derived.by(() => {
    if (isAdmin) return [...topnav, evalNav, wikiNav];
    if (!me) return [];
    const out: any[] = [];
    if (feats.has('chat')) out.push(topnav[0]);       // Chat
    if (feats.has('workspace')) out.push(topnav[1]);  // Workspace
    if (feats.has('sources')) out.push(topnav[2]);    // Sources
    if (feats.has('eval')) out.push(evalNav);
    if (feats.has('wiki')) out.push(wikiNav);
    return out;
  });

  let isLogin = $derived($page.url.pathname.startsWith('/login'));  // /login + /login/admin
  let isEmbed = $derived($page.url.pathname === '/embed');  // bare iframe widget
  let isShare = $derived($page.url.pathname.startsWith('/s/'));  // read-only shared answer
  $effect(() => {
    if (isLogin || isEmbed || isShare) return;
    if (!auth.isAuthed()) { goto('/login'); return; }
    if (!me) auth.me().then((u) => { me = u; if (!u) goto('/login'); });
  });
  // chat-only users can't reach admin areas even by typing the URL
  $effect(() => {
    if (isLogin || isEmbed || isShare || !me) return;
    if (!isAdmin && (/^\/(workspace|settings|brain|sources|eval)/.test($page.url.pathname))) goto('/');
  });

  // primary nav — Chat is implicit (New chat + history), so only sections here
  const nav = [
    { href: '/brain', label: 'Brain', section: '/brain', d: 'M9.5 2a4.5 4.5 0 0 0-4.5 4.5c-1.2.5-2 1.7-2 3 0 .8.3 1.5.8 2-.5.5-.8 1.2-.8 2 0 1.6 1.3 3 3 3a3 3 0 0 0 3 3 2.5 2.5 0 0 0 2.5-2.5V4.5A2.5 2.5 0 0 0 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v14.5a2.5 2.5 0 0 0 2.5 2.5 3 3 0 0 0 3-3c1.7 0 3-1.4 3-3 0-.8-.3-1.5-.8-2 .5-.5.8-1.2.8-2 0-1.3-.8-2.5-2-3A4.5 4.5 0 0 0 14.5 2z' },
    { href: '/settings', label: 'Settings', section: '/settings', d: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13a7.5 7.5 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-1.7-1L15 3h-4l-.3 2.5a7.5 7.5 0 0 0-1.7 1l-2.4-1-2 3.5L4.6 11a7.5 7.5 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 1.7 1L11 21h4l.3-2.5a7.5 7.5 0 0 0 1.7-1l2.4 1 2-3.5z' }
  ];
  // top bar nav — Chat, unified Workspace (Dashboard + Brain folded in), Settings
  const topnav = [
    { href: '/', label: 'Chat', section: '/', d: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z' },
    { href: '/workspace', label: 'Workspace', section: '/workspace', d: 'M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z' },
    { href: '/sources', label: 'Sources', section: '/sources', d: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z' },
    { href: '/settings', label: 'Settings', section: '/settings', d: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13a7.5 7.5 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-1.7-1L15 3h-4l-.3 2.5a7.5 7.5 0 0 0-1.7 1l-2.4-1-2 3.5L4.6 11a7.5 7.5 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 1.7 1L11 21h4l.3-2.5a7.5 7.5 0 0 0 1.7-1l2.4 1 2-3.5z' }
  ];
  // Wiki — browsable knowledge base, shown for ANY logged-in user (like Chat)
  const wikiNav = { href: '/wiki', label: 'Wiki', section: '/wiki', d: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15zM8 7h8M8 11h6' };
  // Eval — offline answer-quality scoring dashboard (admin only)
  const evalNav = { href: '/eval', label: 'Eval', section: '/eval', d: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11' };

  // mobile bottom tab bar — Chat · Workspace · Brain · Settings
  const bottomnav = [
    { href: '/', label: 'Chat', section: '/', d: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z' },
    { href: '/workspace', label: 'Workspace', section: '/workspace', d: 'M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z' },
    { href: '/brain', label: 'Brain', section: '/brain', d: 'M9.5 2a4.5 4.5 0 0 0-4.5 4.5c-1.2.5-2 1.7-2 3 0 .8.3 1.5.8 2-.5.5-.8 1.2-.8 2 0 1.6 1.3 3 3 3a3 3 0 0 0 3 3 2.5 2.5 0 0 0 2.5-2.5V4.5A2.5 2.5 0 0 0 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v14.5a2.5 2.5 0 0 0 2.5 2.5 3 3 0 0 0 3-3c1.7 0 3-1.4 3-3 0-.8-.3-1.5-.8-2 .5-.5.8-1.2.8-2 0-1.3-.8-2.5-2-3A4.5 4.5 0 0 0 14.5 2z' },
    { href: '/settings', label: 'Settings', section: '/settings', d: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13a7.5 7.5 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-1.7-1L15 3h-4l-.3 2.5a7.5 7.5 0 0 0-1.7 1l-2.4-1-2 3.5L4.6 11a7.5 7.5 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 1.7 1L11 21h4l.3-2.5a7.5 7.5 0 0 0 1.7-1l2.4 1 2-3.5z' }
  ];

  // collapsible rail (persisted)
  let collapsed = $state(false);
  $effect(() => {
    if (typeof localStorage === 'undefined') return;
    // auto-collapse the chat rail on phones so the conversation gets the width;
    // otherwise honour the saved preference
    collapsed = window.innerWidth <= 640 ? true : localStorage.getItem('aria_rail') === '1';
  });
  function toggleRail() { collapsed = !collapsed; if (typeof localStorage !== 'undefined') localStorage.setItem('aria_rail', collapsed ? '1' : '0'); }

  let path = $derived($page.url.pathname);
  function sectionActive(s: string) { return s === '/' ? path === '/' : path.startsWith(s); }

  let online = $state(false);
  $effect(() => { api.health().then(() => (online = true)).catch(() => (online = false)); });

  function logout() { auth.logout(); me = null; menuOpen = false; goto('/login'); }
</script>

<!-- runtime white-label: inject accent vars on :root (after app.css → overrides defaults) -->
<svelte:head>
  {#if accentStyle}
    {@html `<style>${accentStyle}</style>`}
  {/if}
</svelte:head>

{#if isLogin || isEmbed || isShare}
  {@render children()}
{:else}
<div class="flex flex-col h-screen overflow-hidden">

  <!-- pull-to-refresh indicator (mobile) -->
  {#if ptr > 0}
    <div class="ptr-ind" style="transform:translateX(-50%) translateY({ptr - 44}px); opacity:{Math.min(1, ptr / 56)}">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="transform:rotate({Math.min(ptr * 2.4, 360)}deg); transition:transform .05s">
        {#if ptr > 70}<path d="M21 12a9 9 0 1 1-6.2-8.5"/><path d="M21 3v6h-6"/>{:else}<path d="M12 5v14M5 12l7 7 7-7"/>{/if}
      </svg>
    </div>
  {/if}

  <!-- ===== GLOBAL FULL-WIDTH HEADER (logo · nav · bell · user) ===== -->
  <header class="h-14 shrink-0 flex items-center gap-1 px-3 border-b" style="border-color:#efefec; background:var(--sand)">
    {#if me}
      <button class="hdr-burger" onclick={() => { collapsed = false; mobileNav.set(true); }} aria-label="Menu">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
    {/if}
    <a href="/" class="flex items-center shrink-0 pl-1 pr-1" title={brandName}>
      <img src={brandLogo} alt={brandName} class="h-12 w-auto" />
    </a>
    <div class="w-2"></div>
    <nav class="topnav-row flex items-center gap-1">
    {#each navList as t}
      {@const on = sectionActive(t.section)}
      <a href={t.href} class="flex items-center gap-2 rounded-[9px] h-9 px-3 text-[14px] transition"
         style={on ? 'background:#f0efed; color:var(--ink); font-weight:600;' : 'color:#46443f;'}
         title={t.label}
         onmouseenter={(e)=>{ if(!on) e.currentTarget.style.background='#efefec'; }}
         onmouseleave={(e)=>{ if(!on) e.currentTarget.style.background='transparent'; }}>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d={t.d}/></svg>
        <span class="hidden sm:inline">{t.label}</span>
      </a>
    {/each}
    </nav>
    {#if me}
      <div class="ml-auto flex items-center gap-1.5">
        <!-- ingest activity robot + notifications are admin/ops tools — hide for chat-only users -->
        {#if isAdmin}
          <IngestRobot inline />
          <Bell />
        {/if}
        <div class="relative">
          <button onclick={() => (menuOpen = !menuOpen)} class="flex items-center gap-2 px-1.5 py-1.5 rounded-lg hover:bg-[#efefec]">
            <span class="w-7 h-7 shrink-0 rounded-full grid place-items-center text-white text-xs font-semibold" style="background:var(--clay)">{(me.name || me.email)[0]?.toUpperCase()}</span>
            <span class="text-left leading-tight hidden sm:block">
              <span class="block text-[13px]" style="color:var(--ink)">{me.name || me.email}</span>
              <span class="block text-[10px] uppercase tracking-wide" style="color:var(--muted)">{me.role}</span>
            </span>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
          </button>
          {#if menuOpen}
            <div class="absolute right-0 top-full mt-1.5 w-48 rounded-xl border shadow-lg py-1.5 z-50" style="background:var(--paper); border-color:var(--border)"
                 role="presentation" onmouseleave={() => (menuOpen = false)}>
              <div class="px-3 py-1.5 text-[11px]" style="color:var(--muted)">{me.email}</div>
              {#if isAdmin}<a href="/settings" onclick={() => (menuOpen = false)} class="block px-3 py-2 text-[13.5px] hover:bg-[#ece9e0]" style="color:#46443f">Settings</a>{/if}
              <button onclick={logout} class="w-full text-left px-3 py-2 text-[13.5px] hover:bg-[#ece9e0]" style="color:#46443f">Sign out</button>
            </div>
          {/if}
        </div>
      </div>
    {/if}
  </header>

  <div class="flex flex-1 min-h-0 overflow-hidden">

  <!-- ===== LEFT RAIL (warm sand) — Chat route only ===== -->
  {#if onChat}
  {#if $mobileNav}
    <button class="chat-rail-scrim" onclick={() => mobileNav.set(false)} aria-label="Close chat history"></button>
  {/if}
  <aside class="chat-rail shrink-0 flex flex-col border-r transition-[width] duration-150" class:mopen={$mobileNav} style="width:{collapsed ? '58px' : '232px'}; background:var(--sand); border-color:#efefec">

    <!-- New chat -->
    <div class="px-2.5 pt-3 pb-2 shrink-0">
      <button onclick={railNewChat} class="w-full flex items-center gap-2.5 rounded-[9px] text-[13.5px] font-medium {collapsed ? 'justify-center px-0 h-9' : 'px-2.5 h-9'}" style="color:#2b2a27; background:transparent" title="New chat"
        onmouseenter={(e)=>e.currentTarget.style.background='#efefec'} onmouseleave={(e)=>e.currentTarget.style.background='transparent'}>
        <span class="grid place-items-center w-[22px] h-[22px] rounded-full border" style="border-color:#cfc9bb"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg></span>
        {#if !collapsed}New chat{/if}
      </button>
    </div>

    <!-- conversation history -->
    {#if !collapsed}
      <div class="mt-3 px-2.5 pb-1 shrink-0">
        <div class="relative">
          <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
          <input bind:value={search} placeholder="Search chats"
            class="w-full bg-transparent outline-none pl-8 pr-2.5 h-8 text-[13px] rounded-[9px]" style="color:#46443f"
            onfocus={(e)=>e.currentTarget.style.background='#efefec'} onblur={(e)=>e.currentTarget.style.background='transparent'} />
        </div>
      </div>
      <div class="flex-1 min-h-0 overflow-y-auto px-2 pb-2">
        {#if $convs.length === 0}
          <div class="px-3 py-5 text-center text-[11.5px]" style="color:var(--muted)">No conversations yet.</div>
        {/if}
        {#each grouped as grp}
          <div class="px-2.5 pt-3 pb-1 text-[10px] uppercase tracking-wide" style="color:var(--muted)">{grp.g}</div>
          {#each grp.items as c (c.id)}
            {@const on = activeCid === c.id && onChat}
            <div class="group flex items-center gap-0.5 rounded-[8px] pr-1"
                 style={on ? 'background:#e6e2d8;' : ''}
                 onmouseenter={(e)=>{ if(!on) e.currentTarget.style.background='#efefec'; }}
                 onmouseleave={(e)=>{ if(!on) e.currentTarget.style.background='transparent'; }}
                 role="presentation">
              {#if renaming === c.id}
                <input class="flex-1 bg-white border rounded-md px-2 py-1 text-[12.5px] mx-1 my-1 min-w-0" style="border-color:var(--clay)"
                  bind:value={renameText} onblur={() => railCommitRename(c)}
                  onkeydown={(e) => { if (e.key === 'Enter') railCommitRename(c); if (e.key === 'Escape') renaming = null; }} />
              {:else}
                <button onclick={() => railOpen(c.id)} class="flex-1 min-w-0 text-left px-2.5 py-2 text-[13px] truncate"
                  style="color:{on ? 'var(--ink)' : '#46443f'}">{c.title}</button>
                <button onclick={(e) => railStartRename(c, e)} class="opacity-0 group-hover:opacity-100 p-1 rounded shrink-0" title="Rename" style="color:var(--muted)">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
                </button>
                <button onclick={(e) => railDelete(c, e)} class="opacity-0 group-hover:opacity-100 p-1 rounded shrink-0" title="Delete" style="color:#cf6a4c">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
                </button>
              {/if}
            </div>
          {/each}
        {/each}
      </div>
    {:else}
      <div class="flex-1"></div>
    {/if}

    <!-- bottom: connection status -->
    {#if !collapsed}
      <div class="shrink-0 border-t px-3.5 py-3 flex items-center gap-1.5 text-[11px]" style="border-color:#efefec; color:var(--muted)">
        <span class="w-1.5 h-1.5 rounded-full" style="background:{online ? '#5fa463' : '#cf6a4c'}"></span>
        {online ? 'Connected' : 'Offline'}
      </div>
    {/if}
  </aside>
  {/if}

  <main class="flex-1 min-w-0 overflow-hidden" style="background:var(--cream)">
    {@render children()}
  </main>

  </div><!-- /rail+main row -->

  <!-- ===== MOBILE BOTTOM TAB BAR (in-flow, phones only) — admins only;
       normal users have just Chat, so a tab bar is pointless ===== -->
  {#if me && isAdmin}
    <nav class="botbar">
      {#each bottomnav as t}
        {@const on = sectionActive(t.section)}
        <a href={t.href} class="botbar-item" class:on>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d={t.d}/></svg>
          <span>{t.label}</span>
        </a>
      {/each}
    </nav>
  {/if}

  <!-- robot now lives in the header next to the Bell (inline) -->

</div>
{/if}

<style>
  /* floating bell: fixed top-right, above page content, below reader/modals */
  .float-bell{ position:fixed; top:10px; right:16px; z-index:30; }

  /* pull-to-refresh indicator */
  .ptr-ind{ position:fixed; top:10px; left:50%; z-index:90; width:38px; height:38px;
    display:flex; align-items:center; justify-content:center; border-radius:50%;
    background:#fff; border:1px solid var(--border, #e0dfda); color:var(--clay, #c2683f);
    box-shadow:0 4px 14px rgba(40,35,30,.16); pointer-events:none; }

  /* mobile bottom tab bar — in-flow so content shrinks above it (no overlap) */
  .botbar { display: none; }
  @media (max-width: 820px) {
    .topnav-row { display: none; }   /* primary nav moves to the bottom bar */
    .botbar {
      display: flex; flex-shrink: 0; align-items: stretch;
      border-top: 1px solid #efefec; background: var(--sand);
      padding-bottom: env(safe-area-inset-bottom, 0);
    }
    .botbar-item {
      flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
      padding: 7px 0 6px; font-size: 11px; color: #8a857c; text-decoration: none;
    }
    .botbar-item.on { color: var(--clay); font-weight: 600; }
    .botbar-item svg { opacity: .9; }
  }

  /* mobile: chat-history rail becomes a slide-in overlay so the thread gets full width */
  .hdr-burger { display: none; }
  .chat-rail-scrim { display: none; }
  @media (max-width: 820px) {
    .hdr-burger {
      display: inline-flex; align-items: center; justify-content: center;
      width: 34px; height: 34px; border-radius: 9px; margin-right: 2px;
      background: transparent; border: none; color: #46443f; cursor: pointer;
    }
    .chat-rail {
      position: fixed !important; top: 56px; bottom: 0; left: 0;
      width: 250px !important; max-width: 84vw; z-index: 71;
      transform: translateX(-100%); transition: transform .2s ease;
      box-shadow: 2px 0 24px rgba(40,35,30,.16);
    }
    .chat-rail.mopen { transform: none; }
    .chat-rail-scrim {
      display: block; position: fixed; inset: 0; z-index: 70;
      background: rgba(30,28,25,.34); border: none; cursor: default;
    }
  }
</style>
