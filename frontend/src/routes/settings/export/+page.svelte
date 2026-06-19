<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let prev = $state<any>(null);
  let loading = $state(true);
  let busy = $state(false);
  let err = $state('');

  function fmtBytes(n: number) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }

  async function load() {
    loading = true; err = '';
    try { prev = await api.okfPreview(); }
    catch (e: any) { err = e.message || 'preview failed'; }
    finally { loading = false; }
  }
  let withImages = $state(false);
  async function download() {
    busy = true; err = '';
    try { await api.okfExport(withImages); }
    catch (e: any) { err = e.message || 'export failed'; }
    finally { busy = false; }
  }

  // ---- import ----
  let impFile = $state<File | null>(null);
  let impUrl = $state('');
  let impDry = $state<any>(null);
  let impDone = $state<any>(null);
  let impBusy = $state(false);
  let impErr = $state('');
  function pick(e: Event) {
    impFile = (e.target as HTMLInputElement).files?.[0] || null;
    impDry = null; impDone = null; impErr = '';
  }
  const canImport = $derived(!!impFile || impUrl.trim().length > 0);
  async function run(dry: boolean) {
    if (!canImport) return;
    impBusy = true; impErr = ''; if (dry) impDone = null;
    try {
      const r = impFile ? await api.okfImport(impFile, dry) : await api.okfImportUrl(impUrl.trim(), dry);
      if (dry) impDry = r; else { impDone = r; impDry = null; await load(); }
    } catch (e: any) { impErr = e.message || (dry ? 'preview failed' : 'import failed'); }
    finally { impBusy = false; }
  }
  const preview2 = () => run(true);
  const commit = () => run(false);
  onMount(load);
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <p class="ttlsub">Move Aria's brain in and out as a vendor-neutral Open Knowledge Format bundle.</p>

    {#if err}<div class="err">{err}</div>{/if}

    <div class="grid">
      <!-- ============ EXPORT ============ -->
      <div class="card">
        <div class="c-top">
          <span class="c-ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg></span>
        </div>
        <div class="c-nm">Export</div>
        <div class="c-sub">Download the entire knowledge base as plain markdown + YAML frontmatter. Opens in any editor, renders on GitHub, version-controls in git, re-imports anywhere.</div>

        {#if loading}
          <div class="muted">Reading the knowledge base…</div>
        {:else if prev}
          <div class="chips">
            <span class="chip"><b>{prev.documents}</b> documents</span>
            <span class="chip"><b>{prev.facts}</b> facts</span>
            <span class="chip"><b>{prev.count}</b> files</span>
            <span class="chip"><b>{fmtBytes(prev.total_bytes)}</b> size</span>
            <span class="chip"><b>v{prev.okf_version}</b> OKF</span>
          </div>

          <label class="imgopt">
            <input type="checkbox" bind:checked={withImages} />
            Include page images <span class="hint">— larger, keeps answer-with-page on re-import</span>
          </label>

          <button class="btn pri full" onclick={download} disabled={busy}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
            {busy ? 'Building…' : 'Export bundle (.tar.gz)'}
          </button>

          <div class="tree">
            {#each prev.files as f}
              <div class="trow"><span class="path">{f.path}</span><span class="sz">{fmtBytes(f.bytes)}</span></div>
            {/each}
          </div>
        {/if}
      </div>

      <!-- ============ IMPORT ============ -->
      <div class="card">
        <div class="c-top">
          <span class="c-ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M5 3h14"/></svg></span>
        </div>
        <div class="c-nm">Import</div>
        <div class="c-sub">Feed an OKF bundle (.tar.gz / .zip) straight in — upload a file or paste an https / GitHub repo URL. The markdown body is the knowledge, so there's no vision pass and no LLM cost. Imported facts land in review (pending) so they never override your docs.</div>

        {#if impErr}<div class="err">{impErr}</div>{/if}

        <label class="drop">
          <input type="file" accept=".tar.gz,.tgz,.tar,.zip,application/gzip,application/zip" onchange={pick} />
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="1.6"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/></svg>
          <span class="drop-t">{impFile ? impFile.name : 'Choose an OKF bundle'}</span>
          <span class="drop-h">.tar.gz · .tgz · .tar · .zip</span>
        </label>

        <div class="orrow"><span></span><em>or paste a URL</em><span></span></div>
        <input type="url" placeholder="https://github.com/org/repo · https://…/bundle.tar.gz" bind:value={impUrl} class="urlinput" />

        <div class="btnrow">
          <button class="btn" onclick={preview2} disabled={!canImport || impBusy}>{impBusy ? 'Reading…' : 'Preview'}</button>
          <button class="btn pri" onclick={commit} disabled={!canImport || impBusy}>Import</button>
        </div>

        {#if impDry}
          <div class="note">
            <strong>Dry run:</strong> {impDry.documents} documents · {impDry.facts} facts ·
            {impDry.links} links ({impDry.links_broken} broken) · {impDry.skipped} skipped (no type).
            Nothing written yet — click <strong>Import</strong> to commit.
          </div>
        {/if}
        {#if impDone}
          <div class="ok">
            ✓ Imported {impDone.imported_documents} documents ({impDone.imported_pages} pages{#if impDone.imported_images}, {impDone.imported_images} images{/if}),
            {impDone.imported_facts_pending} facts queued for review.
            {#if impDone.imported_links}· {impDone.imported_links} link(s).{/if}
            {#if impDone.duplicate_documents_skipped}· {impDone.duplicate_documents_skipped} duplicate(s) skipped.{/if}
          </div>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px; margin-bottom:18px;}

  .err{color:#c0492f; background:#fbeae6; padding:10px 12px; border-radius:10px; font-size:13px; margin-bottom:14px;}
  .muted{color:var(--muted); font-size:13.5px;}
  .hint{font-weight:400; color:var(--muted); font-size:11px;}

  .grid{display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start;}

  .card{border:1px solid var(--border); border-radius:13px; padding:18px; background:#fff;}
  .c-top{display:flex; align-items:center; justify-content:space-between;}
  .c-ic{width:36px; height:36px; border-radius:9px; background:#f4f3f0; display:grid; place-items:center;}
  .c-nm{font-size:15px; font-weight:600; margin-top:12px; color:var(--ink);}
  .c-sub{font-size:12.5px; color:var(--muted); margin-top:4px; line-height:1.55;}

  .chips{display:flex; flex-wrap:wrap; gap:7px; margin-top:14px;}
  .chip{font-size:12px; color:var(--muted); background:#f0efed; border-radius:999px; padding:5px 11px;}
  .chip b{color:var(--ink); font-weight:700;}

  .imgopt{display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink); margin-top:14px; cursor:pointer; line-height:1.4;}

  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; display:inline-flex; align-items:center; justify-content:center; gap:8px;}
  .btn:disabled{opacity:.5; cursor:default;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.full{width:100%; margin-top:14px; height:42px; font-weight:600;}

  .tree{margin-top:16px; background:var(--cream); border:1px solid var(--border); border-radius:10px; max-height:240px; overflow-y:auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;}
  .trow{display:flex; justify-content:space-between; padding:5px 13px; border-bottom:1px solid var(--border);}
  .trow:last-child{border-bottom:none;}
  .path{color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .sz{color:var(--muted); flex:0 0 auto; margin-left:10px;}

  .drop{display:flex; flex-direction:column; align-items:center; gap:6px; position:relative; border:1.5px dashed var(--border); border-radius:12px; padding:22px 16px; margin-top:14px; cursor:pointer; text-align:center; transition:border-color .12s;}
  .drop:hover{border-color:var(--clay);}
  .drop input[type=file]{position:absolute; inset:0; opacity:0; cursor:pointer;}
  .drop-t{font-size:13px; font-weight:600; color:var(--ink); word-break:break-all;}
  .drop-h{font-size:11px; color:var(--muted);}

  .orrow{display:flex; align-items:center; gap:10px; margin:14px 0 12px;}
  .orrow span{flex:1; height:1px; background:var(--border);}
  .orrow em{font-size:11px; color:var(--muted); font-style:normal;}

  .urlinput{width:100%; height:38px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13px; background:#fff; outline:none; box-sizing:border-box;}
  .urlinput:focus{border-color:var(--clay);}

  .btnrow{display:flex; gap:10px; margin-top:12px;}
  .btnrow .btn{flex:1;}

  .note{margin-top:14px; background:var(--cream); border-radius:10px; padding:11px 14px; font-size:12.5px; line-height:1.6; color:var(--muted);}
  .ok{margin-top:14px; background:#e9f4ee; color:#2f6b46; border-radius:10px; padding:11px 14px; font-size:12.5px; line-height:1.6;}

  @media (max-width:640px){
    .grid{grid-template-columns:1fr;}
  }
</style>
