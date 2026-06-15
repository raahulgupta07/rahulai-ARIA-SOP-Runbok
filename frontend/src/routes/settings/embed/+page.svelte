<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import EChart from '$lib/EChart.svelte';
  import { areaOpt, hbarOpt, C } from '$lib/charts';
  import { tick } from '$lib/dashstore';


  type Tab = 'overview' | 'sandbox' | 'appearance' | 'widgets' | 'monitoring' | 'developer';
  let tab = $state<Tab>('overview');
  const TABS: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'widgets', label: 'Widgets' },
    { id: 'appearance', label: 'Theme' },
    { id: 'sandbox', label: 'Sandbox' },
    { id: 'monitoring', label: 'Monitoring' },
    { id: 'developer', label: 'Developer' },
  ];
  const TAB_SUB: Record<Tab, string> = {
    overview: 'Activity at a glance',
    sandbox: 'Try it live',
    appearance: 'Brand & theme',
    widgets: 'Keys & origins',
    monitoring: 'Traffic & blocks',
    developer: 'Snippet & API',
  };

  let host = $state('');
  let err = $state('');
  let keys = $state<any[]>([]);
  let stats = $state<any>({ totals: {}, widgets: [], series: [], blocked: {} });
  let brand = $state<any>({ accent: '#c2683f', position: 'right', greeting: '', subtitle: '', logo_url: '', launcher: 'robot', title: 'City Agent Aria' });
  let justKey = $state<any>(null);

  onMount(() => { host = location.origin; });
  // live: initial load + silent refresh every heartbeat (stats tween, no flash)
  $effect(() => { $tick; loadAll(); });

  async function loadAll() {
    try {
      const [k, s, b] = await Promise.all([api.embedKeys(), api.embedStats(30), api.embedBrand()]);
      keys = k.keys; stats = s; brand = { ...brand, ...b };
    } catch (e: any) { err = e.message; }
  }

  // ---------- create widget ----------
  let nf = $state({ name: '', origins: '', rate: 30, msgCap: 0, costCap: 0, greeting: '', accent: '#c2683f', position: 'right', subtitle: '', logo_url: '', launcher: 'robot', anyOrigin: true });
  let showCreate = $state(false);
  let creating = $state(false);
  function parseOrigins(s: string) { return s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean); }
  async function create() {
    if (!nf.name.trim()) { err = 'Name required'; return; }
    creating = true; err = '';
    try {
      justKey = await api.embedCreateKey({
        name: nf.name.trim(), allowed_origins: nf.anyOrigin ? [] : parseOrigins(nf.origins), rate_per_min: nf.rate,
        daily_msg_cap: nf.msgCap || 0, daily_cost_cap: nf.costCap || 0,
        greeting: nf.greeting.trim() || undefined, accent: nf.accent, position: nf.position,
        subtitle: nf.subtitle.trim() || undefined, logo_url: nf.logo_url.trim() || undefined, launcher: nf.launcher,
      });
      nf = { name: '', origins: '', rate: 30, msgCap: 0, costCap: 0, greeting: '', accent: '#c2683f', position: 'right', subtitle: '', logo_url: '', launcher: 'robot', anyOrigin: true };
      showCreate = false; await loadAll();
    } catch (e: any) { err = e.message; } finally { creating = false; }
  }

  // ---------- key actions ----------
  async function rotate(k: any) { if (!confirm(`Rotate secret for "${k.name}"? Old secret dies now.`)) return; try { const r = await api.embedRotateKey(k.id); justKey = { ...k, secret: r.secret }; } catch (e: any) { err = e.message; } }
  async function toggle(k: any) { try { await api.embedUpdateKey(k.id, { active: !k.active }); await loadAll(); } catch (e: any) { err = e.message; } }
  async function del(k: any) { if (!confirm(`Delete "${k.name}"?`)) return; try { await api.embedDeleteKey(k.id); await loadAll(); } catch (e: any) { err = e.message; } }

  // ---------- brand ----------
  let savingBrand = $state(false);
  let savedBrand = $state(false);
  async function saveBrand() {
    savingBrand = true; savedBrand = false;
    try { brand = await api.embedSaveBrand(brand); savedBrand = true; setTimeout(() => savedBrand = false, 2000); }
    catch (e: any) { err = e.message; } finally { savingBrand = false; }
  }
  let overrides = $derived(keys.filter((k) => (k.accent && k.accent !== brand.accent) || (k.greeting && k.greeting !== brand.greeting)));

  function snippet(pk: string) { return `<script src="${host}/widget.js" data-key="${pk}" async><\/script>`; }
  function copy(t: string) { navigator.clipboard?.writeText(t); }

  // ---------- monitoring ----------
  let monKey = $state<number | 'all'>('all');
  let seriesOpt = $derived(areaOpt((stats.series || []).map((r: any) => ({ label: r.label, n: r.n })), { color: C.blue, name: 'Messages' }));
  let topWidgets = $derived(hbarOpt((stats.widgets || []).slice(0, 8).map((w: any) => ({ label: w.name, value: w.messages || 0 })), { color: C.teal }));

  function statFor(k: any) { return (stats.widgets || []).find((w: any) => w.id === k.id) || {}; }

  // ---------- sandbox (try it live) ----------
  let sbKey = $state<number | null>(null);
  let sbToken = $state('');
  let sbVid = $state('');
  let sbTheme = $state<any>({});
  let sbLoading = $state(false);
  let sbDemoing = $state(false);
  $effect(() => { if (sbKey === null && keys.length) sbKey = keys[0].id; });
  let sbPk = $derived(keys.find((k) => k.id === sbKey)?.public_key || '');
  async function genToken() {
    if (!sbKey) return;
    sbLoading = true; err = '';
    try { const r = await api.embedSandboxToken(sbKey); sbToken = r.token; sbVid = r.visitor_id; sbTheme = r; }
    catch (e: any) { err = e.message; } finally { sbLoading = false; }
  }
  let sbSrc = $derived.by(() => {
    if (!sbToken) return '';
    const p = new URLSearchParams({ token: sbToken, accent: sbTheme.accent || '#c2683f' });
    if (sbTheme.greeting) p.set('greeting', sbTheme.greeting);
    if (sbTheme.subtitle) p.set('subtitle', sbTheme.subtitle);
    if (sbTheme.title) p.set('title', sbTheme.title);
    if (sbTheme.logo_url) p.set('logo', sbTheme.logo_url);
    return `/embed?${p.toString()}`;
  });
  async function createDemo() {
    sbDemoing = true; err = '';
    try { justKey = await api.embedCreateKey({ name: 'Sandbox', allowed_origins: [], rate_per_min: 60 }); await loadAll(); sbKey = null; }
    catch (e: any) { err = e.message; } finally { sbDemoing = false; }
  }
  function curlCmd() {
    return `curl -N -X POST ${host}/api/ask/stream \\
  -H "Authorization: Bearer ${sbToken || '<token>'}" \\
  -H "Content-Type: application/json" \\
  -d '{"q":"What is this system?","mode":"quick"}'`;
  }
  import Section from '$lib/settings/Section.svelte';
</script>

<div class="px-7 py-6">
  {#if err}<div class="rounded-lg px-3 py-2 text-[13px] mb-4" style="background:#fbeae6;color:#c0492f">{err}</div>{/if}

  <!-- secret reveal (any tab) -->
  {#if justKey}
    <div class="rounded-xl border p-4 space-y-3 mb-5" style="border-color:#cdebd6;background:#f1faf4">
      <div class="font-semibold text-[14px] flex items-center gap-1.5" style="color:#2f8f5f"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copy the secret now — it won't be shown again</div>
      <div class="space-y-2 text-[13px]">
        <div><span class="lbl">Public</span><code class="kc">{justKey.public_key}</code><button class="cbtn" onclick={() => copy(justKey.public_key)}>Copy</button></div>
        <div><span class="lbl">Secret</span><code class="kc">{justKey.secret}</code><button class="cbtn" onclick={() => copy(justKey.secret)}>Copy</button></div>
        <div><span class="lbl">Snippet</span><code class="kc snip">{snippet(justKey.public_key)}</code><button class="cbtn" onclick={() => copy(snippet(justKey.public_key))}>Copy</button></div>
      </div>
      <button class="text-[12px] underline" style="color:var(--muted)" onclick={() => justKey = null}>Dismiss</button>
    </div>
  {/if}

  <!-- top pill tabs -->
  <div class="toptabs">
    {#each TABS as t}
      <button class="ptab" class:on={tab === t.id} onclick={() => tab = t.id}>{t.label}</button>
    {/each}
  </div>
  <div class="tabsub">{TAB_SUB[tab]}</div>
  <div class="emc">

  <!-- ============ OVERVIEW ============ -->
  {#if tab === 'overview'}
    <Section title="At a glance" desc="Live embed activity across all widgets over the last 30 days.">
      <div class="grid grid-cols-4 gap-3">
        {#each [
          { k: 'live', label: 'widgets live', sub: `${stats.totals?.widgets || 0} total`, c: '#1a1a18' },
          { k: 'visitors', label: 'visitors · 30d', c: '#3f7fb0' },
          { k: 'messages', label: 'messages · 30d', c: '#7b6bd6' },
          { k: 'blocked', label: 'blocked · 30d', sub: 'origin / rate', c: '#c0492f' }
        ] as s}
          <div class="tile">
            <div class="tval" style="color:{s.c}">{stats.totals?.[s.k] ?? 0}</div>
            <div class="tlbl">{s.label}</div>
            {#if s.sub}<div class="tsub">{s.sub}</div>{/if}
          </div>
        {/each}
      </div>
    </Section>
    <Section title="How embedding works" desc="Drop Aria on any website so anonymous visitors can chat with the same brain.">
      <p class="text-[14px] mb-3" style="color:var(--muted)">
        Drop Aria on any website. Members keep logging in as normal — embed keys let anonymous
        visitors chat with the same brain. <b>Public key</b> goes in the snippet; the
        <b>secret key</b> is for server-to-server SSO and is shown only once.
      </p>
      <button class="primary" onclick={() => { tab = 'widgets'; showCreate = true; }}>+ New widget</button>
    </Section>
  {/if}

  <!-- ============ SANDBOX (try it live · single screen, no scroll) ============ -->
  {#if tab === 'sandbox'}
    {#if keys.length === 0}
      <div class="sbempty">
        <div class="text-[15px] font-semibold mb-1" style="color:var(--ink)">No widget yet</div>
        <p class="text-[13px] mb-4" style="color:var(--muted)">Create a ready-to-use sandbox key (any origin, 60/min) and start chatting in one click.</p>
        <button class="primary" onclick={createDemo} disabled={sbDemoing}>{sbDemoing ? 'Creating…' : '+ Create demo key'}</button>
      </div>
    {:else}
      <div class="sbwrap">
        <!-- compact toolbar -->
        <div class="sbbar">
          <select class="sel" bind:value={sbKey} onchange={() => { sbToken=''; }}>
            {#each keys as k}<option value={k.id}>{k.name}</option>{/each}
          </select>
          <button class="primary" onclick={genToken} disabled={sbLoading}>{#if sbLoading}…{:else}<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none" style="vertical-align:-1px"><polygon points="13 2 3 14 11 14 11 22 21 10 13 10 13 2"/></svg> Generate token{/if}</button>
          <span class="text-[12px]" style="color:var(--muted)">skips origin check · 30 min</span>
          {#if sbToken}
            <code class="kc snip sbtok" title={sbToken}>{sbToken}</code>
            <button class="cbtn" onclick={() => copy(sbToken)}>Copy</button>
          {/if}
        </div>
        <!-- two columns fill the rest of the screen -->
        <div class="sbgrid">
          <div class="sbpane">
            {#if sbSrc}
              <iframe title="sandbox" src={sbSrc}
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
                      class="sbframe"></iframe>
            {:else}
              <div class="sbph">Click <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none" style="vertical-align:-1px"><polygon points="13 2 3 14 11 14 11 22 21 10 13 10 13 2"/></svg> Generate token to start the live widget</div>
            {/if}
          </div>
          <div class="sbside">
            <div class="sbcard">
              <div class="font-semibold text-[13px] mb-2">Drop-in snippet</div>
              <code class="kc snip" style="max-width:100%">{snippet(sbPk)}</code>
              <button class="cbtn block mt-2" onclick={() => copy(snippet(sbPk))}>Copy snippet</button>
            </div>
            <div class="sbcard">
              <div class="font-semibold text-[13px] mb-2">Call the API with your token</div>
              <pre class="code">{curlCmd()}</pre>
              <button class="cbtn block mt-2" onclick={() => copy(curlCmd())}>Copy cURL</button>
            </div>
          </div>
        </div>
      </div>
    {/if}
  {/if}

  <!-- ============ THEME (brand default + live preview) ============ -->
  {#if tab === 'appearance'}
    <div class="grid grid-cols-2 gap-4">
      <Section title="Default appearance" desc="Applies to every widget unless a key sets its own override.">
        <div class="grid grid-cols-2 gap-3">
          <label class="fld"><span>Accent color</span><div class="flex gap-2 items-center"><input type="color" class="swatch" bind:value={brand.accent} /><input class="hexin" bind:value={brand.accent} /></div></label>
          <label class="fld"><span>Position</span>
            <select bind:value={brand.position}><option value="right">bottom-right</option><option value="left">bottom-left</option></select>
          </label>
          <label class="fld col-span-2"><span>Welcome message</span><input bind:value={brand.greeting} placeholder="Hi! Ask me about our SOPs." /></label>
          <label class="fld col-span-2"><span>Subtitle (status line)</span><input bind:value={brand.subtitle} placeholder="online · replies instantly" /></label>
          <label class="fld col-span-2"><span>Logo URL (optional)</span><input bind:value={brand.logo_url} placeholder="https://example.com/logo.png" /></label>
          <label class="fld col-span-2"><span>Launcher icon</span>
            <select bind:value={brand.launcher}><option value="robot">robot</option><option value="chat">chat bubble</option><option value="logo">logo</option></select>
          </label>
        </div>
        <button class="primary mt-4" onclick={saveBrand} disabled={savingBrand}>{savingBrand ? 'Saving…' : savedBrand ? 'Saved' : 'Save brand default'}</button>
        <div class="mt-4 pt-3 text-[12px]" style="border-top:1px solid #efece4;color:var(--muted)">
          <b style="color:#2f8f5f">●</b> {keys.length - overrides.length} inherit brand &nbsp;·&nbsp;
          <b style="color:#c98a2e">◐</b> {overrides.length} custom override{overrides.length === 1 ? '' : 's'}
          {#if overrides.length}<span> — {overrides.map((o) => o.name).join(', ')}</span>{/if}
        </div>
      </Section>

      <!-- live preview -->
      <Section title="Live preview" desc="A mock of the widget header, greeting and composer.">
        <div class="pv" style="--a:{brand.accent}">
          <div class="pv-head">
            {#if brand.logo_url}<img src={brand.logo_url} alt="" class="pv-logo" />{:else}<span class="pv-dotwrap"><span class="pv-dot"></span></span>{/if}
            <div><div class="pv-title">{brand.title || 'City Agent Aria'}</div><div class="pv-sub">{brand.subtitle || 'online · replies instantly'}</div></div>
          </div>
          <div class="pv-body">
            <div class="pv-bubble">{brand.greeting || 'Hi! Ask me anything about our runbooks & SOPs.'}</div>
          </div>
          <div class="pv-input"><span>ask something…</span><span class="pv-send">→</span></div>
        </div>
        <div class="text-[11px] mt-2 text-center" style="color:var(--muted)">preview · position bottom-{brand.position}</div>
      </Section>
    </div>
  {/if}

  <!-- ============ WIDGETS ============ -->
  {#if tab === 'widgets'}
    {#if showCreate}
      <Section title="New widget" desc="Generate an embed key with its own limits and styling.">
        {#snippet actions()}
          <button class="primary" onclick={() => showCreate = !showCreate}>Close</button>
        {/snippet}
        <div class="grid grid-cols-2 gap-3">
          <label class="fld"><span>Name</span><input bind:value={nf.name} placeholder="Marketing site" /></label>
          <label class="fld"><span>Rate / min per visitor</span><input type="number" bind:value={nf.rate} min="1" /></label>
          <label class="fld"><span>Daily message cap (0 = unlimited)</span><input type="number" bind:value={nf.msgCap} min="0" /></label>
          <label class="fld"><span>Daily budget USD (0 = unlimited)</span><input type="number" step="0.01" bind:value={nf.costCap} min="0" /></label>
          <div class="fld col-span-2">
            <span>Where can this widget run?</span>
            <div class="ppmode">
              <button type="button" class="ppopt {nf.anyOrigin ? 'on' : ''}" onclick={() => (nf.anyOrigin = true)}>
                <b><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M9 2v6"/><path d="M15 2v6"/><path d="M7 8h10v3a5 5 0 0 1-10 0z"/><path d="M12 16v6"/></svg> Any website (plug &amp; play)</b><i>Drop the snippet on any site — works immediately. Set a daily cap to bound cost.</i>
              </button>
              <button type="button" class="ppopt {!nf.anyOrigin ? 'on' : ''}" onclick={() => (nf.anyOrigin = false)}>
                <b><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Lock to my sites</b><i>Only the domains you list may load it. Most secure.</i>
              </button>
            </div>
            {#if !nf.anyOrigin}
              <textarea rows="2" bind:value={nf.origins} placeholder="https://www.example.com&#10;http://192.168.2.46:8090" style="margin-top:8px"></textarea>
              <span class="hint">One origin per line — exact scheme + host + port, no trailing slash.</span>
            {:else}
              <span class="hint">Tip: set a <b>Daily message cap</b> above so an open key can't run up unbounded cost.</span>
            {/if}
          </div>
          <label class="fld"><span>Welcome (blank = brand default)</span><input bind:value={nf.greeting} /></label>
          <label class="fld"><span>Accent</span><input type="color" class="swatch" bind:value={nf.accent} /></label>
        </div>
        <button class="primary mt-3" onclick={create} disabled={creating}>{creating ? 'Creating…' : 'Create widget'}</button>
      </Section>
    {/if}

    <Section title="Widgets" desc="Every embed key, its limits, usage and lifecycle actions.">
      {#snippet actions()}
        {#if !showCreate}<button class="primary" onclick={() => showCreate = !showCreate}>+ New widget</button>{/if}
      {/snippet}
    {#if keys.length === 0}
      <div class="text-[13px]" style="color:var(--muted)">No widgets yet.</div>
    {:else}
      {#each keys as k}
        {@const st = statFor(k)}
        <div class="rounded-xl border p-4 mb-3" style="border-color:#efefec;background:#fff;opacity:{k.active ? 1 : .55}">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="w-3 h-3 rounded-full" style="background:{k.accent || brand.accent}"></span>
              <b class="text-[14px]">{k.name}</b>
              <span class="text-[11px] px-1.5 py-0.5 rounded" style="background:{k.active ? '#eaf6ee' : '#f0eee9'};color:{k.active ? '#2f8f5f' : '#8a877f'}">{k.active ? 'active' : 'disabled'}</span>
            </div>
            <div class="flex gap-2 text-[12px]">
              <button class="lnk" onclick={() => { monKey = k.id; tab = 'monitoring'; }}>Stats</button>
              <button class="lnk" onclick={() => toggle(k)}>{k.active ? 'Disable' : 'Enable'}</button>
              <button class="lnk" onclick={() => rotate(k)}>Rotate secret</button>
              <button class="lnk" style="color:#c0492f" onclick={() => del(k)}>Delete</button>
            </div>
          </div>
          <div class="mt-2 space-y-1.5 text-[12px]">
            <div><span class="lbl">Public</span><code class="kc">{k.public_key}</code><button class="cbtn" onclick={() => copy(k.public_key)}>Copy</button></div>
            <div><span class="lbl">Secret</span><code class="kc" style="color:var(--muted)">sk_live_…{k.secret_last4}</code></div>
            <div><span class="lbl">Snippet</span><code class="kc snip">{snippet(k.public_key)}</code><button class="cbtn" onclick={() => copy(snippet(k.public_key))}>Copy</button></div>
            <div class="text-[11px]" style="color:var(--muted)">
              origins: {k.allowed_origins?.length ? k.allowed_origins.join(', ') : 'any'} · {k.rate_per_min}/min{#if k.daily_msg_cap} · cap {k.daily_msg_cap} msg/day{/if}{#if k.daily_cost_cap} · ${k.daily_cost_cap}/day{/if}
              · {st.visitors || 0} visitors · {st.messages || 0} msgs (30d)
            </div>
          </div>
        </div>
      {/each}
    {/if}
    </Section>
  {/if}

  <!-- ============ MONITORING ============ -->
  {#if tab === 'monitoring'}
    <Section title="Traffic" desc="Message volume and the widgets driving it over the last 30 days.">
      <div class="grid grid-cols-2 gap-4">
        <div class="rounded-xl border p-4" style="border-color:#efefec;background:#fff">
          <div class="text-[12px] mb-1" style="color:var(--muted)">Messages / day · 30d</div>
          <EChart option={seriesOpt} height={200} />
        </div>
        <div class="rounded-xl border p-4" style="border-color:#efefec;background:#fff">
          <div class="text-[12px] mb-1" style="color:var(--muted)">Busiest widgets</div>
          <EChart option={topWidgets} height={200} />
        </div>
      </div>
    </Section>
    <Section title="Blocks & sessions" desc="Requests denied by origin or rate limits, plus total sessions.">
      <div class="grid grid-cols-3 gap-3">
        <div class="tile"><div class="tval" style="color:#c0492f">{stats.blocked?.origin_block || 0}</div><div class="tlbl">origin blocks</div></div>
        <div class="tile"><div class="tval" style="color:#c98a2e">{stats.blocked?.rate_block || 0}</div><div class="tlbl">rate blocks</div></div>
        <div class="tile"><div class="tval" style="color:#1a1a18">{stats.totals?.sessions || 0}</div><div class="tlbl">sessions · 30d</div></div>
      </div>
    </Section>
    <Section title="Per-widget breakdown" desc="Visitors, sessions and messages for each embed key.">
      <div class="rounded-xl border" style="border-color:#efefec;background:#fff;overflow:hidden">
        <table class="w-full text-[13px]">
          <thead><tr style="background:#f6f3ec;color:var(--muted)"><th class="th">Widget</th><th class="th">Visitors</th><th class="th">Sessions</th><th class="th">Messages</th><th class="th">Status</th></tr></thead>
          <tbody>
            {#each stats.widgets || [] as w}
              <tr style="border-top:1px solid #efece4"><td class="td">{w.name}</td><td class="td">{w.visitors || 0}</td><td class="td">{w.sessions || 0}</td><td class="td">{w.messages || 0}</td><td class="td">{w.active ? 'active' : 'off'}</td></tr>
            {/each}
          </tbody>
        </table>
      </div>
    </Section>
  {/if}

  <!-- ============ DEVELOPER ============ -->
  {#if tab === 'developer'}
    <Section title="Integration guide" desc="Copy-paste recipes for embedding, SSO and the raw chat API.">
      <div class="space-y-4">
        <div class="rounded-xl border p-5" style="border-color:#efefec;background:#fff">
          <div class="font-semibold text-[14px] mb-2">① HTML widget — paste before &lt;/body&gt;</div>
          <pre class="code">{`<script src="${host}/widget.js" data-key="pk_live_xxx" async><\/script>`}</pre>
          <div class="text-[12px] mt-2" style="color:var(--muted)">Optional: <code>data-accent</code> · <code>data-position="left"</code> · <code>data-title</code></div>
        </div>
        <div class="rounded-xl border p-5" style="border-color:#efefec;background:#fff">
          <div class="font-semibold text-[14px] mb-2">② SSO — server-side, with the secret key</div>
          <pre class="code">{`// your backend (never expose sk_ to the browser)
const r = await fetch("${host}/api/embed/token", {
  method: "POST",
  headers: { "Content-Type": "application/json",
             "Authorization": "Bearer sk_live_xxx" },
  body: JSON.stringify({ visitor_id: user.id, name: user.name }),
});
const { token } = await r.json();  // 30-min visitor JWT`}</pre>
        </div>
        <div class="rounded-xl border p-5" style="border-color:#efefec;background:#fff">
          <div class="font-semibold text-[14px] mb-2">③ Raw chat API</div>
          <pre class="code">{`POST ${host}/api/ask/stream
Authorization: Bearer <visitor token>
{ "q": "How do I reset a price?", "mode": "quick" }
// NDJSON stream: meta → token* → done`}</pre>
        </div>
        <p class="text-[12px]" style="color:var(--muted)">Full reference lives in <code>EMBED.md</code> at the project root.</p>
      </div>
    </Section>
  {/if}
  </div><!-- /.emc -->
</div>

<style>
  /* top pill tabs */
  .toptabs { display: flex; flex-wrap: wrap; gap: 6px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
  .ptab { font-size: 13px; font-weight: 600; padding: 7px 15px; border-radius: 999px; border: none; cursor: pointer; background: transparent; color: var(--muted); transition: background .12s, color .12s; }
  .ptab:hover { background: #f0efed; color: var(--ink); }
  .ptab.on { background: var(--clay); color: #fff; }
  .tabsub { font-size: 12px; color: var(--muted); margin: 12px 0 14px; }
  .emc { min-width: 0; }
  /* sandbox — single screen, internal layout, no page scroll */
  .sbwrap { display: flex; flex-direction: column; gap: 10px; height: clamp(320px, calc(100vh - 300px), 900px); min-height: 320px; }
  .sbbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex: none; }
  .sbtok { max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
  .sbgrid { display: grid; grid-template-columns: minmax(320px, 1fr) minmax(300px, 1fr); gap: 14px; flex: 1; min-height: 0; }
  .sbpane { min-height: 0; display: flex; }
  .sbframe { width: 100%; height: 100%; border: 1px solid #efefec; border-radius: 14px; background: #fff; }
  .sbph { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; font-size: 13px; border: 1px dashed #ddd8cd; border-radius: 14px; color: var(--muted); background: #fff; }
  .sbside { display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow: auto; }
  .sbcard { border: 1px solid #efefec; border-radius: 12px; padding: 14px 16px; background: #fff; flex: none; }
  .sbempty { text-align: center; padding: 48px 12px; }
  .tile { background: #fff; border: 1px solid #efefec; border-radius: 12px; padding: 14px 16px; }
  .tval { font-size: 26px; font-weight: 700; line-height: 1.1; }
  .tlbl { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .tsub { font-size: 11px; color: #a8a59d; }
  .fld { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
  .fld input, .fld textarea, .fld select { border: 1px solid #ddd8cd; border-radius: 8px; padding: 8px 10px; font-size: 13px; color: var(--ink); background: #fff; }
  /* color picker = clean square swatch (override the generic input padding/flex) */
  .fld input.swatch { flex: none !important; width: 44px; height: 38px; padding: 3px; cursor: pointer; }
  .fld input.swatch::-webkit-color-swatch-wrapper { padding: 0; }
  .fld input.swatch::-webkit-color-swatch { border: none; border-radius: 5px; }
  .fld input.swatch::-moz-color-swatch { border: none; border-radius: 5px; }
  .fld input.hexin { flex: 1; min-width: 0; }
  .ppmode { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 4px; }
  .ppopt { text-align: left; border: 1.5px solid #ddd8cd; border-radius: 10px; padding: 10px 12px; background: #fff; cursor: pointer; display: flex; flex-direction: column; gap: 3px; transition: border-color .12s, background .12s; }
  .ppopt:hover { border-color: var(--clay); }
  .ppopt.on { border-color: var(--clay); background: #fdf3ee; }
  .ppopt b { font-size: 12.5px; font-weight: 600; color: var(--ink); }
  .ppopt i { font-size: 11px; font-style: normal; color: var(--muted); line-height: 1.4; }
  .hint { font-size: 11px; color: var(--muted); margin-top: 5px; }
  .fld .flex input { flex: 1; }
  .col-span-2 { grid-column: span 2; }
  .sel { border: 1px solid #ddd8cd; border-radius: 8px; padding: 8px 12px; font-size: 13px; background: #fff; color: var(--ink); }
  .cbtn.block { display: inline-block; margin-left: 0; }
  .primary { background: var(--clay); color: #fff; border: none; border-radius: 9px; padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .primary:disabled { opacity: .5; }
  .lbl { display: inline-block; width: 64px; color: var(--muted); }
  .kc { background: #f7f7f5; border: 1px solid var(--border); border-radius: 6px; padding: 2px 7px; font-family: ui-monospace, monospace; font-size: 12px; color: var(--ink); }
  .kc.snip { display: inline-block; max-width: 540px; overflow-x: auto; white-space: nowrap; vertical-align: middle; }
  .cbtn { margin-left: 8px; font-size: 11px; color: var(--clay); cursor: pointer; background: none; border: none; }
  .lnk { color: #46443f; cursor: pointer; background: none; border: none; }
  .lnk:hover { text-decoration: underline; }
  .code { background: #2b2a27; color: #e8e4da; border-radius: 9px; padding: 12px 14px; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.55; overflow-x: auto; white-space: pre; }
  .th { text-align: left; padding: 8px 12px; font-weight: 600; font-size: 12px; }
  .td { padding: 8px 12px; }
  /* live preview widget */
  .pv { flex: 1; display: flex; flex-direction: column; border-radius: 14px; overflow: hidden; box-shadow: none; background: #fff; min-height: 320px; }
  .pv-head { background: var(--a); color: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 10px; }
  .pv-logo { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; background: #fff; }
  .pv-dotwrap { width: 28px; height: 28px; border-radius: 50%; background: rgba(255,255,255,.2); display: flex; align-items: center; justify-content: center; }
  .pv-dot { width: 9px; height: 9px; border-radius: 50%; background: #fff; }
  .pv-title { font-weight: 600; font-size: 14px; }
  .pv-sub { font-size: 11px; opacity: .85; }
  .pv-body { flex: 1; background: #faf9f5; padding: 14px; }
  .pv-bubble { background: #fff; border: 1px solid #ece9e2; border-radius: 14px; border-bottom-left-radius: 4px; padding: 9px 12px; font-size: 13px; max-width: 85%; color: #2b2a27; }
  .pv-input { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-top: 1px solid #ece9e2; background: #fff; color: #a8a59d; font-size: 13px; }
  .pv-send { width: 28px; height: 28px; border-radius: 50%; background: var(--a); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; }
</style>
