<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';

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

<div class="px-7 py-6">
  {#if err}<div class="err">{err}</div>{/if}

  <Section
    title="Export"
    desc="Download Aria's entire brain as an Open Knowledge Format bundle — plain markdown + YAML frontmatter. Opens in any editor, renders on GitHub, version-controls in git, and re-imports anywhere. No vendor lock-in.">
    {#if loading}
      <div class="muted">Reading the knowledge base…</div>
    {:else if prev}
      <div class="cards">
        <div class="card"><b>{prev.documents}</b><span>documents</span></div>
        <div class="card"><b>{prev.facts}</b><span>facts</span></div>
        <div class="card"><b>{prev.count}</b><span>files</span></div>
        <div class="card"><b>{fmtBytes(prev.total_bytes)}</b><span>bundle size</span></div>
        <div class="card"><b>v{prev.okf_version}</b><span>OKF spec</span></div>
      </div>

      <div class="dlrow">
        <button class="dl" onclick={download} disabled={busy}>
          {busy ? 'Building…' : '⬇  Download OKF bundle (.tar.gz)'}
        </button>
        <label class="imgopt">
          <input type="checkbox" bind:checked={withImages} /> Include page images (larger · keeps answer-with-page on re-import)
        </label>
      </div>

      <h2>Bundle contents</h2>
      <div class="tree">
        {#each prev.files as f}
          <div class="row"><span class="path">{f.path}</span><span class="sz">{fmtBytes(f.bytes)}</span></div>
        {/each}
      </div>

      <div class="note">
        <strong>What you get:</strong> <code>index.md</code> (bundle root) · <code>documents/</code> (one
        <code>.md</code> per SOP, frontmatter + compiled tables) · <code>facts/</code> (active facts with
        <code>#&nbsp;Citations</code>) · <code>log.md</code> (ingest history) · <code>viz.html</code>
        (self-contained graph viewer — double-click to open). Conformant with OKF v{prev.okf_version}.
      </div>
    {/if}
  </Section>

  {#if !loading && prev}
    <Section
      title="Import"
      desc="Feed an OKF bundle (.tar.gz / .zip) straight in — upload a file or paste an https / GitHub repo URL. From another Aria, an Obsidian vault, or any OKF producer. The markdown body is the knowledge, so there's no vision pass and no LLM cost: documents become searchable instantly; imported facts land in review (pending) so they never override your docs.">
      {#if impErr}<div class="err">{impErr}</div>{/if}

      <div class="imp">
        <input type="file" accept=".tar.gz,.tgz,.tar,.zip,application/gzip,application/zip" onchange={pick} />
        <span class="orlbl">or</span>
        <input type="url" placeholder="https://github.com/org/repo  ·  https://…/bundle.tar.gz" bind:value={impUrl}
               class="urlinput" />
        <button class="ghost" onclick={preview2} disabled={!canImport || impBusy}>
          {impBusy ? 'Reading…' : 'Preview'}
        </button>
        <button class="dl" onclick={commit} disabled={!canImport || impBusy}>Import</button>
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
    </Section>
  {/if}
</div>

<style>
  .err { color: #c0492f; background: #fbeae6; padding: 10px 12px; border-radius: 10px; font-size: 13px; margin-bottom: 14px; }
  .muted { color: #8a8780; font-size: 14px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
  .card { background: #fff; border: 1px solid #ece9e2; border-radius: 12px; padding: 12px 18px; min-width: 96px; text-align: center; }
  .card b { display: block; font-size: 20px; color: var(--clay); }
  .card span { font-size: 11.5px; color: #8a8780; text-transform: uppercase; letter-spacing: .04em; }
  .dlrow { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .imgopt { display: flex; align-items: center; gap: 7px; font-size: 13px; color: #5b5a55; }
  .dl { background: var(--clay); color: #fff; border: none; border-radius: 24px; padding: 11px 22px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .dl:disabled { opacity: .5; cursor: default; }
  h2 { font-size: 14px; font-weight: 700; margin: 26px 0 10px; color: #46443f; }
  .tree { background: #fff; border: 1px solid #ece9e2; border-radius: 12px; max-height: 320px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  .row { display: flex; justify-content: space-between; padding: 5px 14px; border-bottom: 1px solid #f4f1ea; }
  .row:last-child { border-bottom: none; }
  .path { color: #46443f; }
  .sz { color: #a8a59d; }
  .note { margin-top: 18px; background: #f7f7f5; border-radius: 12px; padding: 12px 16px; font-size: 13px; line-height: 1.7; color: #5b5a55; }
  .note code { background: #fff; padding: 1px 5px; border-radius: 5px; font-size: 12px; }
  .imp { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; }
  .imp input[type=file] { font-size: 13px; }
  .orlbl { color: #a8a59d; font-size: 12px; }
  .urlinput { flex: 1; min-width: 220px; border: 1px solid #ddd8cd; border-radius: 8px; padding: 8px 11px; font-size: 13px; }
  .ghost { background: #fff; color: var(--clay); border: 1px solid var(--clay); border-radius: 24px; padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .ghost:disabled { opacity: .45; cursor: default; }
  .ok { margin-top: 14px; background: #e9f4ee; color: #2f6b46; border-radius: 12px; padding: 12px 16px; font-size: 13px; line-height: 1.6; }
</style>
