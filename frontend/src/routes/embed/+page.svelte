<script lang="ts">
  // Standalone embedded chat. Auth via public embed key → short-lived visitor
  // token (in-memory). Talks to the SAME origin it is served from. No member
  // login, no shared localStorage token (kept fully isolated from the app).
  import { onMount } from 'svelte';
  import { md, mdCiteAll } from '$lib/md';

  type Src = { page_id: number; doc: string; page_no: number };
  type Msg = { role: 'user' | 'bot'; text: string; sources?: Src[]; streaming?: boolean };

  let key = $state('');
  let accent = $state('#c2683f');
  let token = $state('');
  let greeting = $state('Hi! Ask me anything about our runbooks & SOPs.');
  let subtitle = $state('online · replies instantly');
  let logo = $state('');
  let title = $state('City Agent Aria');
  let ready = $state(false);
  let err = $state('');
  let convId = $state<number | null>(null);

  let msgs = $state<Msg[]>([]);
  let input = $state('');
  let busy = $state(false);
  let scroller: HTMLDivElement;

  const API = (p: string) => `${location.origin}/api${p}`;

  // Delegated click for inline [N] citation chips → open that source's page
  // image in a new tab (same href the numbered coin uses).
  function onCiteClick(e: MouseEvent, m: Msg) {
    const t = (e.target as HTMLElement)?.closest?.('.cite') as HTMLElement | null;
    if (!t) return;
    e.preventDefault();
    const n = parseInt(t.dataset.n || '', 10);
    const s = m.sources?.[n - 1];        // coin i+1 ↔ sources[i]
    if (s) window.open(`${location.origin}/api/pages/${s.page_id}`, '_blank', 'noopener');
  }

  function vid(): string {
    let v = localStorage.getItem('aria_vid');
    if (!v) { v = 'v_' + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem('aria_vid', v); }
    return v;
  }

  onMount(async () => {
    const q = new URLSearchParams(location.search);
    key = q.get('key') || '';
    accent = q.get('accent') || accent;
    // sandbox mode: a pre-minted token is passed directly → skip the session call
    const passed = q.get('token');
    if (passed) {
      token = passed;
      if (q.get('greeting')) greeting = q.get('greeting')!;
      if (q.get('subtitle')) subtitle = q.get('subtitle')!;
      if (q.get('title')) title = q.get('title')!;
      if (q.get('logo')) logo = q.get('logo')!;
      ready = true;
      return;
    }
    if (!key) { err = 'Missing embed key.'; return; }
    try {
      const r = await fetch(API('/embed/session'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ public_key: key, visitor_id: vid() }),
      });
      if (!r.ok) { err = (await r.json().catch(() => ({}))).detail || 'Could not start chat.'; return; }
      const d = await r.json();
      token = d.token;
      if (d.greeting) greeting = d.greeting;
      if (d.accent) accent = d.accent;
      if (d.subtitle) subtitle = d.subtitle;
      if (d.logo_url) logo = d.logo_url;
      if (d.title) title = d.title;
      ready = true;
    } catch { err = 'Connection failed.'; }
  });

  function scrollDown() { requestAnimationFrame(() => scroller && (scroller.scrollTop = scroller.scrollHeight)); }

  async function send(text?: string) {
    const q = (text ?? input).trim();
    if (!q || busy || !ready) return;
    input = '';
    msgs = [...msgs, { role: 'user', text: q }, { role: 'bot', text: '', streaming: true }];
    // IMPORTANT (Svelte 5): mutate through the $state array index so writes go
    // through the proxy and trigger re-render. Holding a raw object ref and
    // mutating it does NOT notify — that left the widget stuck on an empty caret.
    const bi = msgs.length - 1;
    busy = true; scrollDown();
    try {
      const r = await fetch(API('/ask/stream'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ q, conversation_id: convId, mode: 'quick' }),
      });
      if (!r.ok || !r.body) { msgs[bi].text = 'Sorry, something went wrong.'; msgs[bi].streaming = false; return; }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const line of lines) {
          if (!line.trim()) continue;
          let ev: any; try { ev = JSON.parse(line); } catch { continue; }
          if (ev.type === 'meta') convId = ev.conversation_id;
          else if (ev.type === 'token') { msgs[bi].text += ev.v; scrollDown(); }
          else if (ev.type === 'done') {
            msgs[bi].streaming = false;
            // use the server's cleaned text (PAGES line stripped); fallback: strip it ourselves
            if (typeof ev.clean === 'string' && ev.clean) msgs[bi].text = ev.clean;
            else msgs[bi].text = msgs[bi].text.replace(/\n*PAGES:.*$/is, '').trimEnd();
            msgs[bi].sources = (ev.pages || []).map((p: any) => ({ page_id: p.page_id ?? p.id, doc: p.doc_name || p.doc || '', page_no: p.page_no }));
            scrollDown();
          } else if (ev.type === 'error') { msgs[bi].text += `\n[${ev.detail}]`; msgs[bi].streaming = false; }
        }
      }
    } catch { if (!msgs[bi].text) msgs[bi].text = 'Connection lost.'; msgs[bi].streaming = false; }
    finally { busy = false; msgs[bi].streaming = false; scrollDown(); }
  }

  function onKey(e: KeyboardEvent) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }
  function close() { parent.postMessage('aria:close', '*'); }
</script>

<div class="wrap" style="--accent:{accent}">
  <header>
    <div class="brand">
      {#if logo}<img class="hlogo" src={logo} alt="" />{:else}<span class="dot"></span>{/if}
      <span class="htxt"><span class="htitle">{title}</span><span class="hsub">{subtitle}</span></span>
    </div>
    <button class="x" onclick={close} aria-label="Close">✕</button>
  </header>

  <div class="body" bind:this={scroller}>
    {#if err}
      <div class="err">{err}</div>
    {:else if msgs.length === 0}
      <div class="hello">{greeting}</div>
    {/if}
    {#each msgs as m}
      <div class="row {m.role}">
        <div class="bubble">
          {#if m.role === 'bot'}<span class="md" role="presentation" onclick={(e) => onCiteClick(e, m)}>{@html mdCiteAll(m.text)}</span>{:else}{m.text}{/if}{#if m.streaming}<span class="caret">▋</span>{/if}
          {#if m.sources && m.sources.length}
            <div class="srcwrap">
              <span class="srclbl">Sources</span>
              <div class="srcs">
                {#each m.sources.slice(0, 8) as s, i}
                  <a class="coin" href={`${location.origin}/api/pages/${s.page_id}`} target="_blank" rel="noopener" title={`Open ${s.doc} · page ${s.page_no}`}>{i + 1}</a>
                {/each}
              </div>
            </div>
          {/if}
        </div>
      </div>
    {/each}
  </div>

  <div class="composer">
    <input placeholder="Ask a question…" bind:value={input} onkeydown={onKey} disabled={!ready || !!err} />
    <button class="send" onclick={() => send()} disabled={!ready || busy || !input.trim()} aria-label="Send">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
    </button>
  </div>
  <div class="foot">Powered by City Agent Aria</div>
</div>

<style>
  :global(html, body) { margin: 0; height: 100%; background: #faf9f5; }
  .wrap { display: flex; flex-direction: column; height: 100vh; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #2b2a27; background: #faf9f5; }
  header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; background: var(--accent); color: #fff; }
  .brand { display: flex; align-items: center; gap: 9px; }
  .htxt { display: flex; flex-direction: column; line-height: 1.2; }
  .htitle { font-weight: 600; font-size: 14px; }
  .hsub { font-size: 10.5px; opacity: .85; }
  .hlogo { width: 26px; height: 26px; border-radius: 50%; object-fit: cover; background: #fff; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #fff; box-shadow: 0 0 0 3px rgba(255,255,255,.3); }
  .x { background: transparent; border: none; color: #fff; font-size: 16px; cursor: pointer; opacity: .85; }
  .x:hover { opacity: 1; }
  .body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
  .hello { color: #6b6a66; font-size: 14px; line-height: 1.5; padding: 8px 4px; }
  .err { color: #c0492f; font-size: 13px; padding: 10px; background: #fbeae6; border-radius: 10px; }
  .row { display: flex; }
  .row.user { justify-content: flex-end; }
  .bubble { max-width: 84%; padding: 9px 12px; border-radius: 14px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .user .bubble { background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
  .bot .bubble { background: #fff; border: 1px solid #ece9e2; border-bottom-left-radius: 4px; white-space: normal; }
  /* markdown inside bot answers */
  .md :global(p) { margin: 0 0 8px; }
  .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(ul), .md :global(ol) { margin: 4px 0 8px; padding-left: 20px; }
  .md :global(li) { margin: 2px 0; }
  .md :global(strong) { font-weight: 600; }
  .md :global(code) { background: #f1ede4; padding: 1px 5px; border-radius: 5px; font-size: 12.5px; }
  .md :global(pre) { background: #f6f3ec; padding: 8px 10px; border-radius: 8px; overflow-x: auto; font-size: 12px; }
  .md :global(table) { border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 6px 0; }
  .md :global(th), .md :global(td) { border: 1px solid #e6e1d6; padding: 4px 7px; text-align: left; }
  .md :global(th) { background: #f6f3ec; font-weight: 600; }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-size: 14px; font-weight: 600; margin: 8px 0 4px; }
  .md :global(a) { color: var(--accent); }
  .caret { animation: blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .srcwrap { margin-top: 10px; padding-top: 9px; border-top: 1px solid #efece4; }
  .srclbl { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #9a948a; font-weight: 600; margin-bottom: 6px; }
  .srcs { display: flex; flex-wrap: wrap; gap: 6px; }
  .coin { width: 24px; height: 24px; border-radius: 7px; background: #f1ede4; color: var(--accent); font-size: 11.5px; font-weight: 700; display: flex; align-items: center; justify-content: center; text-decoration: none; border: 1px solid #e6e1d6; cursor: pointer; transition: background .14s, color .14s, transform .12s; }
  .coin:hover { background: var(--accent); color: #fff; transform: translateY(-1px); }
  /* inline [N] citation markers → small accent superscript chips */
  .md :global(sup.cite) { display: inline-flex; align-items: center; justify-content: center; vertical-align: super; margin: 0 1px; min-width: 14px; height: 14px; padding: 0 4px; font-size: 10px; line-height: 1; border-radius: 999px; background: #f1ede4; color: var(--accent); font-weight: 700; cursor: pointer; border: 1px solid #e6e1d6; transition: background .12s, color .12s; }
  .md :global(sup.cite:hover) { background: var(--accent); color: #fff; }
  .composer { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid #ece9e2; background: #faf9f5; }
  .composer input { flex: 1; border: 1px solid #ddd8cd; border-radius: 22px; padding: 10px 14px; font-size: 14px; outline: none; background: #fff; }
  .composer input:focus { border-color: var(--accent); }
  .send { width: 40px; height: 40px; border-radius: 50%; border: none; background: var(--accent); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex: 0 0 auto; }
  .send:disabled { opacity: .45; cursor: default; }
  .foot { text-align: center; font-size: 10px; color: #a8a59d; padding: 0 0 7px; }
</style>
