<script lang="ts">
  import { api } from '$lib/api';
  import { onMount } from 'svelte';

  let features = $state<Record<string, boolean>>({ citations: true });
  let loading = $state(true);
  let saving = $state(false);
  let toast = $state('');

  // ── wiki / knowledge schema (Karpathy LLM-wiki Layer 3) ──
  let schema = $state<any>(null);
  let entityTypesStr = $state('');
  let compareAttrsStr = $state('');
  let savingSchema = $state(false);

  function syncSchemaStrings() {
    entityTypesStr = (schema?.entity_types || []).join(', ');
    compareAttrsStr = (schema?.contradiction?.compare_attributes || []).join(', ');
  }

  async function load() {
    loading = true;
    try {
      const r = await api.getFeatures();
      features = { citations: true, ...(r.features || {}) };
    } catch { /* fail-soft → defaults */ }
    try {
      const w = await api.getWikiSchema();
      schema = w.schema;
      syncSchemaStrings();
    } catch { /* fail-soft */ }
    loading = false;
  }
  onMount(load);

  async function saveSchema() {
    if (!schema) return;
    savingSchema = true;
    const patch = {
      wiki_title: schema.wiki_title,
      freshness_days: Number(schema.freshness_days) || 180,
      entity_types: entityTypesStr.split(',').map((s) => s.trim()).filter(Boolean),
      contradiction: {
        auto_resolve_newer: !!schema.contradiction?.auto_resolve_newer,
        compare_attributes: compareAttrsStr.split(',').map((s) => s.trim()).filter(Boolean),
      },
    };
    try {
      const r = await api.saveWikiSchema(patch);
      schema = r.schema;
      syncSchemaStrings();
      flash('Wiki schema saved');
    } catch { flash('Could not save schema'); }
    savingSchema = false;
  }

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  async function toggle(key: string) {
    const next = !features[key];
    features = { ...features, [key]: next };
    saving = true;
    try {
      const r = await api.saveFeatures({ [key]: next });
      features = { citations: true, ...(r.features || {}) };
      flash(next ? 'Citations turned ON' : 'Citations turned OFF');
    } catch {
      features = { ...features, [key]: !next };   // revert on error
      flash('Could not save — try again');
    }
    saving = false;
  }
</script>

{#if toast}<div class="toast">{toast}</div>{/if}

{#if loading}
  <div class="muted">Loading…</div>
{:else}
  <div class="ppad">
  <p class="lead">
    Per-project behaviour switches. Changes are live immediately — no redeploy — so one
    deployment can be tuned per client.
  </p>

  <!-- ===== Citations ===== -->
  <div class="row">
    <div class="info">
      <div class="rt">Inline citations</div>
      <div class="rd">
        When ON, answers cite the exact source page inline (<code>[p.4]</code>) and show
        clickable source coins. When OFF, answers are clean prose with no source markers,
        no coins, and no “PAGES” line — good for projects where the source shouldn’t show.
      </div>
    </div>
    <button class="sw" class:on={features.citations} disabled={saving}
      role="switch" aria-checked={features.citations}
      onclick={() => toggle('citations')}>
      <span class="knob"></span>
    </button>
  </div>

  <!-- live preview -->
  <div class="prev">
    <div class="pv-lab">Preview</div>
    {#if features.citations}
      <div class="pv-card">
        To create a new site, open <b>Customer Maintenance</b> and add the customer
        <span class="cite">[p.4]</span>, then set <b>Type = 2</b> <span class="cite">[p.5]</span>.
        <div class="coins"><span class="coin">1 · New Site Creation · p.4</span><span class="coin">2 · p.5</span></div>
      </div>
    {:else}
      <div class="pv-card">
        To create a new site, open <b>Customer Maintenance</b> and add the customer,
        then set <b>Type = 2</b>.
      </div>
    {/if}
  </div>

  {#if schema}
    <!-- ===== Wiki / knowledge schema (Karpathy LLM-wiki Layer 3) ===== -->
    <div class="sect">
      <div class="sect-h">Knowledge schema</div>
      <p class="lead2">
        The conventions the knowledge base is maintained to — read by the wiki
        compiler, contradiction detection and the browsable wiki. One per project.
      </p>

      <div class="grid">
        <label class="fld">
          <span>Wiki title</span>
          <input bind:value={schema.wiki_title} />
        </label>
        <label class="fld">
          <span>Freshness window (days) — a claim is “stale” past this</span>
          <input type="number" min="1" bind:value={schema.freshness_days} />
        </label>
      </div>

      <label class="fld">
        <span>Entity / concept types <small>(comma-separated — what becomes a linkable wiki page)</small></span>
        <input bind:value={entityTypesStr} placeholder="system, screen, field, code, role…" />
      </label>

      <label class="fld">
        <span>Contradiction — compare these claim attributes across docs <small>(comma-separated)</small></span>
        <input bind:value={compareAttrsStr} placeholder="value, threshold, setting, code, path" />
      </label>

      <div class="row2">
        <div class="info2">
          <div class="rt2">Auto-resolve conflicts to the newer document</div>
          <div class="rd2">
            ON = a newer doc silently wins a contradiction. OFF (recommended) =
            every conflict goes to a review queue for a human to decide.
          </div>
        </div>
        <button class="sw" class:on={schema.contradiction?.auto_resolve_newer}
          role="switch" aria-checked={schema.contradiction?.auto_resolve_newer}
          onclick={() => (schema.contradiction = { ...schema.contradiction, auto_resolve_newer: !schema.contradiction?.auto_resolve_newer })}>
          <span class="knob"></span>
        </button>
      </div>

      <button class="save" disabled={savingSchema} onclick={saveSchema}>
        {savingSchema ? 'Saving…' : 'Save schema'}
      </button>
    </div>
  {/if}
  </div>
{/if}

<style>
  .ppad { padding: 6px 28px 32px; max-width: 1040px; }
  .lead { color: #6b675e; font-size: 13px; max-width: 640px; margin: 0 0 18px; line-height: 1.5; }
  .muted { color: #8a857c; font-size: 13px; }
  .row { display: flex; align-items: flex-start; gap: 18px; max-width: 720px; padding: 16px; border: 1px solid #e9e6dd; border-radius: 14px; background: #fff; }
  .info { flex: 1; min-width: 0; }
  .rt { font-weight: 600; font-size: 14px; color: #1f1e1d; }
  .rd { color: #6b675e; font-size: 12.5px; line-height: 1.5; margin-top: 4px; }
  .rd code { background: #f0eee6; border-radius: 4px; padding: 0 4px; font-size: 11.5px; }
  .sw { flex: none; width: 46px; height: 26px; border-radius: 999px; border: none; background: #d8d3c7; cursor: pointer; position: relative; transition: background .16s; margin-top: 2px; }
  .sw.on { background: #3f8f5f; }
  .sw:disabled { opacity: .6; cursor: default; }
  .knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.2); transition: left .16s; }
  .sw.on .knob { left: 23px; }
  .prev { max-width: 720px; margin-top: 16px; }
  .pv-lab { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: #8a857c; font-weight: 600; margin-bottom: 6px; }
  .pv-card { border: 1px solid #e9e6dd; border-radius: 12px; background: #faf9f5; padding: 14px 16px; font-size: 13px; line-height: 1.6; color: #2a2824; }
  .cite { color: #3f7fb0; font-weight: 600; font-size: 11px; vertical-align: super; }
  .coins { margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap; }
  .coin { font-size: 11px; color: #c2683f; background: #fbeee7; border: 1px solid #eccdbd; border-radius: 7px; padding: 3px 8px; }
  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); background: #1f1e1d; color: #fff; padding: 9px 16px; border-radius: 10px; font-size: 13px; z-index: 60; box-shadow: 0 8px 24px rgba(0,0,0,.2); }

  /* wiki schema section */
  .sect { max-width: 720px; margin-top: 28px; padding-top: 22px; border-top: 1px solid #e9e6dd; }
  .sect-h { font-size: 15px; font-weight: 700; color: #1f1e1d; }
  .lead2 { color: #6b675e; font-size: 12.5px; line-height: 1.5; margin: 4px 0 16px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .fld { display: block; margin-bottom: 12px; }
  .fld > span { display: block; font-size: 12.5px; font-weight: 600; color: #2a2824; margin-bottom: 5px; }
  .fld > span small { font-weight: 400; color: #8a857c; }
  .fld input { width: 100%; box-sizing: border-box; border: 1px solid #e0dfda; border-radius: 9px; padding: 8px 11px; font: inherit; font-size: 13px; color: #1f1e1d; background: #fff; }
  .fld input:focus { outline: none; border-color: #3f7fb0; }
  .row2 { display: flex; align-items: flex-start; gap: 18px; padding: 14px; border: 1px solid #e9e6dd; border-radius: 12px; background: #fff; margin: 6px 0 16px; }
  .info2 { flex: 1; min-width: 0; }
  .rt2 { font-weight: 600; font-size: 13px; color: #1f1e1d; }
  .rd2 { color: #6b675e; font-size: 12px; line-height: 1.5; margin-top: 3px; }
  .save { background: #1f1e1d; color: #fff; border: none; border-radius: 9px; padding: 9px 18px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .save:disabled { opacity: .55; cursor: default; }
</style>
