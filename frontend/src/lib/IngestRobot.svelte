<script lang="ts">
  // Live activity robot — floating bottom-right bubble that expands into a
  // dark terminal-style log streaming ingest/learning events.
  // Mirrors Bell.svelte for auth-header + polling style.

  type Line = {
    id: number;
    doc_id?: number | null;
    doc_name?: string | null;
    ts?: string;
    step?: string;
    msg?: string;
    level?: 'info' | 'error' | string;
  };

  // inline = render as a header icon button + dropdown popover (next to the Bell)
  let { inline = false } = $props<{ inline?: boolean }>();

  let open = $state(false);
  let lines = $state<Line[]>([]);
  let cursor = $state(0);
  let active = $state<{ id?: number; name?: string; status?: string } | null>(null);
  let lastActivityAt = $state(0); // ms timestamp of last new line seen
  let healthy = $state(true); // false if endpoint errors / 404s
  let scrollEl: HTMLDivElement | null = null;

  const TOKEN = 'docsensei_token';
  function authHeaders(): Record<string, string> {
    const h: Record<string, string> = {};
    try {
      const t = localStorage.getItem(TOKEN);
      if (t) h['Authorization'] = `Bearer ${t}`;
    } catch { /* ignore */ }
    return h;
  }

  // api.base is relative ('/api') in the built bundle → ALWAYS build against origin.
  function url(path: string, params?: Record<string, string | number>): string {
    const u = new URL('/api' + path, location.origin);
    if (params) for (const k in params) u.searchParams.set(k, String(params[k]));
    return u.toString();
  }

  function isProcessing(): boolean {
    if (active && (active.status === 'processing' || active.status === 'queued')) return true;
    // recent line within last 8s also counts as "live"
    return lastActivityAt > 0 && Date.now() - lastActivityAt < 8000;
  }

  async function pollLog() {
    try {
      const r = await fetch(url('/ingest/log', { after: cursor, limit: 60 }), { headers: authHeaders() });
      if (!r.ok) { healthy = false; return; }
      const d = await r.json().catch(() => null);
      if (!d || !Array.isArray(d.lines)) { healthy = false; return; }
      healthy = true;
      if (d.lines.length) {
        const merged = lines.concat(d.lines as Line[]);
        // cap buffer at last 200 lines
        lines = merged.length > 200 ? merged.slice(merged.length - 200) : merged;
        lastActivityAt = Date.now();
      }
      if (typeof d.cursor === 'number') cursor = d.cursor;
    } catch {
      healthy = false;
    }
  }

  async function pollActive() {
    try {
      const r = await fetch(url('/ingest/active'), { headers: authHeaders() });
      if (!r.ok) { active = null; return; }
      const d = await r.json().catch(() => null);
      if (d && (d.id || d.name || d.status)) {
        active = { id: d.id, name: d.name, status: d.status };
      } else {
        active = null;
      }
    } catch {
      active = null;
    }
  }

  async function tick() {
    await pollLog();
    await pollActive();
  }

  // Poll loop: fast (2.5s) while expanded, slow (6s) while collapsed.
  // Re-arms the interval when `open` changes (effect re-runs).
  $effect(() => {
    const period = open ? 2500 : 6000;
    tick();
    const iv = setInterval(tick, period);
    return () => clearInterval(iv);
  });

  // auto-scroll to bottom on new lines while expanded
  $effect(() => {
    lines.length; // track
    if (open && scrollEl) {
      requestAnimationFrame(() => { if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight; });
    }
  });

  function hhmmss(ts?: string): string {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  function lineColor(l: Line): string {
    const lvl = (l.level || '').toLowerCase();
    const step = (l.step || '').toLowerCase();
    const msg = (l.msg || '').toLowerCase();
    if (lvl === 'error') return '#e06c6c';
    if (step.includes('ready') || step.includes('done') || msg.includes('✓') || msg.includes('ready') || msg.includes('complete')) return '#5fa463';
    if (step.includes('learn') || step.includes('digest') || step.includes('memory')) return '#7ea2cc';
    if (step.includes('page') || step.includes('read') || step.includes('vision')) return '#9a978f';
    return '#cfcdc6';
  }

  let processing = $derived(isProcessing());
  let statusText = $derived(
    !healthy ? 'idle'
      : processing
        ? `● processing${active?.name ? ' ' + active.name : ''}`
        : 'watching'
  );
</script>

<!-- shared dark terminal panel body -->
{#snippet panelBody()}
  <div class="rp-head">
    <span class="rp-title">ACTIVITY</span>
    <span class="rp-status" class:on={processing} class:dim={!healthy}>{statusText}</span>
    <div class="rp-spacer"></div>
    <button class="rp-x" onclick={() => (open = false)} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </div>
  <div class="rp-log" bind:this={scrollEl}>
    {#if lines.length === 0}
      <div class="rp-empty">No activity yet — upload a document to watch it get ingested.</div>
    {:else}
      {#each lines as l (l.id)}
        <div class="rp-line" style="color:{lineColor(l)}">
          <span class="rp-ts">{hhmmss(l.ts)}</span>
          {#if l.doc_name}<span class="rp-doc">[{l.doc_name}]</span>{/if}
          <span class="rp-msg">{l.msg || l.step || ''}</span>
        </div>
      {/each}
    {/if}
  </div>
{/snippet}

{#if inline}
  <!-- header icon button + dropdown popover (next to the Bell) -->
  <div class="robot-wrap">
    <button class="robot-ico" class:open onclick={() => (open = !open)} aria-label="Activity" title="Activity">
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="8" width="16" height="11" rx="3"/>
        <path d="M12 8V5"/><circle cx="12" cy="4" r="1.3" fill="currentColor" stroke="none"/>
        <circle cx="9" cy="13" r="1.1" fill="currentColor" stroke="none"/>
        <circle cx="15" cy="13" r="1.1" fill="currentColor" stroke="none"/>
        <path d="M9.5 16.2h5"/><path d="M2 12v3M22 12v3"/>
      </svg>
      {#if processing}<span class="ico-dot"></span>{/if}
    </button>
    {#if open}
      <button class="robot-scrim" aria-label="Close activity" onclick={() => (open = false)}></button>
      <div class="robot-pop" role="dialog" aria-label="Activity log">
        {@render panelBody()}
      </div>
    {/if}
  </div>
{:else}
  <!-- legacy floating bottom-right bubble -->
  {#if !open}
    <button class="robot-bubble" onclick={() => (open = true)} aria-label="Open activity log" title="Activity">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="8" width="16" height="11" rx="3"/>
        <path d="M12 8V5"/><circle cx="12" cy="4" r="1.4" fill="#fff" stroke="none"/>
        <circle cx="9" cy="13" r="1.2" fill="#fff" stroke="none"/>
        <circle cx="15" cy="13" r="1.2" fill="#fff" stroke="none"/>
        <path d="M9.5 16.2h5"/><path d="M2 12v3M22 12v3"/>
      </svg>
      {#if processing}<span class="robot-pulse"></span>{/if}
    </button>
  {/if}
  {#if open}
    <div class="robot-panel" role="dialog" aria-label="Activity log">{@render panelBody()}</div>
  {/if}
{/if}

<style>
  /* ── inline header mode (next to Bell) ── */
  .robot-wrap { position: relative; display: inline-flex; }
  .robot-ico {
    width: 34px; height: 34px; border-radius: 9px; border: 0; cursor: pointer;
    background: transparent; color: var(--ink, #1a1a18);
    display: inline-flex; align-items: center; justify-content: center; position: relative;
    transition: background .14s;
  }
  .robot-ico:hover, .robot-ico.open { background: var(--hover, #efefec); }
  .ico-dot {
    position: absolute; top: 6px; right: 6px; width: 7px; height: 7px; border-radius: 50%;
    background: #3f8f5f; border: 1.5px solid var(--sand, #f9f9f8);
    animation: robot-ping 1.6s ease-out infinite;
  }
  .robot-scrim { position: fixed; inset: 0; z-index: 44; background: transparent; border: 0; cursor: default; }
  .robot-pop {
    position: absolute; top: calc(100% + 8px); right: 0; z-index: 45;
    width: 360px; max-width: 92vw; height: 420px; max-height: 70vh;
    background: #161616; border: 1px solid #2a2a2a; border-radius: 12px;
    display: flex; flex-direction: column; overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    animation: rp-drop .16s ease;
  }
  @keyframes rp-drop { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }

  /* Collapsed bubble — bottom-right, above content, below reader(z40)/modals(z55) */
  .robot-bubble {
    position: fixed; bottom: 18px; right: 18px; z-index: 35;
    width: 50px; height: 50px; border-radius: 999px; border: 0; cursor: pointer;
    background: var(--clay, #c2683f); color: #fff;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 6px 18px rgba(33, 31, 28, .24);
    animation: robot-bob 3.2s ease-in-out infinite;
  }
  .robot-bubble:hover { filter: brightness(1.06); }
  @keyframes robot-bob {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-4px); }
  }
  .robot-pulse {
    position: absolute; top: 4px; right: 4px; width: 11px; height: 11px;
    border-radius: 999px; background: #5fa463; border: 2px solid #fff;
    animation: robot-ping 1.4s ease-in-out infinite;
  }
  @keyframes robot-ping {
    0% { box-shadow: 0 0 0 0 rgba(95, 164, 99, .6); }
    70% { box-shadow: 0 0 0 7px rgba(95, 164, 99, 0); }
    100% { box-shadow: 0 0 0 0 rgba(95, 164, 99, 0); }
  }

  /* Expanded terminal panel — anchored bottom-right above the bubble */
  .robot-panel {
    position: fixed; bottom: 18px; right: 18px; z-index: 35;
    width: 360px; max-width: 92vw; height: 420px; max-height: 78vh;
    background: #161616; border: 1px solid #2a2a2a; border-radius: 12px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, .42);
    display: flex; flex-direction: column; overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .rp-head {
    display: flex; align-items: center; gap: 8px;
    padding: 9px 11px; background: #1a1a1a; border-bottom: 1px solid #2a2a2a;
    flex-shrink: 0;
  }
  .rp-bot { font-size: 14px; line-height: 1; }
  .rp-title { font-size: 11.5px; font-weight: 700; letter-spacing: .04em; color: #d97757; }
  .rp-status { font-size: 11px; color: #8b8b8b; }
  .rp-status.on { color: #5fa463; }
  .rp-status.dim { color: #6b6b6b; }
  .rp-spacer { flex: 1; }
  .rp-x {
    background: none; border: 0; cursor: pointer; color: #9b9b9b;
    font-size: 12px; padding: 3px 6px; border-radius: 6px; line-height: 1;
  }
  .rp-x:hover { background: #2a2a2a; color: #fff; }

  .rp-log {
    flex: 1; min-height: 0; overflow-y: auto;
    padding: 8px 10px; font-size: 11.5px; line-height: 1.5;
  }
  .rp-empty { color: #7a7a7a; font-size: 11.5px; padding: 18px 4px; line-height: 1.6; }
  .rp-line { white-space: pre-wrap; word-break: break-word; margin-bottom: 1px; }
  .rp-ts { color: #5e5e5e; margin-right: 7px; }
  .rp-doc { color: #7ea2cc; margin-right: 6px; }
  .rp-msg { }

  .rp-log::-webkit-scrollbar { width: 8px; }
  .rp-log::-webkit-scrollbar-thumb { background: #333; border-radius: 8px; }
  .rp-log::-webkit-scrollbar-track { background: transparent; }
</style>
