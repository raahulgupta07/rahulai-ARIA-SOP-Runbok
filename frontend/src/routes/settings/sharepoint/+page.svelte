<script lang="ts">
  import { api } from '$lib/api';

  type Method = 'push' | 'device' | 'app';

  // ---- config (mirrors api.spGet shape) ----
  let cfg = $state<any>({
    enabled: true,
    method: 'push' as Method,
    site_url: '',
    folder: '',
    recursive: true,
    globs: '*.pdf,*.png,*.jpg',
    interval_h: 6,
    push_token_set: false,
    device: { connected: false, account: '' },
    app: { creds_ready: false, site_path: '' }
  });

  // ---- status (mirrors api.spStatus shape) ----
  let status = $state<any>({
    method: '', running: false, last_sync: null, count: 0,
    error: null, next_run: null, connected: false, log: []
  });

  let loading = $state(true);
  let saving = $state(false);
  let err = $state('');
  let toast = $state('');

  // method picker
  const METHODS: { id: Method; name: string; sub: string; star?: boolean }[] = [
    { id: 'push',   name: 'Desktop Sync',  sub: 'Zero-admin. A small script on your PC watches a folder and pushes new files here.', star: true },
    { id: 'device', name: 'Device login',  sub: 'Sign in once — the server pulls headlessly. No Azure app registration.' },
    { id: 'app',    name: 'Azure app',     sub: 'Tenant + client + secret, app-only access. Managed on the Microsoft 365 page.' }
  ];

  // push (Desktop Sync) state
  let pushOS = $state<'win' | 'mac'>('win');
  let pushToken = $state('');           // shown ONCE after rotate
  let rotating = $state(false);
  let downloading = $state(false);

  // device login state
  let deviceModal = $state(false);
  let deviceCode = $state('');
  let deviceUri = $state('');
  let deviceMsg = $state('');
  let deviceErr = $state('');
  let deviceStarting = $state(false);
  let deviceDisconnecting = $state(false);

  // app state
  let appTesting = $state(false);
  let appTestMsg = $state<{ ok: boolean; detail: string } | null>(null);

  // schedule / status
  let syncing = $state(false);

  // polling — runs while a sync is running OR the device modal is open
  let pollTimer: any = null;
  let needPoll = $derived(!!status.running || deviceModal);

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  function humanize(ts: string | null): string {
    if (!ts) return 'never';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 0) return d.toLocaleString();
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  // ---- load + poll ----
  let started = false;
  $effect(() => {
    if (started) return;
    started = true;
    (async () => {
      try { cfg = { ...cfg, ...(await api.spGet()) }; }
      catch (e: any) { err = e?.message || 'failed to load config'; }
      try { status = { ...status, ...(await api.spStatus()) }; }
      catch { /* status is best-effort */ }
      finally { loading = false; }
    })();
    return () => { if (pollTimer) clearInterval(pollTimer); };
  });

  // start/stop the 4s poll based on needPoll
  $effect(() => {
    if (needPoll && !pollTimer) {
      pollTimer = setInterval(refreshStatus, 4000);
    } else if (!needPoll && pollTimer) {
      clearInterval(pollTimer); pollTimer = null;
    }
  });

  // poll device-login result while the modal is open
  $effect(() => {
    if (!deviceModal) return;
    const t = setInterval(pollDevice, 4000);
    return () => clearInterval(t);
  });

  async function refreshStatus() {
    try { status = { ...status, ...(await api.spStatus()) }; } catch { /* fail-soft */ }
  }

  // ---- generic save (partial) ----
  async function save(patch: Record<string, any>) {
    saving = true; err = '';
    try {
      cfg = { ...cfg, ...(await api.spSave(patch)) };
      flash('Saved');
    } catch (e: any) { err = e?.message || 'save failed'; flash('Error: ' + (e?.message || 'save failed')); }
    finally { saving = false; }
  }

  async function pickMethod(m: Method) {
    if (cfg.method === m) return;
    cfg.method = m;                 // optimistic
    await save({ method: m });
    await refreshStatus();
  }

  // ---- push (Desktop Sync) ----
  async function rotateToken() {
    rotating = true; err = '';
    try {
      const r = await api.spRotateToken();
      pushToken = r?.token || '';
      cfg.push_token_set = true;
      flash('New token generated — copy it now');
    } catch (e: any) { err = e?.message || 'could not generate token'; }
    finally { rotating = false; }
  }

  function copyToken() {
    try { navigator.clipboard?.writeText(pushToken); flash('Copied'); } catch { /* ignore */ }
  }

  async function downloadScript() {
    downloading = true; err = '';
    try {
      const r = await api.spAgentScript(pushOS);
      const blob = new Blob([r?.script ?? ''], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = r?.filename || (pushOS === 'win' ? 'aria-sync.ps1' : 'aria-sync.sh');
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
      flash('Script downloaded');
    } catch (e: any) { err = e?.message || 'could not build script'; }
    finally { downloading = false; }
  }

  // ---- device login ----
  async function startDevice() {
    deviceStarting = true; deviceErr = ''; err = '';
    deviceCode = ''; deviceUri = ''; deviceMsg = '';
    deviceModal = true;
    try {
      const r = await api.spDeviceStart();
      if (r?.ok) {
        deviceCode = r.user_code || '';
        deviceUri = r.verification_uri || 'https://microsoft.com/devicelogin';
        deviceMsg = r.message || '';
      } else {
        deviceErr = r?.detail || 'could not start device login';
      }
    } catch (e: any) { deviceErr = e?.message || 'could not start device login'; }
    finally { deviceStarting = false; }
  }

  async function pollDevice() {
    try {
      const r = await api.spDevicePoll();
      if (!r) return;
      if (r.status === 'done') {
        deviceModal = false;
        cfg = { ...cfg, device: { ...cfg.device, connected: true } };
        try { cfg = { ...cfg, ...(await api.spGet()) }; } catch { /* keep optimistic */ }
        await refreshStatus();
        flash('Connected');
      } else if (r.status === 'error') {
        deviceErr = r.detail || 'sign-in failed';
      }
      // 'pending' → keep waiting
    } catch { /* fail-soft, keep polling */ }
  }

  async function disconnectDevice() {
    deviceDisconnecting = true; err = '';
    try {
      await api.spDeviceDisconnect();
      cfg = { ...cfg, device: { connected: false, account: '' } };
      flash('Disconnected');
    } catch (e: any) { err = e?.message || 'disconnect failed'; }
    finally { deviceDisconnecting = false; }
  }

  // ---- app ----
  async function testApp() {
    appTesting = true; appTestMsg = null; err = '';
    try { appTestMsg = await api.spAppTest(); }
    catch (e: any) { appTestMsg = { ok: false, detail: e?.message || 'test failed' }; }
    finally { appTesting = false; }
  }

  // ---- schedule ----
  async function syncNow() {
    syncing = true; err = '';
    try {
      status = { ...status, ...(await api.spSyncNow()) };
      flash('Sync started');
    } catch (e: any) { err = e?.message || 'could not start sync'; }
    finally { syncing = false; }
  }

  function saveInterval() {
    let n = Number(cfg.interval_h);
    if (!Number.isFinite(n)) n = 6;
    n = Math.min(168, Math.max(1, Math.round(n)));
    cfg.interval_h = n;
    save({ interval_h: n });
  }

  function logColor(level: string): string {
    const l = (level || '').toLowerCase();
    if (l === 'error') return '#c0492f';
    if (l === 'warn' || l === 'warning') return '#9a6a16';
    return '#6b675f';
  }
</script>

<div class="ppad">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else}
    {#if !cfg.enabled}
      <div class="banner">Connector disabled — set <code>CONNECTOR_SP_ENABLED=1</code> to enable SharePoint sync.</div>
    {/if}

    {#if err}<div class="errbar">{err}</div>{/if}

    <!-- method picker -->
    <div class="lbl">How files get here</div>
    <div class="mcards">
      {#each METHODS as m}
        <button class="mcard" class:on={cfg.method === m.id} onclick={() => pickMethod(m.id)} disabled={saving}>
          <div class="mc-top">
            <span class="mc-nm">{m.name}{#if m.star}<span class="star" title="Recommended"> ★</span>{/if}</span>
            {#if cfg.method === m.id}
              <svg class="mc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="var(--clay)"/><path d="m8 12 2.5 2.5L16 9" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            {:else}
              <svg class="mc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#cfcabf" stroke-width="1.6"/></svg>
            {/if}
          </div>
          <div class="mc-sub">{m.sub}</div>
        </button>
      {/each}
    </div>

    <!-- per-method pane -->
    {#if cfg.method === 'push'}
      <div class="card">
        <div class="card-h"><h3>Desktop Sync setup</h3>
          <p>A tiny watcher script runs on your PC. Drop documents into a folder and it pushes new files straight into Aria — no Azure admin, no server credentials.</p>
        </div>

        <ol class="steps">
          <li>
            <b>Pick your OS</b>
            <div class="ostoggle">
              <button class="oseg" class:on={pushOS === 'win'} onclick={() => (pushOS = 'win')}>Windows</button>
              <button class="oseg" class:on={pushOS === 'mac'} onclick={() => (pushOS = 'mac')}>macOS</button>
            </div>
          </li>
          <li>
            <b>Generate a token</b> <span class="hint">— the script authenticates with this. Shown once.</span>
            <div class="tokrow">
              <button class="btn" onclick={rotateToken} disabled={rotating}>{rotating ? 'Generating…' : (cfg.push_token_set ? 'Regenerate token' : 'Generate token')}</button>
              {#if cfg.push_token_set && !pushToken}<span class="ftxt ok">✓ a token is set</span>{/if}
            </div>
            {#if pushToken}
              <div class="tokbox">
                <code class="tokval">{pushToken}</code>
                <button class="btn ghost link" onclick={copyToken}>Copy</button>
              </div>
              <p class="warn">⚠ Copy this now — it won't be shown again. Regenerating revokes the old one.</p>
            {/if}
          </li>
          <li>
            <b>Download the script</b> <span class="hint">— save it on the PC that has the documents.</span>
            <div class="tokrow">
              <button class="btn pri" onclick={downloadScript} disabled={downloading}>{downloading ? 'Building…' : `Download ${pushOS === 'win' ? '.ps1' : '.sh'} script`}</button>
            </div>
          </li>
          <li>
            <b>Set the watched folder &amp; run it</b>
            <label class="fg"><span>Watched-folder hint <span class="hint">— shown in the script header (optional)</span></span>
              <input bind:value={cfg.folder} placeholder="C:\Aria\drop  or  ~/Aria/drop" onblur={() => save({ folder: cfg.folder })} />
            </label>
            <p class="cardsub">New <code>.pdf</code> / <code>.png</code> / <code>.jpg</code> files in that folder are pushed automatically while the script runs.</p>
          </li>
        </ol>
      </div>

    {:else if cfg.method === 'device'}
      <div class="card">
        <div class="card-h"><h3>Device login</h3>
          <p>Sign in once with your Microsoft account. The server then pulls from SharePoint headlessly — no Azure app registration needed.</p>
        </div>

        {#if cfg.device?.connected}
          <div class="connbar">
            <span class="dot"></span>
            <span class="connnm">Connected{#if cfg.device.account} as <b>{cfg.device.account}</b>{/if}</span>
            <button class="btn ghost" onclick={disconnectDevice} disabled={deviceDisconnecting}>{deviceDisconnecting ? 'Disconnecting…' : 'Disconnect'}</button>
          </div>
        {:else}
          <div class="connrow">
            <button class="btn pri" onclick={startDevice} disabled={deviceStarting}>{deviceStarting ? 'Starting…' : 'Connect'}</button>
            <span class="cardsub">Opens a one-time sign-in code.</span>
          </div>
        {/if}

        <div class="g2 mt12">
          <label class="fg"><span>SharePoint site URL</span>
            <input bind:value={cfg.site_url} placeholder="https://contoso.sharepoint.com/sites/Ops" onblur={() => save({ site_url: cfg.site_url })} />
          </label>
          <label class="fg"><span>Folder <span class="hint">— library path</span></span>
            <input bind:value={cfg.folder} placeholder="Shared Documents/Runbooks" onblur={() => save({ folder: cfg.folder })} />
          </label>
        </div>
        <p class="cardsub">Imports <code>.pdf</code> / <code>.png</code> / <code>.jpg</code> from that location on each sync.</p>
      </div>

    {:else}
      <div class="card">
        <div class="card-h"><h3>Azure app (app-only)</h3>
          <p>Tenant + client + secret give the server app-only access. Manage the full credentials on the <a href="/settings/microsoft">Microsoft 365 page</a>; here you set which site to import and test the connection.</p>
        </div>

        <div class="connrow">
          {#if cfg.app?.creds_ready}<span class="ftxt ok">✓ credentials are configured</span>
          {:else}<span class="ftxt bad">No app credentials yet — set them on <a href="/settings/microsoft">Microsoft 365</a>.</span>{/if}
          <button class="btn" onclick={testApp} disabled={appTesting}>{appTesting ? 'Testing…' : 'Test connection'}</button>
          {#if appTestMsg}<span class="ftxt" class:ok={appTestMsg.ok} class:bad={!appTestMsg.ok}>{appTestMsg.ok ? '✓ ' : '✗ '}{appTestMsg.detail}</span>{/if}
        </div>

        <div class="g2 mt12">
          <label class="fg"><span>SharePoint site URL</span>
            <input bind:value={cfg.site_url} placeholder="https://contoso.sharepoint.com/sites/Ops" onblur={() => save({ site_url: cfg.site_url })} />
          </label>
          <label class="fg"><span>Folder <span class="hint">— library path</span></span>
            <input bind:value={cfg.folder} placeholder="Shared Documents/Runbooks" onblur={() => save({ folder: cfg.folder })} />
          </label>
        </div>
      </div>
    {/if}

    <!-- schedule card -->
    <div class="card">
      <div class="card-h"><h3>Schedule</h3>
        <p>How often Aria checks for new files, and an on-demand run.</p>
      </div>
      <div class="schedrow">
        <label class="fg sched-int"><span>Sync every (hours) <span class="hint">— 1 to 168</span></span>
          <input type="number" min="1" max="168" bind:value={cfg.interval_h} onblur={saveInterval} />
        </label>
        <button class="btn pri" onclick={syncNow} disabled={syncing || status.running}>{syncing || status.running ? 'Syncing…' : 'Sync now'}</button>
        <div class="nextrun">
          <span class="nr-lbl">Next run</span>
          <span class="nr-val">{status.next_run ? humanize(status.next_run) : '—'}</span>
        </div>
      </div>
    </div>

    <!-- status card -->
    <div class="card">
      <div class="card-h"><h3>Status</h3>
        <p>Live sync activity for the <b>{cfg.method === 'push' ? 'Desktop Sync' : cfg.method === 'device' ? 'Device login' : 'Azure app'}</b> method.</p>
      </div>

      <div class="statgrid">
        <div class="stat">
          <span class="s-lbl">State</span>
          <span class="s-val">
            <span class="pdot" class:run={status.running}></span>
            {status.running ? 'Running' : 'Idle'}
          </span>
        </div>
        <div class="stat">
          <span class="s-lbl">Last sync</span>
          <span class="s-val">{humanize(status.last_sync)}</span>
        </div>
        <div class="stat">
          <span class="s-lbl">Files synced</span>
          <span class="s-val">{status.count ?? 0}</span>
        </div>
      </div>

      {#if status.error}
        <div class="errbar mt12">{status.error}</div>
      {/if}

      <div class="loglbl">Activity log</div>
      {#if status.log && status.log.length}
        <div class="loglist">
          {#each status.log.slice(-20) as line}
            <div class="logrow">
              <span class="lts">{humanize(line.ts)}</span>
              <span class="lmsg" style="color:{logColor(line.level)}">{line.msg}</span>
            </div>
          {/each}
        </div>
      {:else}
        <p class="cardsub">No activity yet — run a sync to see the log here.</p>
      {/if}
    </div>
  {/if}

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<!-- device-login modal -->
{#if deviceModal}
  <div class="mscrim" role="presentation" onclick={() => (deviceModal = false)}>
    <div class="mcard" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="mhead">
        <h3>Sign in to Microsoft</h3>
        <button class="mx" onclick={() => (deviceModal = false)} aria-label="Close">✕</button>
      </div>
      {#if deviceErr}
        <div class="errbar">{deviceErr}</div>
        <button class="btn" onclick={startDevice} disabled={deviceStarting}>Try again</button>
      {:else if deviceStarting && !deviceCode}
        <p class="muted">Requesting a sign-in code…</p>
      {:else}
        <p class="mline">1. Go to <a href={deviceUri || 'https://microsoft.com/devicelogin'} target="_blank" rel="noopener">{deviceUri || 'microsoft.com/devicelogin'}</a></p>
        <p class="mline">2. Enter this code:</p>
        <div class="codebox">{deviceCode || '…'}</div>
        {#if deviceMsg}<p class="cardsub">{deviceMsg}</p>{/if}
        <p class="cardsub">Waiting for you to finish signing in…</p>
      {/if}
    </div>
  </div>
{/if}

<style>
  /* settings layout renders children flush — own padding wrapper (LANDMINE) */
  .ppad { padding: 6px 28px 32px; max-width: 1040px; }

  /* local tokens — app.css :root carries --cream/--sand/--ink/--muted/--border/--clay; declare the rest as literals */
  .ppad { --line: #e0dfda; }

  .muted { font-size: 13px; color: var(--muted); }
  .banner { margin-bottom: 14px; font-size: 13px; color: #6b675f; background: #f4f3f0; border: 1px solid var(--border); border-radius: 10px; padding: 10px 13px; }
  .banner code { background: #fff; border: 1px solid var(--border); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
  .errbar { margin-bottom: 14px; font-size: 13px; color: #c0492f; background: #fbeae6; border: 1px solid #e6bdb2; border-radius: 10px; padding: 9px 13px; }
  .mt12 { margin-top: 12px; }

  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); z-index: 90;
    background: #1c1a17; color: #fff; font-size: 13px; font-weight: 500; padding: 11px 18px; border-radius: 11px;
    box-shadow: 0 10px 30px rgba(40,35,30,.28); animation: tin .2s ease-out; }
  @keyframes tin { from { opacity: 0; transform: translate(-50%,8px); } to { opacity: 1; transform: translate(-50%,0); } }

  .lbl { font-size: 11px; letter-spacing: .05em; text-transform: uppercase; font-weight: 600; color: var(--muted); margin-bottom: 10px; }

  /* method picker cards */
  .mcards { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; margin-bottom: 18px; }
  .mcard { border: 1px solid var(--border); border-radius: 13px; padding: 15px; background: #fff; text-align: left; cursor: pointer; transition: border-color .12s; }
  .mcard:hover { border-color: #cfcabf; }
  .mcard.on { border: 2px solid var(--clay); padding: 14px; }
  .mcard:disabled { cursor: default; opacity: .7; }
  .mc-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .mc-nm { font-size: 14px; font-weight: 600; color: var(--ink); }
  .star { color: var(--clay); font-weight: 700; }
  .mc-rad { flex: 0 0 auto; }
  .mc-sub { font-size: 12px; color: var(--muted); margin-top: 6px; line-height: 1.45; }

  /* cards */
  .card { border: 1px solid var(--border); border-radius: 13px; padding: 18px 20px; background: #fff; margin-bottom: 14px; }
  .card-h { margin-bottom: 10px; }
  .card-h h3 { font-size: 15px; font-weight: 600; color: var(--ink); }
  .card-h p { font-size: 12.5px; color: var(--muted); margin-top: 3px; line-height: 1.5; max-width: 620px; }
  .card-h a, .mline a, .ftxt a { color: var(--clay); }
  .cardsub { font-size: 12.5px; color: var(--muted); margin: 8px 0 0; line-height: 1.5; }
  .cardsub code, .steps code { background: var(--sand); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
  .hint { font-weight: 400; color: var(--muted); font-size: 11px; }

  /* steps */
  .steps { list-style: none; counter-reset: step; padding: 0; margin: 4px 0 0; }
  .steps > li { position: relative; padding: 0 0 16px 34px; border-left: 1px solid var(--line); margin-left: 11px; }
  .steps > li:last-child { border-left: none; padding-bottom: 0; }
  .steps > li::before { counter-increment: step; content: counter(step); position: absolute; left: -12px; top: -2px;
    width: 23px; height: 23px; border-radius: 50%; background: var(--sand); color: var(--ink);
    font-size: 12px; font-weight: 600; display: grid; place-items: center; border: 1px solid var(--line); }
  .steps > li b { font-size: 13.5px; color: var(--ink); }

  .ostoggle { display: inline-flex; gap: 0; margin-top: 8px; border: 1px solid var(--border); border-radius: 9px; overflow: hidden; }
  .oseg { height: 34px; padding: 0 16px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: var(--ink); }
  .oseg.on { background: var(--clay); color: #fff; }

  .tokrow { display: flex; align-items: center; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
  .tokbox { display: flex; align-items: center; gap: 10px; margin-top: 9px; background: #f4f3f0; border: 1px solid var(--border); border-radius: 9px; padding: 8px 11px; }
  .tokval { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; color: var(--ink); word-break: break-all; flex: 1; }
  .warn { font-size: 12px; color: #9a6a16; margin-top: 7px; }

  /* connection rows */
  .connbar { display: flex; align-items: center; gap: 11px; background: #ecf6f0; border: 1px solid #bfe0cd; border-radius: 10px; padding: 11px 14px; }
  .connbar .dot { width: 9px; height: 9px; border-radius: 50%; background: #2f8f5f; flex: 0 0 auto; }
  .connnm { flex: 1; font-size: 13.5px; color: #226844; }
  .connrow { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

  /* form */
  .fg { display: block; padding: 7px 0; }
  .fg span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; }
  .fg input { width: 100%; height: 40px; border: 1px solid var(--border); border-radius: 9px; padding: 0 12px; font-size: 13.5px; background: #fff; outline: none; box-sizing: border-box; }
  .fg input:focus { border-color: var(--clay); }
  .g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

  /* schedule */
  .schedrow { display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
  .sched-int { width: 200px; }
  .nextrun { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
  .nr-lbl { font-size: 11px; color: var(--muted); }
  .nr-val { font-size: 13.5px; color: var(--ink); font-weight: 500; }

  /* status */
  .statgrid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; }
  .stat { border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; background: #fff; }
  .s-lbl { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .s-val { font-size: 15px; font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 7px; }
  .pdot { width: 9px; height: 9px; border-radius: 50%; background: #cfcabf; flex: 0 0 auto; }
  .pdot.run { background: #2f8f5f; box-shadow: 0 0 0 0 rgba(47,143,95,.5); animation: pulse 1.4s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(47,143,95,.5); } 70% { box-shadow: 0 0 0 7px rgba(47,143,95,0); } 100% { box-shadow: 0 0 0 0 rgba(47,143,95,0); } }

  .loglbl { font-size: 11px; letter-spacing: .05em; text-transform: uppercase; font-weight: 600; color: var(--muted); margin: 16px 0 8px; }
  .loglist { border: 1px solid var(--border); border-radius: 10px; background: #faf9f7; max-height: 260px; overflow-y: auto; padding: 6px 0; }
  .logrow { display: flex; gap: 12px; padding: 4px 13px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; line-height: 1.5; }
  .lts { color: #9a958c; flex: 0 0 88px; }
  .lmsg { flex: 1; word-break: break-word; }

  /* buttons — 3-variant convention (primary / default / ghost) */
  .btn { height: 38px; padding: 0 16px; border-radius: 9px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid var(--border); background: #fff; color: var(--ink); }
  .btn.pri { background: var(--clay); color: #fff; border-color: var(--clay); }
  .btn.ghost { border: none; background: transparent; color: var(--muted); }
  .btn.link { height: auto; padding: 4px 0; text-decoration: underline; font-size: 12.5px; }
  .btn:disabled { opacity: .6; cursor: default; }

  .ftxt { font-size: 12.5px; color: var(--muted); }
  .ftxt.ok { color: #2f8f5f; } .ftxt.bad { color: #c0492f; }

  /* modal */
  .mscrim { position: fixed; inset: 0; z-index: 95; background: rgba(30,28,25,.4); display: grid; place-items: center; padding: 20px; }
  .mcard { background: #fff; border-radius: 14px; width: 100%; max-width: 420px; padding: 20px 22px; box-shadow: 0 18px 50px rgba(40,35,30,.3); }
  .mhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .mhead h3 { font-size: 16px; font-weight: 600; color: var(--ink); }
  .mx { border: none; background: transparent; font-size: 15px; color: var(--muted); cursor: pointer; padding: 4px; }
  .mline { font-size: 13.5px; color: var(--ink); margin: 6px 0; }
  .codebox { font-family: ui-monospace, Menlo, monospace; font-size: 26px; font-weight: 700; letter-spacing: .12em; text-align: center;
    background: #f4f3f0; border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin: 8px 0 10px; color: var(--ink); }

  @media (max-width: 760px) {
    .mcards { grid-template-columns: 1fr; }
    .statgrid { grid-template-columns: 1fr; }
    .g2 { grid-template-columns: 1fr; }
  }
</style>
