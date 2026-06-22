<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let cfg = $state<any>({ enabled: false, app_id: '', app_password: '', public_url: '', skip_auth: false, has_password: false });
  let saving = $state(false);
  let toast = $state('');                 // floating success/error toast
  let err = $state('');

  onMount(async () => { try { cfg = { ...cfg, ...(await api.teamsConfig()) }; } catch (e: any) { err = e.message; } });

  let endpoint = $derived((cfg.public_url ? cfg.public_url.replace(/\/$/, '') : '<your-public-url>') + '/api/teams/messages');

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  async function save() {
    saving = true; err = '';
    try {
      const body: any = { enabled: cfg.enabled, app_id: cfg.app_id, public_url: cfg.public_url, skip_auth: cfg.skip_auth };
      if (cfg.app_password) body.app_password = cfg.app_password;
      const r = await api.teamsSaveConfig(body);
      cfg = { ...cfg, ...r, app_password: '' };
      flash('Saved ✓ — Teams settings updated');
    } catch (e: any) { err = e.message; flash('Error: ' + e.message); } finally { saving = false; }
  }
  function copy(t: string) { navigator.clipboard?.writeText(t); flash('Copied ✓'); }
  async function downloadPackage() {
    try { await api.teamsManifestZip(); } catch (e: any) { err = e.message; flash('Error: ' + e.message); }
  }

  // Azure guide modal
  let guideOpen = $state(false);
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Run Aria as a bot inside Microsoft Teams (Azure Bot Service / Bot Framework).</p>
      <div class="flex items-center gap-3">
        {#if cfg.enabled}<span class="enabled">● Enabled</span>{:else}<span class="text-xs" style="color:var(--muted)">Disabled</span>{/if}
        <button class="btn pri" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    </div>

    {#if err}<div class="mb-4 text-sm" style="color:#c0492f">{err}</div>{/if}

    <div class="formbody">
    <!-- Header -->
    <div class="hdr">
      <span class="orb">T</span>
      <div>
        <div class="hdr-ttl">Run Aria inside Teams</div>
        <div class="hdr-sub">Answer runbook questions from a 1:1 chat or an @mention in any channel.</div>
      </div>
      <label class="enbox">
        <input type="checkbox" bind:checked={cfg.enabled} />
        <span>Enable Teams bot</span>
      </label>
    </div>

    <!-- Bot fields -->
    <div class="lbl">Azure bot</div>
    <div class="g2">
      <label class="fg"><span>Bot App ID <span class="hint">— Application (client) ID</span></span>
        <input bind:value={cfg.app_id} placeholder="00000000-0000-0000-0000-000000000000" /></label>
      <label class="fg"><span>App password <span class="hint">{cfg.has_password ? '— saved; enter to replace' : '— client secret value'}</span></span>
        <input type="password" bind:value={cfg.app_password} placeholder={cfg.has_password ? '•••••••• (saved)' : 'secret value'} /></label>
    </div>
    <label class="fg"><span>Public URL of Aria <span class="hint">— HTTPS base URL Teams can reach</span></span>
      <input bind:value={cfg.public_url} placeholder="https://aria.yourco.com" /></label>
    <label class="checkrow">
      <input type="checkbox" bind:checked={cfg.skip_auth} />
      <span><b>Skip inbound auth (DEV ONLY)</b> — bypass Bot Framework JWT check for dev-tunnel testing. Never leave on in production.</span>
    </label>

    <!-- Messaging endpoint card -->
    <div class="lbl mt">Messaging endpoint</div>
    <div class="epcard">
      <div class="ep-lbl">Paste this into your Azure Bot's “Messaging endpoint”.</div>
      <div class="ep-row">
        <code>{endpoint}</code>
        <button class="cpy" onclick={() => copy(endpoint)} aria-label="Copy endpoint" title="Copy">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
        </button>
      </div>
    </div>

    <!-- Actions -->
    <div class="lbl mt">Get started</div>
    <div class="acts">
      <button class="btn pri big" onclick={downloadPackage}>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
        Download manifest.zip
      </button>
      <button class="btn big" onclick={() => (guideOpen = true)}>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></svg>
        8-step Azure guide
      </button>
    </div>
    <p class="hintp">The <b>.zip</b> bundles <code>manifest.json</code> + <code>color.png</code> + <code>outline.png</code> — upload it directly in Teams → Apps → Manage your apps → Upload a custom app.</p>
    </div><!-- /.formbody -->
  </div>

  <!-- ============ AZURE GUIDE MODAL ============ -->
  {#if guideOpen}
    <div class="scrim" onclick={() => (guideOpen = false)} role="presentation"></div>
    <div class="modal" role="dialog" aria-modal="true">
      <div class="m-head">
        <div class="m-ttl">8-step Azure setup</div>
        <button class="m-x" onclick={() => (guideOpen = false)} aria-label="Close">✕</button>
      </div>
      <div class="m-body">
        <ol class="guide">
          <li><b>Azure → App registration.</b> portal.azure.com → Microsoft Entra ID → App registrations → New registration. Copy the <b>Application (client) ID</b> → paste into <b>Bot App ID</b>. Under Certificates &amp; secrets → New client secret → copy the <b>Value</b> → paste into <b>App password</b>.</li>
          <li><b>Azure → create the Bot.</b> Create a resource → <b>Azure Bot</b>. Use the App ID above (type: Multi-tenant or Single-tenant). After it's created, open the bot → <b>Configuration</b> → set <b>Messaging endpoint</b> to the URL shown above → Apply.</li>
          <li><b>Enable the Teams channel.</b> In the Azure Bot → <b>Channels</b> → add <b>Microsoft Teams</b> → agree → Apply.</li>
          <li><b>Make Aria reachable.</b> Aria must be on <b>public HTTPS</b>. For production use your ingress URL; for a quick test run a dev tunnel (e.g. <code>devtunnel host -p 8081</code> or <code>ngrok http 8081</code>) and put that HTTPS URL in <b>Public URL</b> above (and as the messaging endpoint).</li>
          <li><b>Fill + save</b> the App ID, secret, and Public URL above, then toggle <b>Enable Teams bot</b> → Save.</li>
          <li><b>Build the app package.</b> Click <b>Download manifest.zip</b> — it already bundles the manifest + both icons.</li>
          <li><b>Upload to Teams.</b> Teams → Apps → Manage your apps → <b>Upload a custom app</b> → pick the zip. (If your org blocks custom uploads, an admin publishes it from the Teams Admin Center.)</li>
          <li><b>Use it.</b> Search “Aria” in Teams for a 1:1 chat, or <b>@mention</b> it in a channel. Ask a runbook question — Aria replies with the answer + source buttons.</li>
        </ol>
        <p class="note">Notes: Teams shows a single (non-streaming) reply; slow deep answers arrive a moment after a typing indicator. Identity is a shared “Teams” principal for now. Turn <b>Skip inbound auth</b> OFF for production.</p>
      </div>
      <div class="m-foot">
        <span></span>
        <button class="btn pri" onclick={() => (guideOpen = false)}>Got it</button>
      </div>
    </div>
  {/if}

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .formbody{max-width:720px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .enabled{font-size:12px; font-weight:600; color:#2f8f5f;}
  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%); z-index:90;
    background:#1c1a17; color:#fff; font-size:13px; font-weight:500; padding:11px 18px; border-radius:11px;
    box-shadow:0 10px 30px rgba(40,35,30,.28); animation:tin .2s ease-out;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,8px);}to{opacity:1; transform:translate(-50%,0);}}

  /* header */
  .hdr{display:flex; align-items:center; gap:14px; border:1px solid var(--border); border-radius:14px; padding:16px 18px; background:#fff; margin-bottom:20px;}
  .orb{width:46px; height:46px; border-radius:12px; background:#5059c9; color:#fff; font-weight:800; font-size:22px; display:grid; place-items:center; flex:0 0 auto;}
  .hdr-ttl{font-size:15.5px; font-weight:600; color:var(--ink);}
  .hdr-sub{font-size:12.5px; color:var(--muted); margin-top:2px;}
  .enbox{margin-left:auto; display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600; color:var(--ink); cursor:pointer; white-space:nowrap;}
  .enbox input{width:16px; height:16px; accent-color:var(--clay); cursor:pointer;}

  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:10px;}
  .lbl.mt{margin-top:22px;}

  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .fg{display:block; padding:7px 0;} .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus{border-color:var(--clay);}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}

  .checkrow{display:flex; align-items:flex-start; gap:10px; margin-top:8px; font-size:12.5px; line-height:1.5; color:var(--muted); cursor:pointer;}
  .checkrow input{width:16px; height:16px; margin-top:1px; accent-color:var(--clay); flex:0 0 auto; cursor:pointer;}
  .checkrow b{color:var(--ink);}

  /* endpoint card */
  .epcard{border:1px solid var(--border); border-radius:13px; padding:14px 16px; background:var(--cream);}
  .ep-lbl{font-size:12.5px; color:var(--muted); margin-bottom:9px;}
  .ep-row{display:flex; align-items:center; gap:10px;}
  .ep-row code{flex:1; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; color:var(--ink); word-break:break-all;}
  .cpy{width:34px; height:34px; flex:0 0 auto; border-radius:8px; border:1px solid var(--border); background:#fff; color:var(--muted); cursor:pointer; display:grid; place-items:center;}
  .cpy:hover{color:var(--ink); border-color:#cfcabf;}

  /* action buttons */
  .acts{display:flex; gap:12px; flex-wrap:wrap;}
  .hintp{font-size:12px; color:var(--muted); margin-top:10px; line-height:1.55;}
  .hintp code{background:var(--sand); border-radius:5px; padding:1px 6px; font-family:ui-monospace,Menlo,monospace; font-size:11.5px;}

  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink); display:inline-flex; align-items:center; gap:8px;}
  .btn.big{flex:1; min-width:200px; height:46px; justify-content:center; font-weight:600;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn:disabled{opacity:.6;}

  /* azure guide modal */
  .scrim{position:fixed; inset:0; background:rgba(28,26,23,.45); z-index:80;}
  .modal{position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); z-index:81; width:min(560px,calc(100vw - 32px)); max-height:88vh; display:flex; flex-direction:column;
    background:#fff; border-radius:16px; box-shadow:0 24px 60px rgba(28,26,23,.32); animation:min .16s ease-out; overflow:hidden;}
  @keyframes min{from{opacity:0; transform:translate(-50%,-46%);}to{opacity:1; transform:translate(-50%,-50%);}}
  .m-head{display:flex; align-items:center; justify-content:space-between; padding:15px 20px; border-bottom:1px solid var(--border);}
  .m-ttl{font-size:15px; font-weight:600; color:var(--ink);}
  .m-x{width:30px; height:30px; border-radius:8px; border:none; background:transparent; color:var(--muted); cursor:pointer; font-size:14px;}
  .m-x:hover{background:#f0efed;}
  .m-body{padding:18px 20px; overflow-y:auto;}
  .m-foot{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:13px 20px; border-top:1px solid var(--border);}

  .guide{display:flex; flex-direction:column; gap:11px; counter-reset:g; padding-left:0;}
  .guide li{list-style:none; position:relative; padding-left:34px; font-size:13px; line-height:1.55; color:var(--ink);}
  .guide li::before{counter-increment:g; content:counter(g); position:absolute; left:0; top:0; width:22px; height:22px; border-radius:50%; background:var(--navpill,#f0efed); color:var(--ink); font-size:12px; font-weight:700; display:grid; place-items:center;}
  .guide code, .note code{background:var(--sand); border-radius:5px; padding:1px 6px; font-family:ui-monospace,Menlo,monospace; font-size:12px;}
  .note{margin-top:16px; font-size:12px; color:var(--muted); line-height:1.6;}

  @media (max-width:640px){
    .g2{grid-template-columns:1fr;}
    .acts{flex-direction:column;}
    .btn.big{width:100%;}
    .modal{top:auto; bottom:0; left:0; transform:none; width:100%; max-width:100%; max-height:92vh; border-radius:18px 18px 0 0; animation:sheet .2s ease-out;}
    @keyframes sheet{from{transform:translateY(100%);}to{transform:translateY(0);}}
  }
</style>
