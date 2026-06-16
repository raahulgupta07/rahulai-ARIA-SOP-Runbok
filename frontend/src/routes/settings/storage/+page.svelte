<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>({
    backend: 'local', bucket: '', region: 'us-east-1', endpoint_url: '',
    access_key_id: '', secret_access_key: '', prefix: 'docsensei/',
    import_prefix: 'inbox-drop/', force_path_style: true, presign: false, has_secret: false
  });
  let loading = $state(true);
  let saving = $state(false);
  let saved = $state(false);
  let err = $state('');
  let testing = $state(false);
  let testMsg = $state<{ ok: boolean; detail: string } | null>(null);

  onMount(async () => {
    try { cfg = { ...cfg, ...(await api.storageConfig()), secret_access_key: '' }; }
    catch (e: any) { err = e.message; } finally { loading = false; }
  });

  let isS3 = $derived(cfg.backend === 's3');

  async function save() {
    saving = true; saved = false; err = ''; testMsg = null;
    try {
      const body: any = {
        backend: cfg.backend, bucket: cfg.bucket, region: cfg.region,
        endpoint_url: cfg.endpoint_url, access_key_id: cfg.access_key_id,
        prefix: cfg.prefix, import_prefix: cfg.import_prefix,
        force_path_style: cfg.force_path_style, presign: cfg.presign
      };
      if (cfg.secret_access_key) body.secret_access_key = cfg.secret_access_key;
      const r = await api.storageSave(body);
      cfg = { ...cfg, ...r, secret_access_key: '' };
      saved = true; setTimeout(() => (saved = false), 2000);
    } catch (e: any) { err = e.message; } finally { saving = false; }
  }

  async function test() {
    testing = true; testMsg = null; err = '';
    try { testMsg = await api.storageTest(); }
    catch (e: any) { err = e.message; } finally { testing = false; }
  }
</script>

<div class="px-7 py-6 max-w-[900px]">
  {#if err}<div class="mb-4 text-sm" style="color:#c0492f">{err}</div>{/if}

  <Section title="Storage" desc="Where uploaded files and rendered page images live. Text, Q&A and facts always stay in the database — this only moves the binary files.">
    {#snippet actions()}
      <span class="text-[12px]" style="color:{isS3 ? '#3f8f5f' : 'var(--muted)'}">{isS3 ? '● S3 / MinIO' : 'local disk'}</span>
    {/snippet}

    <Row label="Backend" hint="Local disk is simplest. S3/MinIO is durable, survives a container wipe, and scales across instances.">
      <select class="inp" bind:value={cfg.backend}>
        <option value="local">Local disk</option>
        <option value="s3">S3 / MinIO</option>
      </select>
    </Row>

    {#if isS3}
      <Row label="Bucket" hint="Created automatically on save if it doesn't exist.">
        <input class="inp" bind:value={cfg.bucket} placeholder="my-aria-docs" />
      </Row>
      <Row label="Region" hint="AWS region (ignored by most MinIO setups).">
        <input class="inp" bind:value={cfg.region} placeholder="us-east-1" />
      </Row>
      <Row label="Endpoint URL" hint="Leave BLANK for real AWS S3. Set it for MinIO or any S3-compatible store (e.g. http://minio:9000).">
        <input class="inp" bind:value={cfg.endpoint_url} placeholder="(blank = AWS)" />
      </Row>
      <Row label="Access key ID" hint="AWS access key, or MinIO root user.">
        <input class="inp" bind:value={cfg.access_key_id} placeholder="AKIA… / minioadmin" />
      </Row>
      <Row label="Secret access key" hint={cfg.has_secret ? 'A secret is saved. Enter a new value only to replace it.' : 'AWS secret, or MinIO root password.'}>
        <input class="inp" type="password" bind:value={cfg.secret_access_key} placeholder={cfg.has_secret ? '•••••••• (saved)' : 'secret value'} />
      </Row>
      <Row label="Path-style addressing" hint="Required for MinIO. Leave off for AWS S3.">
        <Toggle bind:checked={cfg.force_path_style} />
      </Row>
      <Row label="Presigned image serving" hint="Browser pulls page images straight from S3 (the app serves zero bytes). Faster + cheaper for high traffic; needs the bucket reachable from users' browsers.">
        <Toggle bind:checked={cfg.presign} />
      </Row>
    {/if}
  </Section>

  {#if isS3}
    <Section title="Layout & import" desc="How keys are namespaced in the bucket, and where bulk-import looks for documents.">
      <Row label="Managed prefix" hint="Our files: inbox/ → processed/|failed/, pages/doc_<id>/.">
        <input class="inp" bind:value={cfg.prefix} placeholder="docsensei/" />
      </Row>
      <Row label="Import prefix" hint="Drop documents here, then use Workspace → Add → Import from S3.">
        <input class="inp" bind:value={cfg.import_prefix} placeholder="inbox-drop/" />
      </Row>
      <div class="note">
        Bucket layout: <code>{cfg.import_prefix || 'inbox-drop/'}</code> ← you drop docs here ·
        <code>{cfg.prefix || 'docsensei/'}inbox/ → processed/</code> · <code>{cfg.prefix || 'docsensei/'}pages/doc_&lt;id&gt;/</code>
      </div>
    </Section>
  {/if}

  <div class="flex items-center gap-3 mt-2">
    <button class="btn" onclick={test} disabled={testing || !isS3 || !cfg.bucket}>{testing ? 'Testing…' : 'Test connection'}</button>
    <button class="btn pri" onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
    {#if saved}<span class="text-[12px]" style="color:#3f8f5f">✓ saved · live now</span>{/if}
    {#if testMsg}<span class="text-[12px]" style="color:{testMsg.ok ? '#3f8f5f' : '#c0492f'}">{testMsg.ok ? '✓ ' : '✗ '}{testMsg.detail}</span>{/if}
  </div>
  <p class="note">Test connection checks the <b>saved</b> config — click Save first. Switching an S3-ingested library back to Local breaks its page-image links; keep the backend you ingested under (or re-ingest).</p>
</div>

<style>
  .inp { width: 320px; max-width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px; font-size: 13px; background: var(--cream); color: var(--ink); }
  .btn { font-size: 13px; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: var(--ink); cursor: pointer; }
  .btn.pri { background: var(--ink); color: var(--cream); border-color: var(--ink); }
  .btn:disabled { opacity: .6; }
  .note { margin-top: 14px; font-size: 12px; color: var(--muted); line-height: 1.6; }
  .note code { background: var(--sand); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
</style>
