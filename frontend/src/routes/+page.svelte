<script lang="ts">
  import { api } from '$lib/api';
  import { goto } from '$app/navigation';
  import { md, mdCited, mdCiteAll } from '$lib/md';
  import { auth } from '$lib/auth';
  import Lightbox from '$lib/Lightbox.svelte';
  import Burst from '$lib/Burst.svelte';
  import BrainGraph from '$lib/BrainGraph.svelte';
  import { pickNode } from '$lib/dashutil';
  import { parseDocName } from '$lib/docname';
  import { convs, activeConvId, newChatSignal, reloadConvs } from '$lib/chatstore';

  let me = $state<any>(null);
  $effect(() => { auth.me().then((u) => (me = u)).catch(() => {}); });
  let firstName = $derived(me?.name ? String(me.name).split(' ')[0] : '');

  type Src = { page_id: number; doc_id?: number; doc_name: string; page_no: number; image_url: string; has_image?: boolean };
  type Step = { label: string; detail?: string; state?: string };
  type Msg = { role: 'user' | 'bot'; text: string; pages?: Src[]; loading?: boolean; follows?: string[]; steps?: Step[]; thinking?: boolean; stepsOpen?: boolean; stopped?: boolean; vote?: 'up' | 'down'; copied?: boolean; shared?: boolean; streaming?: boolean; t0?: number; thoughtMs?: number; q?: string; blind?: boolean; nearest?: string | null; related?: any[]; messageId?: number; tokens?: { in: number; out: number; total: number }; cost?: number; grounded?: boolean; citedN?: number; accuracy?: number | null; checking?: boolean; servedQaId?: number | null };
  type Conv = { id: number; title: string; updated_at: string };

  let activeId = $state<number | null>(null);
  let messages = $state<Msg[]>([]);
  let input = $state('');
  let busy = $state(false);
  let controller: AbortController | null = $state(null);
  let zoom = $state('');
  let zoomCap = $state('');
  let mode = $state<'auto' | 'quick' | 'deep'>(
    (typeof localStorage !== 'undefined' && (localStorage.getItem('docsensei_mode') as 'auto' | 'quick' | 'deep')) || 'auto'
  );
  function setMode(m: 'auto' | 'quick' | 'deep') {
    mode = m;
    try { localStorage.setItem('docsensei_mode', m); } catch {}
  }

  let scroller: HTMLDivElement;
  function scrollDown() {
    requestAnimationFrame(() => scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }));
  }

  $effect(() => { reloadConvs(); });

  // ===== rail ↔ thread sync (the rail lives in the layout now) =====
  // open a conversation when the rail selects one
  $effect(() => {
    const id = $activeConvId;
    if (id != null && id !== activeId) openConv(id);
  });
  // clear to a fresh thread when the rail hits "New chat"
  let lastNew = 0;
  $effect(() => {
    const s = $newChatSignal;
    if (s !== lastNew) { lastNew = s; activeId = null; messages = []; input = ''; busy = false; }
  });

  // Prefill the composer from ?ask= (e.g. "Ask about this doc" deep-link). Do NOT auto-send.
  $effect(() => {
    if (typeof location === 'undefined') return;
    const ask = new URLSearchParams(location.search).get('ask');
    if (ask) {
      input = ask;
      try { history.replaceState(null, '', '/'); } catch {}
    }
  });

  async function openConv(id: number) {
    activeId = id;
    activeConvId.set(id);
    try {
      const r = await api.getConversation(id);
      messages = (r.messages || []).map((m: any) => ({ role: m.role, text: m.text, pages: m.pages || [] }));
    } catch { messages = []; }
    scrollDown();
  }

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    input = '';
    messages.forEach((m) => (m.follows = undefined));  // only newest answer shows follow-ups
    messages.push({ role: 'user', text: q });
    messages.push({ role: 'bot', text: '', loading: true, thinking: true, steps: [], streaming: true, t0: Date.now(), q });
    busy = true;
    controller = new AbortController();
    scrollDown();
    const wasNew = activeId === null;
    const idx = messages.length - 1;

    await api.askStream(
      q,
      activeId,
      mode,
      // onMeta — gives the conversation id for brand-new chats
      (obj) => { if (obj.conversation_id && activeId === null) { activeId = obj.conversation_id; activeConvId.set(activeId); } },
      // onToken — append partial text, drop the loader on first token
      (v) => {
        const m = messages[idx];
        m.loading = false;
        if (m.thinking) { m.thinking = false; m.steps?.forEach((s) => (s.state = 'done')); }  // collapse trace when answer starts
        m.text += v;
        messages = messages; // trigger reactivity
        scrollDown();
      },
      // onDone — replace with cleaned text (PAGES line stripped) + source pages
      async (obj) => {
        const m = messages[idx];
        m.loading = false;
        m.streaming = false;
        if (m.t0) m.thoughtMs = Date.now() - m.t0;
        if (typeof obj.clean === 'string') m.text = obj.clean;
        m.pages = obj.pages || [];
        m.blind = !!obj.blind;
        m.nearest = obj.nearest || null;
        m.messageId = obj.message_id;
        m.tokens = obj.tokens;
        m.cost = obj.cost;
        m.grounded = !!obj.grounded;
        m.servedQaId = obj.served_qa_id ?? null;   // set when answer came from Q&A cache
        m.citedN = obj.cited_n ?? (obj.pages || []).length;
        messages = messages;
        // accuracy is on-demand: user clicks the ✦ check-accuracy button (saves an LLM call/answer)
        if (obj.conversation_id && activeId === null) { activeId = obj.conversation_id; activeConvId.set(activeId); }
        // follow-up chips come folded into the answer stream now (no extra LLM call) → instant
        if (obj.followups?.length) { m.follows = obj.followups; messages = messages; }
        // graph-grounded "related runbooks" from the cited pages' neighbors
        const pids = (m.pages || []).map((p: any) => p.page_id).filter(Boolean);
        if (pids.length) api.answerRelated(pids).then((r: any) => { if (r.related?.length) { m.related = r.related; messages = messages; } }).catch(() => {});
        await reloadConvs();   // refresh the rail (new conv / new title / reorder)
        busy = false;
        scrollDown();
      },
      // onError
      (detail) => {
        const m = messages[idx];
        m.loading = false;
        m.streaming = false;
        m.text = detail === 'unauthorized'
          ? 'Unauthorized — please sign in again.'
          : (detail || 'error');
        messages = messages;
        busy = false;
        scrollDown();
      },
      // onStep — live "thinking" trace of what the agent is doing
      (obj) => {
        const m = messages[idx];
        // blind-spot self-correction: server is re-answering with a wider search →
        // wipe the ungrounded first attempt so attempt-2 streams fresh
        if (obj.reset) { m.text = ''; m.thinking = true; }
        m.steps = m.steps || [];
        m.steps.forEach((s) => { if (s.state === 'running') s.state = 'done'; });
        m.steps.push({ label: obj.label, detail: obj.detail, state: obj.state || 'done' });
        messages = messages;
        scrollDown();
      },
      controller.signal
    );
  }

  function stop() {
    controller?.abort();
    controller = null;
    const m = messages.find((x) => x.role === 'bot' && (x.loading || x.thinking || x.streaming));
    if (m) {
      m.loading = false;
      m.thinking = false;
      m.streaming = false;
      m.steps?.forEach((s) => (s.state = 'done'));
      m.stopped = true;
    }
    busy = false;
    messages = messages;
  }

  function openZoom(p: Src) { zoom = api.pageImg(p.page_id); zoomCap = `${p.doc_name} — page ${p.page_no}`; }

  // ===== Perplexity-style source drawer (right slide-over) =====
  let src = $state<{ list: Src[]; idx: number } | null>(null);
  let srcCur = $derived(src ? src.list[src.idx] : null);
  let srcView = $state<'image' | 'text'>('image');     // page image vs compiled markdown
  let srcZoom = $state(100);                            // % width (100 = fit width)
  let srcMd = $state<{ md: string; compiled: boolean; raw: string } | null>(null);
  let srcMdBusy = $state(false);
  function openSrc(pages: Src[] | undefined, n: number) {
    if (!pages || !pages.length) return;
    let i = pages.findIndex((p) => p.page_no === n);
    if (i < 0) i = 0;
    src = { list: pages, idx: i };
    srcZoom = 100; srcMd = null;
    // imported (markdown-only) pages have no image → open straight to Text
    srcView = pages[i]?.has_image === false ? 'text' : 'image';
  }
  function closeSrc() { src = null; }
  function srcGo(i: number) { if (src) { src = { ...src, idx: i }; srcZoom = 100; srcMd = null; } }
  function srcPrev() { if (src && src.idx > 0) srcGo(src.idx - 1); }
  function srcNext() { if (src && src.idx < src.list.length - 1) srcGo(src.idx + 1); }
  function srcDocId(p: any): number | null { return p && typeof p.doc_id === 'number' ? p.doc_id : null; }
  function openInBrain(p: any) { const id = srcDocId(p); if (id) goto(`/brain?doc=${id}&pg=${p.page_no}`); }
  function srcZoomIn() { srcZoom = Math.min(300, srcZoom + 25); }
  function srcZoomOut() { srcZoom = Math.max(50, srcZoom - 25); }
  function srcFit() { srcZoom = 100; }
  // load compiled markdown when Text view is shown (per page)
  $effect(() => {
    if (src && srcView === 'text' && srcCur && !srcMd && !srcMdBusy) {
      const pid = srcCur.page_id;
      srcMdBusy = true;
      api.pageMd(pid).then((r) => { srcMd = r; }).catch(() => { srcMd = { md: '', compiled: false, raw: '' }; }).finally(() => { srcMdBusy = false; });
    }
  });
  function onSrcKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && downModal) { e.preventDefault(); cancelDownvote(); return; }
    if (e.key === 'Escape' && sourcesDrawer && !src) { e.preventDefault(); closeSourcesSummary(); return; }
    if (!src) return;
    if (e.key === 'Escape') { e.preventDefault(); closeSrc(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); srcNext(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); srcPrev(); }
    else if (srcView === 'image' && (e.key === '+' || e.key === '=')) { e.preventDefault(); srcZoomIn(); }
    else if (srcView === 'image' && e.key === '-') { e.preventDefault(); srcZoomOut(); }
    else if (srcView === 'image' && e.key === '0') { e.preventDefault(); srcFit(); }
  }

  async function copyAnswer(m: Msg) {
    try { await navigator.clipboard.writeText(m.text); } catch {}
    m.copied = true;
    messages = messages;
    setTimeout(() => { m.copied = false; messages = messages; }, 1500);
  }

  async function shareAnswer(m: Msg) {
    if (!m.messageId) return;
    try {
      const { token } = await api.shareAnswer(m.messageId);
      const url = location.origin + '/s/' + token;
      try { await navigator.clipboard.writeText(url); } catch {}
      m.shared = true; messages = messages;
      setTimeout(() => { m.shared = false; messages = messages; }, 2500);
    } catch (e: any) { alert(e?.message || 'Could not create share link'); }
  }

  // the user question that produced bot message `m` (the user turn right before it)
  function questionFor(m: Msg): string {
    const i = messages.indexOf(m);
    for (let j = i - 1; j >= 0; j--) if (messages[j].role === 'user') return messages[j].text;
    return '';
  }

  // direct authed fetch (mirrors api.ts: BASE = origin + /api, Bearer docsensei_token)
  function authHeaders(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    try { const t = localStorage.getItem('docsensei_token'); if (t) h['Authorization'] = `Bearer ${t}`; } catch {}
    return h;
  }

  // 👍 = one-click positive; 👎 = open capture popup
  function voteAnswer(m: Msg, v: 'up' | 'down') {
    if (v === 'down') { downModal = m; downNote = ''; downCorrection = ''; return; }
    m.vote = 'up';
    messages = messages;
    // send question + cited pages so the backend can harvest a Q&A pair from this
    // sourced+upvoted answer (Phase 2). direct fetch — api.feedback strips extras.
    const body = {
      conversation_id: activeId,
      vote: 'up',
      answer: m.text,
      question: questionFor(m),
      page_ids: (m.pages || []).map((p) => p.page_id)
    };
    fetch(`${api.base}/feedback`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) }).catch(() => {});
  }

  // on-demand citation accuracy check for one answer (LLM judges each cited page)
  async function checkAccuracy(m: Msg) {
    if (!m.messageId || m.checking) return;
    m.checking = true; messages = messages;
    try {
      const r = await api.verifyAnswer(m.messageId);
      m.accuracy = r?.score ?? null;
    } catch { m.accuracy = null; }
    finally { m.checking = false; messages = messages; }
  }
  function fmtTokens(n?: number) { return n == null ? '' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); }

  // display-only: strip inline provenance tags from a rendered answer (does NOT mutate stored m.text).
  // removes e.g. "...extension 4500 [Known Fact]." / "[Known fact]" / "[Known]" — leaves real [p.N] citation chips intact.
  function cleanAnswer(text: string): string {
    return (text || '').replace(/\s*\[known fact\]/gi, '').replace(/\s*\[known\]/gi, '');
  }

  // ===== downvote capture (training signal) =====
  let downModal = $state<Msg | null>(null);
  let downNote = $state('');
  let downCorrection = $state('');
  function submitDownvote(skip = false) {
    const m = downModal;
    if (!m) return;
    m.vote = 'down';
    messages = messages;
    const body: any = {
      conversation_id: activeId,
      vote: 'down',
      answer: m.text,
      question: questionFor(m),
      page_ids: (m.pages || []).map((p) => p.page_id),
      served_qa_id: m.servedQaId ?? null   // 👎 on a cache-served answer demotes that pair
    };
    if (!skip) {
      if (downNote.trim()) body.note = downNote.trim();
      if (downCorrection.trim()) body.correction = downCorrection.trim();
    }
    // direct fetch (api.feedback's type strips extra fields); fail-soft
    fetch(`${api.base}/feedback`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) }).catch(() => {});
    downModal = null; downNote = ''; downCorrection = '';
  }
  function cancelDownvote() { downModal = null; downNote = ''; downCorrection = ''; }

  // ===== sources-with-summary drawer (Perplexity-style, grouped by doc) =====
  type SrcGroup = { doc_id: number; title: string; summary: string; pages: { page_id: number; page_no: number; snippet?: string; image_url?: string; has_image?: boolean }[] };
  let sourcesDrawer = $state<{ groups: SrcGroup[] } | null>(null);
  let sourcesBusy = $state(false);
  let provGraph = $state<any>(null);   // provenance mini-map for the sources drawer
  let sourcesFocus = $state<number | null>(null);
  async function openSourcesSummary(m: Msg, focusPid?: number) {
    if (!m.pages || !m.pages.length) return;
    const pids = m.pages.map((p) => p.page_id).filter(Boolean);
    const ids = pids.join(',');
    sourcesBusy = true;
    sourcesDrawer = { groups: [] };
    provGraph = null;
    sourcesFocus = focusPid ?? null;
    api.answerGraph(pids).then((g: any) => { if (g.nodes?.length) provGraph = g; }).catch(() => {});
    try {
      const r = await fetch(`${api.base}/answer/sources?ids=${encodeURIComponent(ids)}`, { headers: authHeaders() });
      const j = await r.json().catch(() => ({}));
      sourcesDrawer = { groups: (j.sources || []) as SrcGroup[] };
      if (sourcesFocus) setTimeout(() => { document.getElementById('src-pg-' + sourcesFocus)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 80);
    } catch {
      sourcesDrawer = { groups: [] };
    } finally {
      sourcesBusy = false;
    }
  }
  function closeSourcesSummary() { sourcesDrawer = null; provGraph = null; }
  // open the existing single-page viewer for a group's pages, focused on page_no
  function openGroupPage(g: SrcGroup, page_no: number) {
    const list: Src[] = g.pages.map((p) => ({
      page_id: p.page_id, doc_id: g.doc_id, doc_name: g.title, page_no: p.page_no, image_url: p.image_url || '', has_image: p.has_image
    }));
    openSrc(list, page_no);
  }
  function openGroupDoc(g: SrcGroup) { goto(`/brain?doc=${g.doc_id}`); }

  // re-run a turn in deep mode
  function regenerate(m: Msg) {
    if (busy) return;
    const q = questionFor(m);
    if (!q) return;
    mode = 'deep';
    input = q;
    send();
  }

  // Delegated click for inline citation chips inside a rendered answer.
  // [N]   → data-n  = 1-based SOURCE number → open the same drawer a coin opens.
  // [p.N] → data-pn = page number → open the single-page viewer (legacy form).
  function onCiteClick(e: MouseEvent, m: Msg) {
    const t = (e.target as HTMLElement)?.closest?.('.cite') as HTMLElement | null;
    if (!t) return;
    e.preventDefault();
    if (t.dataset.n) {
      const n = parseInt(t.dataset.n, 10);
      const p = m.pages?.[n - 1];        // coin i+1 ↔ m.pages[i]
      if (p) openSourcesSummary(m, p.page_id);
      return;
    }
    const pn = parseInt(t.dataset.pn || '', 10);
    openSrc(m.pages, pn);
  }

  function greeting() {
    const h = new Date().getHours();
    return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  }

  // quiet suggestion chips (claude-style) — corpus-derived (zero LLM), click fills the composer.
  // server returns proven Q&A first, then doc-section titles, then curated fallbacks.
  // corpus-derived; empty until /suggestions responds (no hardcoded chips on an
  // empty/wiped corpus — they'd look like real data when there is none).
  let starters = $state<{ cat: string; q: string; doc?: string | null; id?: number; hot?: boolean }[]>([]);
  // home chip language (persisted) — 'en' English, 'my' Burmese
  let chipLang = $state<'en' | 'my'>('en');
  $effect(() => { try { const v = localStorage.getItem('aria_lang'); if (v === 'my' || v === 'en') chipLang = v; } catch {} });
  function setChipLang(l: 'en' | 'my') { chipLang = l; try { localStorage.setItem('aria_lang', l); } catch {} }
  $effect(() => {
    api.suggestions(chipLang).then((r) => { starters = r?.suggestions ?? []; }).catch(() => { starters = []; });
  });
  let taEl = $state<HTMLTextAreaElement | null>(null);
  function useSuggestion(q: string) { input = q; send(); }
  function clickStarter(s: { id?: number; q: string }) {
    if (s.id != null) api.chipClick(s.id, chipLang);   // bandit reward signal
    useSuggestion(s.q);
  }

  // pick a line-icon name for a starter card from its category/question text
  function chipIcon(s: { cat: string; q: string }): string {
    const t = ((s.cat || '') + ' ' + (s.q || '')).toLowerCase();
    if (/user|account|login|password|disable|role|permission/.test(t)) return 'user';
    if (/setup|create|install|configure|new site|provision/.test(t)) return 'setup';
    if (/batch|job|cron|schedule|night|run/.test(t)) return 'batch';
    if (/error|issue|fail|trouble|fix|problem|incident/.test(t)) return 'alert';
    if (/refund|approval|finance|payment|flow|process/.test(t)) return 'flow';
    return 'doc';
  }
  function chipLabel(s: { cat: string; q: string }): string {
    const c = (s.cat || 'DOCS').trim();
    return (c.length > 14 ? c.slice(0, 14) : c).toUpperCase();
  }
  function shortDoc(raw: string) { const t = parseDocName(raw || '').title || raw || ''; return t.length > 22 ? t.slice(0, 21) + '…' : t; }

  // live stat tiles for the welcome hero
  let stats = $state<any>(null);
  $effect(() => { api.usage().then((r) => (stats = r.stats)).catch(() => {}); });

  // wall clock for the top badge
  let clock = $state('');
  $effect(() => {
    const tick = () => { try { clock = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); } catch {} };
    tick();
    const t = setInterval(tick, 30000);
    return () => clearInterval(t);
  });

</script>

<svelte:window onkeydown={onSrcKey} />
<Lightbox bind:src={zoom} caption={zoomCap} />

<!-- ===== source drawer (Perplexity-style document viewer) ===== -->
{#if src && srcCur}
  <button class="srcscrim" onclick={closeSrc} aria-label="Close source"></button>
  <aside class="srcdrawer">
    <div class="srchead">
      <div class="min-w-0">
        <div class="text-[10.5px] uppercase tracking-wide" style="color:var(--clay)">Source · {src.idx + 1} of {src.list.length}</div>
        <div class="text-[14px] font-semibold truncate mt-0.5" style="color:var(--ink)">{srcCur.doc_name}</div>
        <div class="text-[12px]" style="color:var(--muted)">Page {srcCur.page_no}</div>
      </div>
      <button class="srcx" onclick={closeSrc} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    </div>

    <!-- view toggle + zoom toolbar -->
    <div class="srctoolbar">
      <div class="srcseg">
        <button class="{srcView === 'image' ? 'on' : ''}" onclick={() => (srcView = 'image')}>Page</button>
        <button class="{srcView === 'text' ? 'on' : ''}" onclick={() => (srcView = 'text')}>Text</button>
      </div>
      {#if srcView === 'image'}
        <div class="srczoomctl">
          <button onclick={srcZoomOut} aria-label="Zoom out" disabled={srcZoom <= 50}>−</button>
          <span class="tnum">{srcZoom}%</span>
          <button onclick={srcZoomIn} aria-label="Zoom in" disabled={srcZoom >= 300}>+</button>
          <button class="srcfit" onclick={srcFit}>Fit</button>
          <button class="srcfit" onclick={() => srcCur && openZoom(srcCur)} title="Full screen">⤢</button>
        </div>
      {/if}
    </div>

    <!-- viewer body -->
    <div class="srcbody">
      {#if srcView === 'image' && srcCur?.has_image !== false}
        <div class="srcimgwrap">
          <img src={api.pageImg(srcCur.page_id)} alt="page {srcCur.page_no}" style="width:{srcZoom}%" />
        </div>
      {:else if srcView === 'image'}
        <div class="srcimgwrap"><div class="srcmd-ph">No page image — this is a text-only (imported) source. Switch to Text.</div></div>
      {:else}
        <div class="srcmd">
          {#if srcMdBusy}
            <div class="srcmd-ph">Loading page text…</div>
          {:else if srcMd && (srcMd.md || srcMd.raw)}
            {#if !srcMd.compiled}<div class="srcmd-note">Not compiled yet — showing raw extracted text.</div>{/if}
            <div class="md">{@html md(srcMd.md || srcMd.raw)}</div>
          {:else}
            <div class="srcmd-ph">No text for this page.</div>
          {/if}
        </div>
      {/if}
    </div>

    <div class="srcnav">
      <button onclick={srcPrev} disabled={src.idx === 0} aria-label="Previous source">‹ Prev</button>
      <span class="tnum">{src.idx + 1} / {src.list.length}</span>
      <button onclick={srcNext} disabled={src.idx >= src.list.length - 1} aria-label="Next source">Next ›</button>
    </div>

    {#if src.list.length > 1}
      <div class="srcthumbs">
        {#each src.list as p, i}
          <button class="srcthumb {i === src.idx ? 'on' : ''}" onclick={() => srcGo(i)} title="Page {p.page_no}">
            <img src={api.pageImg(p.page_id)} alt="page {p.page_no}" loading="lazy" />
            <span>{i + 1}</span>
          </button>
        {/each}
      </div>
    {/if}

    <div class="srcfoot">
      {#if srcDocId(srcCur)}
        <button class="srcopen" onclick={() => openInBrain(srcCur)}>Open full document →</button>
      {/if}
    </div>
  </aside>
{/if}

<!-- ===== sources-with-summary drawer (Perplexity-style, grouped by doc) ===== -->
{#if sourcesDrawer}
  <button class="ssscrim" onclick={closeSourcesSummary} aria-label="Close sources"></button>
  <aside class="ssdrawer">
    <div class="srchead">
      <div class="min-w-0">
        <div class="text-[10.5px] uppercase tracking-wide" style="color:var(--clay)">Sources</div>
        <div class="text-[14px] font-semibold mt-0.5" style="color:var(--ink)">Where this answer came from</div>
      </div>
      <button class="srcx" onclick={closeSourcesSummary} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    </div>

    <div class="ssbody">
      {#if provGraph && provGraph.nodes?.length}
        <div class="mb-3">
          <div class="text-[10.5px] uppercase tracking-wide mb-1.5" style="color:var(--muted)">How this answer connects</div>
          <div style="height:200px; border-radius:12px; overflow:hidden">
            <BrainGraph data={provGraph} fill onpick={pickNode} />
          </div>
        </div>
      {/if}
      {#if sourcesBusy}
        <div class="srcmd-ph">Loading sources…</div>
      {:else if !sourcesDrawer.groups.length}
        <div class="srcmd-ph">No source details available.</div>
      {:else}
        {#each sourcesDrawer.groups as g}
          <div class="ssgroup">
            <div class="ssg-head">
              <div class="min-w-0">
                <div class="ssg-title">{g.title}</div>
                {#if g.summary}<div class="ssg-summary">{g.summary}</div>{/if}
              </div>
              <button class="ssg-open" onclick={() => openGroupDoc(g)}>Open full doc →</button>
            </div>
            <div class="ssg-pages">
              {#each g.pages as p}
                <button id="src-pg-{p.page_id}" class="ssg-chip {sourcesFocus === p.page_id ? 'focus' : ''}" onclick={() => openGroupPage(g, p.page_no)} title="Page {p.page_no}">
                  {#if p.image_url}
                    <img src={api.pageImg(p.page_id)} alt="page {p.page_no}" loading="lazy" />
                  {:else}
                    <div class="ssg-noimg">p.{p.page_no}</div>
                  {/if}
                  <div class="ssg-chip-body">
                    <div class="ssg-pno">Page {p.page_no}</div>
                    {#if p.snippet}<div class="ssg-snip">{p.snippet}</div>{/if}
                  </div>
                </button>
              {/each}
            </div>
          </div>
        {/each}
      {/if}
    </div>
  </aside>
{/if}

<!-- ===== downvote capture popup (training signal) ===== -->
{#if downModal}
  <button class="dvscrim" onclick={cancelDownvote} aria-label="Cancel"></button>
  <div class="dvcard" role="dialog" aria-modal="true">
    <div class="srchead">
      <div class="min-w-0">
        <div class="text-[10.5px] uppercase tracking-wide" style="color:var(--clay)">Feedback</div>
        <div class="text-[14px] font-semibold mt-0.5" style="color:var(--ink)">Help Aria improve this answer</div>
      </div>
      <button class="srcx" onclick={cancelDownvote} aria-label="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    </div>
    <label class="dvlabel" for="dv-note">What was wrong? <span class="dvopt">(optional)</span></label>
    <textarea id="dv-note" bind:value={downNote} rows="2" class="dvinput" placeholder="e.g. wrong steps, missing detail, cited the wrong page…"></textarea>
    <label class="dvlabel" for="dv-corr">What's the correct answer? <span class="dvopt">(optional)</span></label>
    <textarea id="dv-corr" bind:value={downCorrection} rows="3" class="dvinput" placeholder="The accurate steps / value…"></textarea>
    <div class="dvactions">
      <button class="dvskip" onclick={() => submitDownvote(true)}>Skip</button>
      <button class="dvsubmit" onclick={() => submitDownvote(false)}>Submit feedback</button>
    </div>
  </div>
{/if}

<div class="h-full">
  <!-- ===== thread (rail now lives in the global layout) ===== -->
  <div class="h-full min-w-0 flex flex-col" style="background:#ffffff">
    <div class="h-3 shrink-0"></div>

    <div bind:this={scroller} class="flex-1 overflow-y-auto px-6 py-8">
      {#if messages.length === 0}
        <div class="min-h-full flex flex-col items-center justify-center text-center max-w-2xl mx-auto pb-2">
          <div class="leading-none mb-3"><Burst size={34} /></div>
          <h1 class="serif text-[34px] font-normal leading-tight" style="color:var(--ink)">
            {greeting()}{firstName ? ', ' + firstName : ''}
          </h1>
          <p class="mt-2.5 text-[14px] leading-relaxed" style="color:var(--muted)">
            Runbooks &amp; IT assistance — ask me anything from your SOPs
          </p>
          <!-- starter-chip language toggle -->
          <div class="langtog mt-3" role="group" aria-label="Question language">
            <button class="langbtn {chipLang === 'en' ? 'on' : ''}" onclick={() => setChipLang('en')}>English</button>
            <button class="langbtn {chipLang === 'my' ? 'on' : ''}" onclick={() => setChipLang('my')}>မြန်မာ</button>
          </div>

          <!-- corpus-derived starter cards (2×2) -->
          <div class="mt-8 sgrid">
            {#each starters as s}
              <button class="scard" onclick={() => clickStarter(s)}>
                <span class="scard-eyebrow">
                  <span class="scard-ic">
                    {#if chipIcon(s) === 'user'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                    {:else if chipIcon(s) === 'setup'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                    {:else if chipIcon(s) === 'batch'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
                    {:else if chipIcon(s) === 'alert'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                    {:else if chipIcon(s) === 'flow'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M9 6h6a3 3 0 0 1 3 3v6"/></svg>
                    {:else}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>
                    {/if}
                  </span>
                  <span class="scard-cat">{chipLabel(s)}</span>
                  {#if s.hot}<span class="scard-hot" title="Popular this week">POPULAR</span>{/if}
                  <svg class="scard-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                </span>
                <span class="scard-q">{s.q}</span>
                {#if s.doc}<span class="scard-doc">{s.doc}</span>{/if}
              </button>
            {/each}
          </div>

          <!-- demoted stat line -->
          <div class="mt-7 text-[12px]" style="color:var(--muted)">
            {$convs.length} sessions · {stats?.docs ?? '—'} runbooks · {stats?.pages ?? '—'} pages
          </div>
        </div>
      {/if}

      <div class="max-w-3xl mx-auto space-y-7">
        {#each messages as m}
          {#if m.role === 'user'}
            <div class="flex gap-3.5">
              <div class="shrink-0 w-[30px] h-[30px] rounded-[7px] grid place-items-center text-[13px] font-semibold" style="background:#e0ddd2; color:#5a5750">R</div>
              <div class="flex-1 min-w-0 pt-1 text-[15.5px] leading-relaxed" style="color:var(--ink)">{m.text}</div>
            </div>
          {:else}
            <div class="flex gap-3.5">
              <div class="shrink-0 pt-0.5"><Burst size={28} active={m.loading || m.thinking || m.streaming} /></div>
              <div class="flex-1 min-w-0">
                <!-- Claude-style thinking / activity trace -->
                {#if m.steps && m.steps.length}
                  <div class="think mb-3 rounded-[12px] border overflow-hidden" style="border-color:var(--border); background:#FBFAF8">
                    <button onclick={() => (m.stepsOpen = !m.stepsOpen)} class="w-full flex items-center gap-2 px-3.5 py-2.5 text-left">
                      {#if m.thinking}
                        <svg class="spin shrink-0" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--clay)" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.2-8.5"/></svg>
                        <span class="text-[13px] font-medium" style="color:var(--ink)">Working…</span>
                        <span class="text-[12.5px] truncate" style="color:var(--muted)">{m.steps[m.steps.length - 1]?.label}</span>
                      {:else}
                        <svg class="shrink-0" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#5fa463" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                        <span class="text-[13px] font-medium" style="color:var(--ink)">Thought for {m.thoughtMs ? (m.thoughtMs / 1000).toFixed(1) : '—'}s</span>
                        <span class="text-[12px]" style="color:var(--muted)">· {m.steps.length} steps</span>
                      {/if}
                      <span class="flex-1"></span>
                      {#if !m.thinking}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" style="transform:rotate({m.stepsOpen ? 180 : 0}deg); transition:transform .15s"><path d="M6 9l6 6 6-6"/></svg>
                      {/if}
                    </button>
                    {#if m.thinking || m.stepsOpen}
                      <div class="px-3.5 pb-3 pt-0.5 space-y-2">
                        {#each m.steps as s}
                          <div class="flex items-start gap-2.5">
                            {#if s.state === 'running'}
                              <svg class="spin shrink-0 mt-0.5" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--clay)" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.2-8.5"/></svg>
                            {:else}
                              <span class="shrink-0 mt-[5px] w-1.5 h-1.5 rounded-full" style="background:#5fa463"></span>
                            {/if}
                            <span class="min-w-0">
                              <span class="block text-[12.5px]" style="color:var(--ink)">{s.label}</span>
                              {#if s.detail}<span class="block text-[11.5px] truncate" style="color:var(--muted)">{s.detail}</span>{/if}
                            </span>
                          </div>
                        {/each}
                      </div>
                    {/if}
                  </div>
                {/if}
                {#if m.loading && !(m.steps && m.steps.length)}
                  <div class="flex gap-1.5 items-center py-2">
                    <span class="w-2 h-2 rounded-full animate-bounce" style="background:var(--clay); animation-delay:0ms"></span>
                    <span class="w-2 h-2 rounded-full animate-bounce" style="background:var(--clay); animation-delay:150ms"></span>
                    <span class="w-2 h-2 rounded-full animate-bounce" style="background:var(--clay); animation-delay:300ms"></span>
                  </div>
                {/if}
                {#if !m.loading}
                  <div class="md pt-0.5" style="color:#2b2a27" role="presentation" onclick={(e) => onCiteClick(e, m)}>{@html mdCiteAll(cleanAnswer(m.text))}{#if m.streaming}<span class="caret">▌</span>{/if}</div>
                  {#if m.stopped}
                    <div class="mt-2 text-[12px]" style="color:var(--muted)">Stopped</div>
                  {/if}
                  {#if m.blind && m.nearest}
                    <div class="mt-3 flex items-center gap-2 text-[12.5px] px-3 py-2 rounded-[10px]" style="background:#fdf6ea; border:1px solid #e6cf9f; color:#7a5a20">
                      <span>Thin coverage — nearest runbook:</span>
                      <button class="suggest" style="padding:3px 11px; font-size:12px" onclick={() => useSuggestion('Tell me about ' + m.nearest)}>{m.nearest}</button>
                    </div>
                  {/if}
                  {#if m.pages && m.pages.length}
                    <div class="mt-4 flex items-center gap-2 flex-wrap">
                      <span class="text-[11px] uppercase tracking-wide" style="color:var(--muted)">Sources</span>
                      {#each m.pages as p, i}
                        <button class="coin" onclick={() => openSourcesSummary(m, p.page_id)} title="{shortDoc(p.doc_name)} · page {p.page_no}">
                          <span class="coinn">{i + 1}</span>
                          <span class="coinlbl">{shortDoc(p.doc_name)}·p{p.page_no}</span>
                        </button>
                      {/each}
                      <button class="coinmore" onclick={() => openSourcesSummary(m)}>{m.pages.length} source{m.pages.length === 1 ? '' : 's'} →</button>
                    </div>
                  {/if}

                  <!-- ===== unified, quiet answer footer (actions left · metadata right) ===== -->
                  {#if m.text}
                    <div class="footrow mt-3">
                      <!-- LEFT: actions as thin monochrome icons -->
                      <div class="footacts">
                        <button onclick={() => copyAnswer(m)} class="ico {m.copied ? 'on' : ''}" aria-label="Copy answer" title={m.copied ? 'Copied' : 'Copy'}>
                          {#if m.copied}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                          {:else}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                          {/if}
                        </button>
                        <button onclick={() => regenerate(m)} class="ico" disabled={busy} aria-label="Try again in deep mode" title="Try again (deep)">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>
                        </button>
                        <button onclick={() => voteAnswer(m, 'up')} class="ico {m.vote === 'up' ? 'on' : ''}" aria-label="Good answer" title="Good answer">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v11"/><path d="M7 10l4-7a2 2 0 0 1 3 1.7V9h5a2 2 0 0 1 2 2.3l-1.3 8A2 2 0 0 1 20.7 21H7"/></svg>
                        </button>
                        <button onclick={() => voteAnswer(m, 'down')} class="ico {m.vote === 'down' ? 'on' : ''}" aria-label="Bad answer" title="Bad answer">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V3"/><path d="M17 14l-4 7a2 2 0 0 1-3-1.7V15H5a2 2 0 0 1-2-2.3l1.3-8A2 2 0 0 1 6.3 3H17"/></svg>
                        </button>
                        {#if m.messageId}
                          <button onclick={() => shareAnswer(m)} class="ico {m.shared ? 'on' : ''}" aria-label="Share answer" title={m.shared ? 'Link copied' : 'Share answer'}>
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/></svg>
                          </button>
                        {/if}
                      </div>

                      <!-- RIGHT: muted metadata, single line -->
                      <div class="footmeta">
                        <span>ARIA 1.0</span>
                        <span class="dot">·</span>
                        <span>{mode === 'deep' ? 'Deep' : mode === 'quick' ? 'Quick' : 'Auto'}</span>
                        {#if !m.streaming && m.tokens}<span class="dot">·</span><span class="tnum" title="prompt {m.tokens.in} + output {m.tokens.out}">{fmtTokens(m.tokens.total)} tokens</span>{/if}
                        {#if !m.streaming && m.cost != null}<span class="dot">·</span><span class="tnum">${m.cost.toFixed(4)}</span>{/if}
                        {#if !m.streaming && m.thoughtMs}<span class="dot">·</span><span class="tnum">{(m.thoughtMs / 1000).toFixed(1)}s</span>{/if}
                        {#if !m.streaming && m.text && !m.grounded}<span class="dot">·</span><span class="unsourced">unsourced</span>{/if}
                        {#if !m.streaming && m.accuracy != null}
                          <span class="dot">·</span>
                          <span class="acc-chip" style="background:{m.accuracy >= 80 ? '#e8f3ec' : m.accuracy >= 60 ? '#fbf1df' : '#fbe9e6'}; color:{m.accuracy >= 80 ? '#3f8f5f' : m.accuracy >= 60 ? '#a9742a' : '#c0492f'}" title="share of cited pages that actually back the answer">{m.accuracy}% accurate</span>
                        {:else if !m.streaming && m.messageId && m.grounded}
                          <span class="dot">·</span>
                          <button class="acc-link" disabled={m.checking} onclick={() => checkAccuracy(m)}>{m.checking ? 'checking…' : 'check accuracy'}</button>
                        {/if}
                      </div>
                    </div>
                  {/if}

                  {#if (m.related && m.related.length) || (m.follows && m.follows.length)}
                    <div class="footsec">
                      <div class="foothead">Explore</div>
                      {#if m.related && m.related.length}
                        <div class="flex flex-wrap gap-1.5">
                          {#each m.related as r}
                            <button class="exchip" onclick={() => useSuggestion('Tell me about ' + r.title)}>{r.title}</button>
                          {/each}
                        </div>
                      {/if}
                      {#if m.follows && m.follows.length}
                        <div class="flex flex-wrap gap-1.5 {m.related && m.related.length ? 'mt-2' : ''}">
                          {#each m.follows as f}
                            <button class="exchip exq" onclick={() => { input = f; send(); }}>
                              <span class="exq-q">?</span>{f}
                            </button>
                          {/each}
                        </div>
                      {/if}
                    </div>
                  {/if}
                {/if}
              </div>
            </div>
          {/if}
        {/each}
      </div>
    </div>

    <!-- composer -->
    <div class="px-6 pb-5 pt-3 shrink-0">
      <div class="composer max-w-3xl mx-auto rounded-2xl border p-3" style="background:var(--paper); border-color:var(--border)">
        <textarea bind:value={input} bind:this={taEl}
          onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          onfocus={(e)=>{ const c=e.currentTarget.closest('.composer') as HTMLElement; if(c){c.style.borderColor='var(--clay)'; c.style.boxShadow='0 0 0 3px #f3f3f1';} }}
          onblur={(e)=>{ const c=e.currentTarget.closest('.composer') as HTMLElement; if(c){c.style.borderColor='var(--border)'; c.style.boxShadow='0 1px 3px rgba(0,0,0,.04)';} }}
          placeholder="Ask anything…" rows="1"
          class="w-full resize-none bg-transparent outline-none px-2 pt-1 pb-2.5 text-[15.5px] max-h-40" style="color:var(--ink)"></textarea>
        <div class="flex items-center justify-between">
          <div class="flex gap-2 items-center">
            <!-- Auto / Quick / Deep segmented toggle -->
            <div class="seg flex items-center rounded-[9px] border p-0.5" style="border-color:var(--border); background:white">
              <button type="button" onclick={() => setMode('auto')}
                class="seg-pill {mode === 'auto' ? 'on' : ''}"
                title="let Aria pick quick or deep per question">Auto</button>
              <button type="button" onclick={() => setMode('quick')}
                class="seg-pill {mode === 'quick' ? 'on' : ''}"
                title="fast, detailed answer from page text">Quick</button>
              <button type="button" onclick={() => setMode('deep')}
                class="seg-pill {mode === 'deep' ? 'on' : ''}"
                title="also reads page images + navigates (slower, thorough)">Deep</button>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <!-- model wordmark, Claude-style, in the input area -->
            <span class="text-[12.5px] select-none" style="color:var(--muted)">ARIA 1.0</span>
            {#if busy}
              <button onclick={stop} aria-label="Stop" class="w-10 h-10 rounded-full grid place-items-center text-white transition-transform hover:scale-105" style="background:var(--clay)">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
              </button>
            {:else}
              <button onclick={send} disabled={!input.trim()} aria-label="Send" class="w-10 h-10 rounded-full grid place-items-center text-white disabled:opacity-40 transition-transform hover:scale-105" style="background:var(--clay)">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
              </button>
            {/if}
          </div>
        </div>
      </div>
      <div class="text-center text-[11.5px] mt-2.5" style="color:var(--muted)">Aria answers from your SOPs and runbooks and cites the source page. Verify critical steps against the document.</div>
    </div>
  </div>
</div>

<style>
  /* inline citation markers ([N] / [p.N]) → small coral superscript chips */
  :global(.md sup.cite) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    vertical-align: super;
    margin: 0 1px;
    min-width: 14px;
    height: 14px;
    padding: 0 4px;
    font-size: 10px;
    line-height: 1;
    border-radius: 999px;
    background: #f6e9e2;
    color: var(--clay);
    cursor: pointer;
    border: none;
    font-weight: 700;
    transition: background 0.12s ease;
  }
  :global(.md sup.cite:hover) { background: var(--clay); color: #fff; }

  /* Quick / Deep segmented toggle */
  .seg-pill {
    padding: 5px 10px;
    font-size: 12.5px;
    border-radius: 7px;
    color: var(--muted);
    background: transparent;
    transition: background 0.12s ease, color 0.12s ease;
    white-space: nowrap;
  }
  .seg-pill.on {
    background: #f3f3f1;
    color: var(--clay);
    font-weight: 600;
  }

  .starter {
    border-color: var(--border);
    transition: border-color .12s ease, box-shadow .12s ease, transform .12s ease, background .12s ease;
  }
  .starter:hover {
    border-color: var(--clay);
    background: #f7f7f5;
    box-shadow: 0 4px 14px rgba(0,0,0,.06);
    transform: translateY(-1px);
  }
  /* claude-style quiet suggestion chips */
  .suggest {
    font-size: 13.5px; color: var(--ink); background: var(--paper);
    border: 1px solid var(--border); border-radius: 999px; padding: 8px 15px;
    max-width: 340px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    transition: border-color .12s ease, background .12s ease, color .12s ease;
  }
  .suggest:hover { border-color: var(--clay); color: var(--clay); background: #f3f3f1; }

  /* corpus-derived starter cards — 2×2 grid, icon + category eyebrow + wrapping question */
  .sgrid {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px; width: 100%; max-width: 560px;
  }
  .scard {
    display: flex; flex-direction: column; gap: 9px; text-align: left;
    padding: 15px 16px; border: 1px solid var(--border); border-radius: 14px;
    background: var(--paper);
    transition: border-color .14s ease, background .14s ease,
                box-shadow .16s ease, transform .16s ease;
  }
  .scard:hover {
    border-color: #d8d6cf; background: #fcfbfa;
    box-shadow: 0 6px 18px rgba(0,0,0,.06); transform: translateY(-2px);
  }
  .scard-eyebrow { display: flex; align-items: center; gap: 7px; }
  .scard-ic {
    width: 24px; height: 24px; border-radius: 7px; flex: none;
    display: grid; place-items: center;
    background: #f4f3f0; color: var(--brand);
    transition: background .14s ease, color .14s ease;
  }
  .scard-ic svg { width: 14px; height: 14px; }
  .scard:hover .scard-ic { background: var(--brand); color: #fff; }
  .scard-cat {
    font-size: 10.5px; font-weight: 700; letter-spacing: .07em;
    color: var(--muted); text-transform: uppercase;
  }
  .scard-arrow {
    width: 15px; height: 15px; margin-left: auto; color: var(--muted);
    opacity: 0; transform: translateX(-3px);
    transition: opacity .14s ease, transform .14s ease, color .14s ease;
  }
  .scard:hover .scard-arrow { opacity: 1; transform: translateX(0); color: var(--ink); }
  .scard-q {
    font-size: 13.5px; line-height: 1.45; color: var(--ink);
    display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3;
    -webkit-box-orient: vertical; overflow: hidden;
  }
  .scard-doc {
    display: block; margin-top: 7px; font-size: 11px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; opacity: .85;
  }
  .scard-hot {
    margin-left: 6px; font-size: 8.5px; font-weight: 700; letter-spacing: .06em;
    color: var(--clay); border: 1px solid var(--clay); border-radius: 999px;
    padding: 1px 6px; opacity: .9;
  }
  @media (max-width: 560px) { .sgrid { grid-template-columns: 1fr; } }

  /* starter-chip language toggle */
  .langtog { display: inline-flex; gap: 2px; padding: 2px; border: 1px solid var(--border); border-radius: 999px; background: #fff; }
  .langbtn { font-size: 12px; padding: 3px 12px; border-radius: 999px; color: var(--muted); transition: background .12s ease, color .12s ease; }
  .langbtn.on { background: var(--clay); color: #fff; }
  .langbtn:not(.on):hover { color: var(--ink); }

  .caret { display: inline-block; width: 7px; color: var(--clay); animation: caret 1s steps(1) infinite; }
  @keyframes caret { 50% { opacity: 0; } }
  /* compact source coins (was big page thumbnails — those live in the drawer now) */
  .coin { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px 3px 4px; border: 1px solid var(--border); background: #fff; border-radius: 999px; transition: border-color .12s ease, background .12s ease; max-width: 220px; }
  .coin:hover { border-color: var(--clay); background: #f7f7f5; }
  .coinn { width: 19px; height: 19px; border-radius: 50%; background: var(--clay); color: #fff; font-size: 10.5px; font-weight: 700; display: grid; place-items: center; flex: none; }
  .coinlbl { font-size: 12px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .coinmore { font-size: 12px; color: var(--clay); font-weight: 500; padding: 3px 9px; border-radius: 999px; }
  .coinmore:hover { text-decoration: underline; }
  .composer { transition: border-color .14s ease, box-shadow .14s ease; box-shadow: 0 1px 3px rgba(0,0,0,.04); }

  /* ===== source drawer (Perplexity-style document viewer) ===== */
  .srcscrim { position: fixed; inset: 0; z-index: 55; background: rgba(40,35,30,.34); -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px); border: none; cursor: default; }
  .srcdrawer { position: fixed; z-index: 56; top: 0; right: 0; bottom: 0; width: 560px; max-width: 96vw; background: var(--cream); box-shadow: -14px 0 50px rgba(40,30,20,.18); padding: 18px 18px 16px; display: flex; flex-direction: column; animation: srcin .22s ease; }
  @keyframes srcin { from { transform: translateX(40px); opacity: .4; } to { transform: translateX(0); opacity: 1; } }
  .srchead { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; flex-shrink: 0; }
  .srcx { flex-shrink: 0; width: 30px; height: 30px; border-radius: 8px; border: none; background: #ece8df; cursor: pointer; font-size: 14px; color: #46443f; }
  .srcx:hover { background: #e2ddd2; }
  /* toolbar: view toggle + zoom */
  .srctoolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; flex-shrink: 0; }
  .srcseg { display: inline-flex; padding: 2px; border-radius: 9px; background: #e9e5dc; gap: 2px; }
  .srcseg button { font-size: 12px; padding: 5px 14px; border-radius: 7px; border: none; background: transparent; color: var(--muted); cursor: pointer; font-weight: 600; }
  .srcseg button.on { background: #fff; color: var(--clay); box-shadow: 0 1px 2px rgba(0,0,0,.06); }
  .srczoomctl { display: flex; align-items: center; gap: 4px; color: var(--muted); font-size: 12px; }
  .srczoomctl button { width: 28px; height: 28px; border-radius: 7px; border: 1px solid var(--border); background: #fff; cursor: pointer; font-size: 15px; line-height: 1; color: var(--ink); }
  .srczoomctl button:disabled { opacity: .4; cursor: default; }
  .srczoomctl .tnum { width: 40px; text-align: center; }
  .srcfit { width: auto !important; padding: 0 9px; font-size: 12px !important; font-weight: 600; }
  /* viewer body — fills remaining height, scrolls */
  .srcbody { flex: 1; min-height: 0; border: 1px solid var(--border); border-radius: 12px; background: #faf8f3; overflow: auto; box-shadow: 0 4px 18px rgba(40,30,20,.05) inset; }
  .srcimgwrap { display: flex; justify-content: center; padding: 10px; min-height: 100%; }
  .srcimgwrap img { display: block; height: fit-content; max-width: none; background: #fff; box-shadow: 0 2px 10px rgba(40,30,20,.12); border-radius: 2px; }
  .srcmd { padding: 18px 20px; background: #fff; min-height: 100%; }
  .srcmd-ph { color: var(--muted); font-size: 13px; padding: 20px; text-align: center; }
  .srcmd-note { font-size: 11px; color: #a9742a; background: #fbf2e6; padding: 5px 9px; border-radius: 6px; margin-bottom: 12px; }
  .srcnav { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; color: var(--muted); font-size: 12.5px; flex-shrink: 0; }
  .srcnav button { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); background: #fff; cursor: pointer; font-size: 12.5px; color: var(--ink); }
  .srcnav button:disabled { opacity: .4; cursor: default; }
  .srcthumbs { display: flex; gap: 8px; margin-top: 12px; overflow-x: auto; padding-bottom: 4px; flex-shrink: 0; }
  .srcthumb { position: relative; flex: 0 0 auto; width: 54px; height: 70px; border-radius: 8px; overflow: hidden; border: 2px solid var(--border); background: #fff; cursor: pointer; padding: 0; }
  .srcthumb.on { border-color: var(--clay); }
  .srcthumb img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
  .srcthumb span { position: absolute; top: 2px; left: 2px; width: 15px; height: 15px; display: grid; place-items: center; border-radius: 50%; font-size: 9px; font-weight: 700; color: #fff; background: rgba(43,42,39,.7); }
  .srcfoot { display: flex; gap: 8px; margin-top: 12px; flex-shrink: 0; }
  .srcopen { flex: 1; background: var(--clay); color: #fff; border: none; border-radius: 9px; padding: 11px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .srcopen:hover { background: var(--clay-dk); }
  .tnum { font-variant-numeric: tabular-nums; }

  /* ===== unified quiet answer footer ===== */
  .footrow {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; flex-wrap: wrap;
  }
  .footacts { display: flex; align-items: center; gap: 2px; }
  .ico {
    display: grid; place-items: center;
    width: 28px; height: 28px; flex: 0 0 auto;
    border-radius: 7px; border: none; background: transparent;
    color: var(--muted); cursor: pointer;
    transition: background .12s ease, color .12s ease;
  }
  .ico:hover { background: #efefec; color: var(--ink); }
  .ico:disabled { opacity: .4; cursor: default; }
  .ico:disabled:hover { background: transparent; color: var(--muted); }
  .ico.on { color: var(--clay); background: #efefec; }
  .footmeta {
    display: flex; align-items: center; flex-wrap: wrap;
    gap: 5px; margin-left: auto;
    font-size: 11.5px; color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .footmeta .dot { opacity: .55; }
  .footmeta .tnum { font-variant-numeric: tabular-nums; }
  .footmeta .unsourced { color: #c0492f; }
  .acc-chip { font-weight: 600; padding: 1px 8px; border-radius: 999px; }
  .acc-link {
    font: inherit; color: var(--clay); background: transparent; border: none;
    padding: 0; cursor: pointer; text-decoration: underline;
    text-underline-offset: 2px; text-decoration-thickness: 1px;
  }
  .acc-link:hover { opacity: .75; }
  .acc-link:disabled { opacity: .5; cursor: default; text-decoration: none; }

  /* lighter related / follow-up sections — hairline divider + muted label */
  .footsec {
    margin-top: 14px; padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .foothead {
    font-size: 10.5px; font-weight: 600; letter-spacing: .1em;
    text-transform: uppercase; color: var(--muted);
    margin-bottom: 8px;
  }
  /* compact explore chips (related runbooks + follow-up questions) */
  .exchip {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 12.5px; color: var(--ink);
    background: #fff; border: 1px solid var(--border); border-radius: 999px;
    padding: 5px 12px; max-width: 360px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    transition: border-color .12s ease, background .12s ease, color .12s ease;
  }
  .exchip:hover { background: #efefec; border-color: #d8d6d0; color: var(--ink); }
  .exq-q {
    display: grid; place-items: center; flex: none;
    width: 15px; height: 15px; border-radius: 50%;
    background: #efefec; color: var(--muted);
    font-size: 10px; font-weight: 700;
  }
  .stat { border-color: var(--border); transition: border-color .12s ease, box-shadow .12s ease; }
  .stat:hover { border-color: #d8d6d0; box-shadow: 0 3px 12px rgba(0,0,0,.05); }
  :global(.line-clamp-2) {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  /* sources-summary chip under an answer */
  .srcchip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px; font-size: 12.5px; font-weight: 600;
    border-radius: 999px; border: 1px solid var(--border);
    background: #f3f3f1; color: var(--clay); cursor: pointer;
    transition: background .12s ease, border-color .12s ease;
  }
  .srcchip:hover { background: #dde7f2; border-color: var(--clay); }

  /* ===== sources-with-summary drawer (mirrors .srcdrawer, one z below) ===== */
  .ssscrim { position: fixed; inset: 0; z-index: 53; background: rgba(40,35,30,.34); -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px); border: none; cursor: default; }
  .ssdrawer { position: fixed; z-index: 54; top: 0; right: 0; bottom: 0; width: 520px; max-width: 96vw; background: var(--cream); box-shadow: -14px 0 50px rgba(40,30,20,.18); padding: 18px 18px 16px; display: flex; flex-direction: column; animation: srcin .22s ease; }
  .ssbody { flex: 1; min-height: 0; overflow: auto; }
  .ssgroup { border: 1px solid var(--border); border-radius: 12px; background: #fff; padding: 14px; margin-bottom: 12px; }
  .ssg-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
  .ssg-title { font-size: 13.5px; font-weight: 700; color: var(--ink); }
  .ssg-summary { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.5; }
  .ssg-open { flex-shrink: 0; font-size: 11.5px; font-weight: 600; color: var(--clay); background: #f3f3f1; border: none; border-radius: 8px; padding: 6px 10px; cursor: pointer; white-space: nowrap; }
  .ssg-open:hover { background: #dde7f2; }
  .ssg-pages { display: flex; flex-direction: column; gap: 8px; }
  .ssg-chip { display: flex; gap: 10px; align-items: flex-start; text-align: left; padding: 8px; border: 1px solid var(--border); border-radius: 10px; background: #faf8f3; cursor: pointer; transition: border-color .12s ease, background .12s ease; }
  .ssg-chip.focus { border-color: var(--clay); background: #f3f3f1; box-shadow: 0 0 0 2px #dcdcd8; }
  .ssg-chip:hover { border-color: var(--clay); background: #fff; }
  .ssg-chip img { flex-shrink: 0; width: 52px; height: 66px; object-fit: cover; object-position: top; border-radius: 5px; border: 1px solid var(--border); background: #fff; }
  .ssg-noimg { flex-shrink: 0; width: 52px; height: 66px; display: grid; place-items: center; border-radius: 5px; border: 1px solid var(--border); background: #fff; font-size: 11px; font-weight: 700; color: var(--clay); }
  .ssg-chip-body { min-width: 0; }
  .ssg-pno { font-size: 12px; font-weight: 600; color: var(--ink); }
  .ssg-snip { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  /* ===== downvote capture popup ===== */
  .dvscrim { position: fixed; inset: 0; z-index: 57; background: rgba(40,35,30,.34); -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px); border: none; cursor: default; }
  .dvcard { position: fixed; z-index: 58; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 460px; max-width: 94vw; background: var(--cream); border-radius: 16px; box-shadow: 0 24px 60px rgba(40,30,20,.28); padding: 18px; animation: srcin .18s ease; }
  .dvlabel { display: block; font-size: 12.5px; font-weight: 600; color: var(--ink); margin: 12px 0 6px; }
  .dvopt { font-weight: 400; color: var(--muted); }
  .dvinput { width: 100%; resize: vertical; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 9px 11px; font-size: 13.5px; color: var(--ink); outline: none; }
  .dvinput:focus { border-color: var(--clay); box-shadow: 0 0 0 3px #f3f3f1; }
  .dvactions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
  .dvskip { padding: 8px 16px; border-radius: 9px; border: 1px solid var(--border); background: #fff; color: var(--muted); font-size: 13px; cursor: pointer; }
  .dvskip:hover { background: #f3f1ea; }
  .dvsubmit { padding: 8px 18px; border-radius: 9px; border: none; background: var(--clay); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
  .dvsubmit:hover { background: var(--clay-dk); }
</style>
