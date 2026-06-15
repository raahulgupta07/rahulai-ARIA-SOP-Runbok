<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>({ enabled: false, app_id: '', app_password: '', public_url: '', skip_auth: false, has_password: false });
  let saving = $state(false);
  let saved = $state(false);
  let err = $state('');

  onMount(async () => { try { cfg = { ...cfg, ...(await api.teamsConfig()) }; } catch (e: any) { err = e.message; } });

  let endpoint = $derived((cfg.public_url ? cfg.public_url.replace(/\/$/, '') : '<your-public-url>') + '/api/teams/messages');

  async function save() {
    saving = true; saved = false; err = '';
    try {
      const body: any = { enabled: cfg.enabled, app_id: cfg.app_id, public_url: cfg.public_url, skip_auth: cfg.skip_auth };
      if (cfg.app_password) body.app_password = cfg.app_password;
      const r = await api.teamsSaveConfig(body);
      cfg = { ...cfg, ...r, app_password: '' };
      saved = true; setTimeout(() => (saved = false), 2000);
    } catch (e: any) { err = e.message; } finally { saving = false; }
  }
  function copy(t: string) { navigator.clipboard?.writeText(t); }
  async function downloadManifest() {
    try {
      const m = await api.teamsManifest();
      const blob = new Blob([JSON.stringify(m, null, 2)], { type: 'application/json' });
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'manifest.json'; a.click();
    } catch (e: any) { err = e.message; }
  }
  async function downloadPackage() {
    try { await api.teamsManifestZip(); } catch (e: any) { err = e.message; }
  }
</script>

<div class="px-7 py-6 max-w-[900px]">
  {#if err}<div class="mb-4 text-sm" style="color:#c0492f">{err}</div>{/if}

  <Section title="Connection" desc="Run Aria as a bot inside Microsoft Teams (Azure Bot Service / Bot Framework).">
    {#snippet actions()}
      <span class="text-[12px]" style="color:{cfg.enabled ? '#3f8f5f' : 'var(--muted)'}">{cfg.enabled ? '● enabled' : 'disabled'}</span>
    {/snippet}
    <Row label="Enable Teams bot" hint="Turn on once Azure is configured below.">
      <Toggle bind:checked={cfg.enabled} />
    </Row>
    <Row label="Bot App ID" hint="Application (client) ID from your Azure app registration / bot.">
      <input class="inp" bind:value={cfg.app_id} placeholder="00000000-0000-0000-0000-000000000000" />
    </Row>
    <Row label="Bot client secret" hint={cfg.has_password ? 'A secret is saved. Enter a new value only to replace it.' : 'Client secret value from Azure.'}>
      <input class="inp" type="password" bind:value={cfg.app_password} placeholder={cfg.has_password ? '•••••••• (saved)' : 'secret value'} />
    </Row>
    <Row label="Public URL of Aria" hint="Public HTTPS base URL Teams can reach (prod ingress, or a dev tunnel).">
      <input class="inp" bind:value={cfg.public_url} placeholder="https://aria.yourco.com" />
    </Row>
    <Row label="Skip inbound auth (DEV ONLY)" hint="Bypass Bot Framework JWT check — for dev-tunnel testing only. Never leave on in production.">
      <Toggle bind:checked={cfg.skip_auth} />
    </Row>
    <div class="mt-3 flex items-center gap-3">
      <button class="btn pri" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      {#if saved}<span class="text-[12.5px]" style="color:#3f8f5f">Saved ✓</span>{/if}
    </div>
  </Section>

  <Section title="Messaging endpoint" desc="Paste this into your Azure Bot's “Messaging endpoint”.">
    <div class="codebox"><code>{endpoint}</code><button class="cbtn" onclick={() => copy(endpoint)}>Copy</button></div>
  </Section>

  <Section title="Teams app package" desc="Download the ready-to-upload package, or just the manifest.">
    <div class="flex items-center gap-3 flex-wrap">
      <button class="btn pri" onclick={downloadPackage}>Download app package (.zip)</button>
      <button class="btn" onclick={downloadManifest}>Download manifest.json</button>
    </div>
    <p class="text-[12px] mt-2" style="color:var(--muted)">The <b>.zip</b> bundles <code>manifest.json</code> + <code>color.png</code> + <code>outline.png</code> — upload it directly in Teams → Apps → Manage your apps → Upload a custom app. (Use the manifest button only if you want to supply your own icons.)</p>
  </Section>

  <Section title="Step-by-step setup">
    <ol class="guide">
      <li><b>Azure → App registration.</b> portal.azure.com → Microsoft Entra ID → App registrations → New registration. Copy the <b>Application (client) ID</b> → paste into <b>Bot App ID</b> above. Under Certificates &amp; secrets → New client secret → copy the <b>Value</b> → paste into <b>Bot client secret</b>.</li>
      <li><b>Azure → create the Bot.</b> Create a resource → <b>Azure Bot</b>. Use the App ID above (type: Multi-tenant or Single-tenant). After it's created, open the bot → <b>Configuration</b> → set <b>Messaging endpoint</b> to the URL shown above → Apply.</li>
      <li><b>Enable the Teams channel.</b> In the Azure Bot → <b>Channels</b> → add <b>Microsoft Teams</b> → agree → Apply.</li>
      <li><b>Make Aria reachable.</b> Aria must be on <b>public HTTPS</b>. For production use your ingress URL; for a quick test run a dev tunnel (e.g. <code>devtunnel host -p 8081</code> or <code>ngrok http 8081</code>) and put that HTTPS URL in <b>Public URL</b> above (and as the messaging endpoint).</li>
      <li><b>Fill + save</b> the App ID, secret, and Public URL above, then toggle <b>Enable Teams bot</b> → Save.</li>
      <li><b>Build the app package.</b> Click <b>Download manifest.json</b>, add the two icons, zip the three files.</li>
      <li><b>Upload to Teams.</b> Teams → Apps → Manage your apps → <b>Upload a custom app</b> → pick the zip. (If your org blocks custom uploads, an admin publishes it from the Teams Admin Center.)</li>
      <li><b>Use it.</b> Search “Aria” in Teams for a 1:1 chat, or <b>@mention</b> it in a channel. Ask a runbook question — Aria replies with the answer + source buttons.</li>
    </ol>
    <p class="note">Notes: Teams shows a single (non-streaming) reply; slow deep answers arrive a moment after a typing indicator. Identity is a shared “Teams” principal for now (per-user mapping is a later option). Turn <b>Skip inbound auth</b> OFF for production.</p>
  </Section>
</div>

<style>
  .inp { width: 320px; max-width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px; font-size: 13px; background: var(--cream); color: var(--ink); }
  .btn { font-size: 13px; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: var(--ink); cursor: pointer; }
  .btn.pri { background: var(--ink); color: var(--cream); border-color: var(--ink); }
  .btn:disabled { opacity: .6; }
  .codebox { display: flex; align-items: center; gap: 10px; background: #161616; border-radius: 10px; padding: 11px 14px; }
  .codebox code { color: #e8e4da; font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; flex: 1; word-break: break-all; }
  .cbtn { font-size: 12px; color: #cfcabf; border: 1px solid #3a352e; border-radius: 6px; padding: 4px 10px; background: transparent; cursor: pointer; flex: none; }
  .cbtn:hover { color: #fff; }
  .guide { display: flex; flex-direction: column; gap: 11px; counter-reset: g; padding-left: 0; }
  .guide li { list-style: none; position: relative; padding-left: 34px; font-size: 13.5px; line-height: 1.55; color: var(--ink); }
  .guide li::before { counter-increment: g; content: counter(g); position: absolute; left: 0; top: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--navpill); color: var(--ink); font-size: 12px; font-weight: 700; display: grid; place-items: center; }
  .guide code, .note code { background: var(--sand); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .note { margin-top: 16px; font-size: 12px; color: var(--muted); line-height: 1.6; }
</style>
