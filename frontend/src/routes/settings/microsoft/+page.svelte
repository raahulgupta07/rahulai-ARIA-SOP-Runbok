<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>({
    tenant_id: '', client_id: '', client_secret: '',
    has_secret: false, sp_enabled: true, od_enabled: true
  });
  let loading = $state(true);
  let saving = $state(false);
  let err = $state('');
  let testing = $state(false);
  let toast = $state('');                 // floating success/error toast
  let testMsg = $state<{ ok: boolean; detail: string } | null>(null);

  let connected = $derived(!!cfg.has_secret && !!cfg.tenant_id && !!cfg.client_id);

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  onMount(async () => {
    try { cfg = { ...cfg, ...(await api.msConfig()), client_secret: '' }; }
    catch (e: any) { err = e.message; } finally { loading = false; }
  });

  async function save() {
    saving = true; err = ''; testMsg = null;
    try {
      const body: any = {
        tenant_id: cfg.tenant_id, client_id: cfg.client_id,
        sp_enabled: cfg.sp_enabled, od_enabled: cfg.od_enabled
      };
      if (cfg.client_secret) body.client_secret = cfg.client_secret;
      const r = await api.msSaveConfig(body);
      cfg = { ...cfg, ...r, client_secret: '' };
      flash('Saved ✓ — live now');
    } catch (e: any) { err = e.message; flash('Error: ' + e.message); } finally { saving = false; }
  }

  async function test() {
    testing = true; testMsg = null; err = '';
    try { testMsg = await api.msTest(); }
    catch (e: any) { err = e.message; } finally { testing = false; }
  }

  async function clearSecret() {
    saving = true; err = ''; testMsg = null;
    try { const r = await api.msClearSecret(); cfg = { ...cfg, ...r, client_secret: '' }; flash('Secret cleared'); }
    catch (e: any) { err = e.message; } finally { saving = false; }
  }
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Connect one Azure app (app-only) to import documents from SharePoint and OneDrive.</p>
      <div class="flex items-center gap-3">
        <span class="text-xs" style="color:var(--muted)">{saving ? 'saving…' : ''}</span>
        <button class="btn" onclick={test} disabled={testing || !cfg.has_secret}>{testing ? 'Testing…' : 'Test'}</button>
        <button class="btn pri" onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
    </div>

    {#if err}<div class="errbar">{err}</div>{/if}

    <div class="formbody">
    <!-- brand / connection header -->
    <div class="brandhead">
      <span class="brand-ic">
        <svg width="22" height="22" viewBox="0 0 23 23" aria-hidden="true"><path fill="#f25022" d="M1 1h10v10H1z"/><path fill="#7fba00" d="M12 1h10v10H12z"/><path fill="#00a4ef" d="M1 12h10v10H1z"/><path fill="#ffb900" d="M12 12h10v10H12z"/></svg>
      </span>
      <div class="bmeta">
        <div class="bnm">Microsoft 365 connection</div>
        <div class="bsub">Set this up once. Choose the folder to import in Workspace → Add → Import from Microsoft 365.</div>
      </div>
      <span class="status" class:on={connected}>{connected ? '● Connected' : '○ Not configured'}</span>
    </div>

    <!-- credentials -->
    <div class="card">
      <div class="lbl">Azure app credentials</div>
      <div class="g2">
        <label class="fg"><span>Tenant ID</span>
          <input bind:value={cfg.tenant_id} placeholder="aaaaaaaa-bbbb-cccc-…" />
        </label>
        <label class="fg"><span>Client ID</span>
          <input bind:value={cfg.client_id} placeholder="application (client) id" />
        </label>
      </div>
      <label class="fg"><span>Client secret <span class="hint">{cfg.has_secret ? '— a secret is saved; enter a new value only to replace it' : '— created under Certificates & secrets'}</span></span>
        <input type="password" autocomplete="new-password" bind:value={cfg.client_secret} placeholder={cfg.has_secret ? '•••••••• (saved)' : 'paste client secret'} />
      </label>
      {#if cfg.has_secret}
        <button class="btn ghost link" onclick={clearSecret} disabled={saving}>Clear saved secret</button>
      {/if}
    </div>

    <!-- sources -->
    <div class="card">
      <div class="lbl">Sources</div>
      <p class="cardsub">Which Microsoft sources admins can import from. Each source's location and schedule are set in the Add menu.</p>
      <div class="srow">
        <div class="sr-meta">
          <div class="sr-nm">SharePoint</div>
          <div class="sr-sub">Import from SharePoint document libraries.</div>
        </div>
        <Toggle bind:checked={cfg.sp_enabled} />
      </div>
      <div class="srow">
        <div class="sr-meta">
          <div class="sr-nm">OneDrive</div>
          <div class="sr-sub">Import from a user's OneDrive.</div>
        </div>
        <Toggle bind:checked={cfg.od_enabled} />
      </div>
    </div>

    <div class="foot">
      <button class="btn" onclick={test} disabled={testing || !cfg.has_secret}>{testing ? 'Testing…' : 'Test credentials'}</button>
      <button class="btn pri" onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
      {#if testMsg}<span class="testmsg" class:ok={testMsg.ok}>{testMsg.ok ? '✓ ' : '✗ '}{testMsg.detail}</span>{/if}
    </div>

    <p class="note">Azure app needs <b>application</b> permissions <code>Sites.Read.All</code> (SharePoint) + <code>Files.Read.All</code> (OneDrive) with admin consent. Test credentials checks the <b>saved</b> values — click Save first. The secret is stored server-side and never shown back.</p>
    </div><!-- /.formbody -->
  </div>

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .formbody{max-width:720px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .errbar{margin-bottom:14px; font-size:13px; color:#c0492f; background:#fbeae6; border:1px solid #e6bdb2; border-radius:10px; padding:9px 13px;}
  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%); z-index:90;
    background:#1c1a17; color:#fff; font-size:13px; font-weight:500; padding:11px 18px; border-radius:11px;
    box-shadow:0 10px 30px rgba(40,35,30,.28); animation:tin .2s ease-out;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,8px);}to{opacity:1; transform:translate(-50%,0);}}

  /* brand / connection header */
  .brandhead{display:flex; align-items:center; gap:13px; border:1px solid var(--border); border-radius:13px; padding:15px; background:#fff; margin-bottom:14px;}
  .brand-ic{width:42px; height:42px; border-radius:10px; background:#f4f3f0; display:grid; place-items:center; flex:0 0 auto;}
  .bmeta{flex:1; min-width:0;}
  .bnm{font-size:15px; font-weight:600; color:var(--ink);}
  .bsub{font-size:12px; color:var(--muted); margin-top:2px;}
  .status{font-size:12px; font-weight:600; color:var(--muted); white-space:nowrap; flex:0 0 auto;}
  .status.on{color:#2f8f5f;}

  /* cards */
  .card{border:1px solid var(--border); border-radius:13px; padding:16px 18px; background:#fff; margin-bottom:14px;}
  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:12px;}
  .cardsub{font-size:12.5px; color:var(--muted); margin:-6px 0 12px;}

  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .fg{display:block; padding:7px 0;} .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus{border-color:var(--clay);}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}

  /* source toggle rows */
  .srow{display:flex; align-items:center; gap:13px; padding:11px 0; border-top:1px solid var(--border);}
  .srow:first-of-type{border-top:none;}
  .sr-meta{flex:1; min-width:0;}
  .sr-nm{font-size:14px; font-weight:600; color:var(--ink);}
  .sr-sub{font-size:12px; color:var(--muted); margin-top:2px;}

  .foot{display:flex; align-items:center; gap:12px; margin-top:2px;}
  .testmsg{font-size:12px; color:#c0492f;} .testmsg.ok{color:#2f8f5f;}

  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink);}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.ghost{border:none; background:transparent; color:var(--muted);}
  .btn.link{height:auto; padding:6px 0; margin-top:6px; text-decoration:underline; font-size:12.5px;}
  .btn:disabled{opacity:.6; cursor:default;}

  .note{margin-top:16px; font-size:12px; color:var(--muted); line-height:1.6;}
  .note code{background:var(--sand); border-radius:5px; padding:1px 6px; font-family:ui-monospace,Menlo,monospace; font-size:11.5px;}

  @media (max-width:640px){
    .g2{grid-template-columns:1fr;}
    .brandhead{flex-wrap:wrap;}
    .status{order:3; width:100%;}
  }
</style>
