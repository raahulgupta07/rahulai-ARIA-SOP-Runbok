<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>({
    tenant_id: '', client_id: '', client_secret: '',
    has_secret: false, sp_enabled: true, od_enabled: true
  });
  let loading = $state(true);
  let saving = $state(false);
  let saved = $state(false);
  let err = $state('');
  let testing = $state(false);
  let testMsg = $state<{ ok: boolean; detail: string } | null>(null);

  onMount(async () => {
    try { cfg = { ...cfg, ...(await api.msConfig()), client_secret: '' }; }
    catch (e: any) { err = e.message; } finally { loading = false; }
  });

  async function save() {
    saving = true; saved = false; err = ''; testMsg = null;
    try {
      const body: any = {
        tenant_id: cfg.tenant_id, client_id: cfg.client_id,
        sp_enabled: cfg.sp_enabled, od_enabled: cfg.od_enabled
      };
      if (cfg.client_secret) body.client_secret = cfg.client_secret;
      const r = await api.msSaveConfig(body);
      cfg = { ...cfg, ...r, client_secret: '' };
      saved = true; setTimeout(() => (saved = false), 2000);
    } catch (e: any) { err = e.message; } finally { saving = false; }
  }

  async function test() {
    testing = true; testMsg = null; err = '';
    try { testMsg = await api.msTest(); }
    catch (e: any) { err = e.message; } finally { testing = false; }
  }

  async function clearSecret() {
    saving = true; err = ''; testMsg = null;
    try { const r = await api.msClearSecret(); cfg = { ...cfg, ...r, client_secret: '' }; }
    catch (e: any) { err = e.message; } finally { saving = false; }
  }
</script>

<div class="px-7 py-6 max-w-[900px]">
  {#if err}<div class="mb-4 text-sm" style="color:#c0492f">{err}</div>{/if}

  <Section title="Microsoft 365" desc="Connect one Azure app (app-only) so the admin can import documents from SharePoint libraries and OneDrive. Set this up once here; choose which folder to import in Workspace → Add → Import from Microsoft 365.">
    {#snippet actions()}
      <span class="text-[12px]" style="color:{cfg.has_secret ? '#3f8f5f' : 'var(--muted)'}">{cfg.has_secret ? '● connected' : 'not configured'}</span>
    {/snippet}

    <Row label="Tenant ID" hint="Directory (tenant) ID from your Azure app registration.">
      <input class="inp" bind:value={cfg.tenant_id} placeholder="aaaaaaaa-bbbb-cccc-…" />
    </Row>
    <Row label="Client ID" hint="Application (client) ID from your Azure app registration.">
      <input class="inp" bind:value={cfg.client_id} placeholder="application (client) id" />
    </Row>
    <Row label="Client secret" hint={cfg.has_secret ? 'A secret is saved. Enter a new value only to replace it.' : 'App client secret value (created under Certificates & secrets).'}>
      <input class="inp" type="password" autocomplete="new-password" bind:value={cfg.client_secret} placeholder={cfg.has_secret ? '•••••••• (saved)' : 'paste client secret'} />
    </Row>
  </Section>

  <Section title="Sources" desc="Which Microsoft sources admins can import from. Each source's location and schedule are set in the Add menu.">
    <Row label="SharePoint" hint="Import from SharePoint document libraries.">
      <Toggle bind:checked={cfg.sp_enabled} />
    </Row>
    <Row label="OneDrive" hint="Import from a user's OneDrive.">
      <Toggle bind:checked={cfg.od_enabled} />
    </Row>
  </Section>

  <div class="flex items-center gap-3 mt-2">
    <button class="btn" onclick={test} disabled={testing || !cfg.has_secret}>{testing ? 'Testing…' : 'Test credentials'}</button>
    <button class="btn pri" onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
    {#if cfg.has_secret}<button class="btn" onclick={clearSecret} disabled={saving}>Clear secret</button>{/if}
    {#if saved}<span class="text-[12px]" style="color:#3f8f5f">✓ saved · live now</span>{/if}
    {#if testMsg}<span class="text-[12px]" style="color:{testMsg.ok ? '#3f8f5f' : '#c0492f'}">{testMsg.ok ? '✓ ' : '✗ '}{testMsg.detail}</span>{/if}
  </div>
  <p class="note">Azure app needs <b>application</b> permissions <code>Sites.Read.All</code> (SharePoint) + <code>Files.Read.All</code> (OneDrive) with admin consent. Test credentials checks the <b>saved</b> values — click Save first. The secret is stored server-side and never shown back.</p>
</div>

<style>
  .inp { width: 320px; max-width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px; font-size: 13px; background: var(--cream); color: var(--ink); }
  .btn { font-size: 13px; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: var(--ink); cursor: pointer; }
  .btn.pri { background: var(--ink); color: var(--cream); border-color: var(--ink); }
  .btn:disabled { opacity: .6; }
  .note { margin-top: 14px; font-size: 12px; color: var(--muted); line-height: 1.6; }
  .note code { background: var(--sand); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
</style>
