<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

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
  let toast = $state('');                 // floating success/error toast
  let clean = $state('');                 // snapshot of last-saved config

  // unsaved-changes indicator: current config differs from the last save
  let dirty = $derived(!!clean && JSON.stringify(snap()) !== clean);

  function snap() {
    // secret intentionally excluded — never compared/echoed
    return {
      backend: cfg.backend, bucket: cfg.bucket, region: cfg.region,
      endpoint_url: cfg.endpoint_url, access_key_id: cfg.access_key_id,
      prefix: cfg.prefix, import_prefix: cfg.import_prefix,
      force_path_style: cfg.force_path_style, presign: cfg.presign,
      secret_touched: !!cfg.secret_access_key
    };
  }

  onMount(async () => {
    try { cfg = { ...cfg, ...(await api.storageConfig()), secret_access_key: '' }; }
    catch (e: any) { err = e.message; flash('Error: ' + e.message); }
    finally { loading = false; clean = JSON.stringify(snap()); }
  });

  let isS3 = $derived(cfg.backend === 's3');

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  function pickBackend(b: 'local' | 's3') { cfg.backend = b; }

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
      clean = JSON.stringify(snap());
      saved = true; setTimeout(() => (saved = false), 2000);
      flash('Saved ✓ — storage backend live now');
    } catch (e: any) { err = e.message; flash('Error: ' + e.message); } finally { saving = false; }
  }

  async function test() {
    testing = true; testMsg = null; err = '';
    try { testMsg = await api.storageTest(); }
    catch (e: any) { err = e.message; flash('Error: ' + e.message); } finally { testing = false; }
  }
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Where uploaded files and rendered page images live — text, Q&amp;A and facts always stay in the database.</p>
      <div class="flex items-center gap-3">
        {#if dirty}<span class="unsaved">● Unsaved changes</span>{/if}
        <button class="btn" onclick={test} disabled={testing || !isS3 || !cfg.bucket}>{testing ? 'Testing…' : 'Test connection'}</button>
        <button class="btn pri" class:nudge={dirty} onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
    </div>

    {#if err}<div class="mb-4 text-sm" style="color:#c0492f">{err}</div>{/if}

    <!-- backend selector cards -->
    <div class="lbl">Storage backend</div>
    <div class="bcards">
      <button class="bcard" class:on={cfg.backend === 'local'} onclick={() => pickBackend('local')}>
        <div class="bc-top">
          <span class="bc-ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M4 7c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2z"/><path d="M4 7v10c0 1.1 3.6 2 8 2s8-.9 8-2V7"/><path d="M4 12c0 1.1 3.6 2 8 2s8-.9 8-2"/></svg></span>
          {#if cfg.backend === 'local'}
            <svg class="bc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="var(--clay)"/><path d="m8 12 2.5 2.5L16 9" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {:else}
            <svg class="bc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#cfcabf" stroke-width="1.6"/></svg>
          {/if}
        </div>
        <div class="bc-nm">Local disk</div>
        <div class="bc-sub">Simplest. Files live on the container volume.</div>
      </button>

      <button class="bcard" class:on={cfg.backend === 's3'} onclick={() => pickBackend('s3')}>
        <div class="bc-top">
          <span class="bc-ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M5 18a3.5 3.5 0 0 1-.5-7A5 5 0 0 1 14 9.5a4 4 0 0 1 .9 8H6.5"/><path d="M9 13v5M12 11v7M15 13v5"/></svg></span>
          {#if cfg.backend === 's3'}
            <svg class="bc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="var(--clay)"/><path d="m8 12 2.5 2.5L16 9" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {:else}
            <svg class="bc-rad" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#cfcabf" stroke-width="1.6"/></svg>
          {/if}
        </div>
        <div class="bc-nm">S3 · MinIO</div>
        <div class="bc-sub">Durable, survives a wipe, scales across instances.</div>
      </button>
    </div>

    <!-- S3 settings — dimmed/disabled unless s3 -->
    <div class="card" class:dim={!isS3}>
      <div class="card-h">
        <h3>S3 / MinIO settings</h3>
        <p>Created automatically on save if the bucket doesn't exist. Set an Endpoint URL only for MinIO or other S3-compatible stores.</p>
      </div>

      <div class="g2">
        <label class="fg"><span>Bucket</span>
          <input bind:value={cfg.bucket} placeholder="my-aria-docs" disabled={!isS3} /></label>
        <label class="fg"><span>Region <span class="hint">— ignored by most MinIO</span></span>
          <input bind:value={cfg.region} placeholder="us-east-1" disabled={!isS3} /></label>
      </div>

      <label class="fg"><span>Endpoint URL <span class="hint">— blank = real AWS S3</span></span>
        <input bind:value={cfg.endpoint_url} placeholder="(blank = AWS) e.g. http://minio:9000" disabled={!isS3} /></label>

      <div class="g2">
        <label class="fg"><span>Access key ID</span>
          <input bind:value={cfg.access_key_id} placeholder="AKIA… / minioadmin" disabled={!isS3} /></label>
        <label class="fg"><span>Secret access key</span>
          <input type="password" bind:value={cfg.secret_access_key} disabled={!isS3}
            placeholder={cfg.has_secret ? '•••••••• (saved)' : 'secret value'} />
          <span class="fg-h">{cfg.has_secret ? 'A secret is saved. Enter a new value only to replace it.' : 'AWS secret, or MinIO root password.'}</span></label>
      </div>

      <div class="g2">
        <label class="fg"><span>Managed prefix <span class="hint">— our files</span></span>
          <input bind:value={cfg.prefix} placeholder="docsensei/" disabled={!isS3} /></label>
        <label class="fg"><span>Import prefix <span class="hint">— drop docs here</span></span>
          <input bind:value={cfg.import_prefix} placeholder="inbox-drop/" disabled={!isS3} /></label>
      </div>

      <div class="tog">
        <div class="tog-row">
          <div class="tog-meta">
            <div class="tog-nm">Path-style addressing</div>
            <div class="tog-sub">Required for MinIO. Leave off for AWS S3.</div>
          </div>
          <label class="sw"><input type="checkbox" bind:checked={cfg.force_path_style} disabled={!isS3} /><span class="track"></span></label>
        </div>
        <div class="tog-row">
          <div class="tog-meta">
            <div class="tog-nm">Presigned image serving</div>
            <div class="tog-sub">Browser pulls page images straight from S3 (app serves zero bytes). Needs the bucket reachable from users' browsers.</div>
          </div>
          <label class="sw"><input type="checkbox" bind:checked={cfg.presign} disabled={!isS3} /><span class="track"></span></label>
        </div>
      </div>

      {#if isS3}
        <div class="note">
          Bucket layout: <code>{cfg.import_prefix || 'inbox-drop/'}</code> ← you drop docs here ·
          <code>{cfg.prefix || 'docsensei/'}inbox/ → processed/</code> · <code>{cfg.prefix || 'docsensei/'}pages/doc_&lt;id&gt;/</code>
        </div>
      {/if}
    </div>

    <!-- footer -->
    <div class="foot">
      <button class="btn" onclick={test} disabled={testing || !isS3 || !cfg.bucket}>{testing ? 'Testing…' : 'Test connection'}</button>
      <button class="btn pri" onclick={save} disabled={saving || loading}>{saving ? 'Saving…' : 'Save'}</button>
      {#if saved}<span class="ftxt ok">✓ saved · live now</span>{/if}
      {#if testMsg}<span class="ftxt" class:ok={testMsg.ok} class:bad={!testMsg.ok}>{testMsg.ok ? '✓ ' : '✗ '}{testMsg.detail}</span>{/if}
    </div>
    <p class="note">Test connection checks the <b>saved</b> config — click Save first. Switching an S3-ingested library back to Local breaks its page-image links; keep the backend you ingested under (or re-ingest).</p>
  </div>

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px; max-width:560px; line-height:1.5;}
  .unsaved{font-size:12px; font-weight:600; color:#9a6a16;}
  .btn.nudge{box-shadow:0 0 0 3px rgba(194,104,63,.22);}
  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%); z-index:90;
    background:#1c1a17; color:#fff; font-size:13px; font-weight:500; padding:11px 18px; border-radius:11px;
    box-shadow:0 10px 30px rgba(40,35,30,.28); animation:tin .2s ease-out;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,8px);}to{opacity:1; transform:translate(-50%,0);}}

  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:10px;}

  /* backend selector cards */
  .bcards{display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin-bottom:18px;}
  .bcard{border:1px solid var(--border); border-radius:13px; padding:15px; background:#fff; text-align:left; cursor:pointer; transition:border-color .12s, box-shadow .12s;}
  .bcard:hover{border-color:#cfcabf;}
  .bcard.on{border:2px solid var(--clay); padding:14px;}
  .bc-top{display:flex; align-items:center; justify-content:space-between;}
  .bc-ic{width:36px; height:36px; border-radius:9px; background:#f4f3f0; display:grid; place-items:center;}
  .bc-rad{flex:0 0 auto;}
  .bc-nm{font-size:14px; font-weight:600; margin-top:11px; color:var(--ink);}
  .bc-sub{font-size:12px; color:var(--muted); margin-top:2px; line-height:1.45;}

  /* S3 settings card */
  .card{border:1px solid var(--border); border-radius:13px; padding:18px 20px; background:#fff; transition:opacity .15s;}
  .card.dim{opacity:.55;}
  .card-h{margin-bottom:6px;}
  .card-h h3{font-size:15px; font-weight:600; color:var(--ink);}
  .card-h p{font-size:12.5px; color:var(--muted); margin-top:3px; line-height:1.5; max-width:560px;}

  .fg{display:block; padding:7px 0;}
  .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus{border-color:var(--clay);}
  .fg input:disabled{background:#f4f3f0; cursor:not-allowed;}
  .fg-h{display:block; font-size:11px; color:var(--muted); margin-top:5px;}
  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}

  /* toggle rows */
  .tog{margin-top:6px; border-top:1px solid var(--border); padding-top:6px;}
  .tog-row{display:flex; align-items:center; justify-content:space-between; gap:16px; padding:11px 0; border-bottom:1px solid var(--border);}
  .tog-row:last-child{border-bottom:none;}
  .tog-meta{min-width:0;}
  .tog-nm{font-size:13.5px; font-weight:500; color:var(--ink);}
  .tog-sub{font-size:12px; color:var(--muted); margin-top:2px; line-height:1.45; max-width:520px;}
  .sw{position:relative; flex:0 0 auto; cursor:pointer;}
  .sw input{position:absolute; opacity:0; width:0; height:0;}
  .sw .track{display:block; width:40px; height:23px; border-radius:999px; background:#d7d3ca; transition:background .15s;}
  .sw .track::after{content:''; position:absolute; top:3px; left:3px; width:17px; height:17px; border-radius:50%; background:#fff; box-shadow:0 1px 2px rgba(0,0,0,.2); transition:transform .15s;}
  .sw input:checked + .track{background:var(--clay);}
  .sw input:checked + .track::after{transform:translateX(17px);}
  .sw input:disabled + .track{opacity:.5; cursor:not-allowed;}

  /* footer */
  .foot{display:flex; align-items:center; gap:12px; margin-top:16px;}
  .ftxt{font-size:12px; color:var(--muted);}
  .ftxt.ok{color:#3f8f5f;} .ftxt.bad{color:#c0492f;}

  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink);}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn:disabled{opacity:.6; cursor:not-allowed;}

  .note{margin-top:14px; font-size:12px; color:var(--muted); line-height:1.6;}
  .note code{background:var(--sand); border-radius:5px; padding:1px 6px; font-family:ui-monospace, Menlo, monospace; font-size:11.5px;}

  @media (max-width:640px){
    .bcards{grid-template-columns:1fr;}
    .g2{grid-template-columns:1fr;}
    .foot{flex-wrap:wrap;}
  }
</style>
