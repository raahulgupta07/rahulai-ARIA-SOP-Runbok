<script lang="ts">
  import { api } from '$lib/api';
  import WhatsNew from '$lib/WhatsNew.svelte';

  let open = $state(false);
  let tab = $state<'activity' | 'whatsnew'>('activity');
  let filter = $state<'all' | 'unread' | 'alerts'>('all');
  let unread = $state(0);
  let items = $state<any[]>([]);
  let loading = $state(false);

  async function refreshUnread() {
    try {
      const d = await api.notifications('unread');
      unread = d.unread ?? (d.items?.length ?? 0);
    } catch { /* ignore */ }
  }

  async function loadList() {
    loading = true;
    try {
      const d = await api.notifications(filter);
      items = d.items ?? [];
      if (typeof d.unread === 'number') unread = d.unread;
    } catch { items = []; } finally { loading = false; }
  }

  async function markAll() {
    try { await api.markAllRead(); } catch { /* ignore */ }
    unread = 0;
    await loadList();
  }

  // poll unread every 30s + fetch once on mount
  $effect(() => {
    refreshUnread();
    const iv = setInterval(refreshUnread, 30000);
    return () => clearInterval(iv);
  });

  // load list when panel opens or filter/tab changes
  $effect(() => {
    if (open && tab === 'activity') {
      // reference filter so effect re-runs on change
      filter;
      loadList();
    }
  });

  function setFilter(f: 'all' | 'unread' | 'alerts') { filter = f; }

  function ago(ts: string) {
    if (!ts) return '';
    const then = new Date(ts).getTime();
    if (isNaN(then)) return '';
    const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (s < 45) return 'just now';
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  }

  function icon(level: string) {
    if (level === 'success') return { ch: '✓', col: '#5fa463' };
    if (level === 'alert') return { ch: '⚠', col: '#cf6a4c' };
    if (level === 'warn') return { ch: '⚠', col: '#c79a3a' };
    return { ch: '•', col: '#6f6c65' };
  }
</script>

<div class="bell-wrap">
  <button class="bell-btn" onclick={() => (open = !open)} aria-label="Notifications">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#46443f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>
    </svg>
    {#if unread > 0}
      <span class="bell-badge">{unread > 99 ? '99+' : unread}</span>
    {/if}
  </button>

  {#if open}
    <!-- backdrop click-catcher -->
    <div class="bell-back" role="presentation" onclick={() => (open = false)}></div>

    <div class="bell-panel" role="presentation">
      <div class="bp-tabs">
        <button class="bp-tab {tab === 'activity' ? 'on' : ''}" onclick={() => (tab = 'activity')}>
          Activity{#if unread > 0}<span class="bp-cnt">{unread}</span>{/if}
        </button>
        <button class="bp-tab {tab === 'whatsnew' ? 'on' : ''}" onclick={() => (tab = 'whatsnew')}>
          What's new
        </button>
        <div class="bp-spacer"></div>
        <button class="bp-x" onclick={() => (open = false)} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>

      {#if tab === 'activity'}
        <div class="bp-top">
          <span class="bp-live"><span class="bp-livedot"></span>live · {unread} unread</span>
          <button class="bp-mark" onclick={markAll}>Mark all read</button>
        </div>

        <div class="bp-chips">
          <button class="bp-chip {filter === 'all' ? 'on' : ''}" onclick={() => setFilter('all')}>All</button>
          <button class="bp-chip {filter === 'unread' ? 'on' : ''}" onclick={() => setFilter('unread')}>Unread</button>
          <button class="bp-chip {filter === 'alerts' ? 'on' : ''}" onclick={() => setFilter('alerts')}>Alerts</button>
        </div>

        <div class="bp-list">
          {#if loading}
            <div class="bp-empty">Loading…</div>
          {:else if items.length === 0}
            <div class="bp-empty">No activity yet.</div>
          {:else}
            {#each items as it}
              {@const ic = icon(it.level)}
              <div class="bp-item">
                <span class="bp-ic" style="color:{ic.col}">{ic.ch}</span>
                <div class="bp-body">
                  <div class="bp-title">{it.title}</div>
                  {#if it.body}<div class="bp-sub">{it.body}</div>{/if}
                </div>
                <div class="bp-right">
                  <span class="bp-time">{ago(it.created_at)}</span>
                  {#if !it.read}<span class="bp-unread"></span>{/if}
                </div>
              </div>
            {/each}
          {/if}
        </div>
      {:else}
        <div class="bp-wn">
          <WhatsNew />
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .bell-wrap { position:relative; display:flex; align-items:center; }
  .bell-btn { position:relative; width:34px; height:34px; border-radius:9px; border:0; background:transparent;
    display:flex; align-items:center; justify-content:center; cursor:pointer; }
  .bell-btn:hover { background:#ece9e0; }
  .bell-badge { position:absolute; top:-1px; right:-1px; min-width:16px; height:16px; padding:0 4px;
    border-radius:999px; background:var(--clay); color:#fff; font-size:10px; font-weight:700;
    display:flex; align-items:center; justify-content:center; line-height:1; }

  .bell-back { position:fixed; inset:0; z-index:60; }
  .bell-panel { position:absolute; right:0; top:calc(100% + 8px); width:380px; max-width:92vw; z-index:70;
    background:#fff; border:1px solid #E7E3D8; border-radius:14px;
    box-shadow:0 12px 34px rgba(33,31,28,.16); overflow:hidden;
    /* --clay inherits the runtime-injected brand accent (no local override) */
    --muted:#73706A; --ink:#211F1C; }

  .bp-tabs { display:flex; align-items:center; gap:4px; padding:9px 10px; border-bottom:1px solid #ECE8DD; }
  .bp-tab { background:none; border:0; cursor:pointer; font-size:13px; font-weight:500; color:#46443f;
    padding:5px 11px; border-radius:9px; display:flex; align-items:center; gap:6px; }
  .bp-tab.on { background:color-mix(in srgb, var(--clay) 12%, #fff); color:var(--clay); }
  .bp-cnt { font-size:10.5px; font-weight:700; background:var(--clay); color:#fff;
    border-radius:999px; min-width:15px; height:15px; padding:0 4px; display:flex; align-items:center; justify-content:center; }
  .bp-spacer { flex:1; }
  .bp-x { background:none; border:0; cursor:pointer; color:var(--muted); font-size:13px; padding:4px 7px; border-radius:7px; }
  .bp-x:hover { background:#ece9e0; }

  .bp-top { display:flex; align-items:center; justify-content:space-between; padding:9px 13px 3px; }
  .bp-live { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }
  .bp-livedot { width:6px; height:6px; border-radius:999px; background:#5fa463; }
  .bp-mark { background:none; border:0; cursor:pointer; color:var(--clay); font-size:12px; font-weight:500; }

  .bp-chips { display:flex; gap:6px; padding:6px 13px 10px; }
  .bp-chip { background:#fff; border:1px solid #E7E3D8; border-radius:999px; cursor:pointer;
    font-size:11.5px; color:#46443f; padding:3px 11px; }
  .bp-chip.on { background:color-mix(in srgb, var(--clay) 12%, #fff); color:var(--clay); border-color:#dcdcd8; }

  .bp-list { max-height:360px; overflow-y:auto; padding:2px 6px 8px; }
  .bp-empty { text-align:center; color:var(--muted); font-size:12.5px; padding:26px 0; }
  .bp-item { display:flex; gap:10px; padding:9px 8px; border-radius:10px; }
  .bp-item:hover { background:#FAF9F5; }
  .bp-ic { font-size:14px; line-height:1.4; width:16px; text-align:center; flex-shrink:0; }
  .bp-body { flex:1; min-width:0; }
  .bp-title { font-size:13px; font-weight:600; color:var(--ink); }
  .bp-sub { font-size:12px; color:var(--muted); line-height:1.4; margin-top:1px; }
  .bp-right { display:flex; flex-direction:column; align-items:flex-end; gap:5px; flex-shrink:0; }
  .bp-time { font-size:11px; color:var(--muted); white-space:nowrap; }
  .bp-unread { width:7px; height:7px; border-radius:999px; background:var(--clay); }

  .bp-wn { padding:14px 14px 16px; max-height:420px; overflow-y:auto; }
</style>
