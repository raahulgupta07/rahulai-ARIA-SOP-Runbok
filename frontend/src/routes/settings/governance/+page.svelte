<script lang="ts">
  import { api } from '$lib/api';
  import { onMount } from 'svelte';

  // per-source approval policy
  type Pol = { mode: 'auto' | 'review'; min: number };
  let policy = $state<Record<string, Pol>>({});
  let pending = $state<any[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let saved = $state(false);
  let busy = $state(false);

  const META: Record<string, { label: string; desc: string; conf: boolean }> = {
    human:    { label: 'Hand-taught', desc: 'Facts you enter via the Teach-a-fact button.', conf: false },
    doc:      { label: 'From documents', desc: 'Facts auto-extracted from a document at ingest.', conf: true },
    chat:     { label: 'Learned in chat', desc: 'Facts the agent extracts when you teach it mid-conversation.', conf: true },
    feedback: { label: 'From corrections', desc: 'Facts created from a downvote correction.', conf: false }
  };
  const ORDER = ['human', 'doc', 'chat', 'feedback'];

  async function load() {
    loading = true;
    try {
      const g = await api.getGovernance();
      policy = g.policy || {};
      const m = await api.memory('pending');
      pending = m.memory || [];
    } catch (e) { /* fail-soft */ }
    loading = false;
  }
  onMount(load);

  async function save() {
    saving = true; saved = false;
    try { const r = await api.saveGovernance(policy); policy = r.policy || policy; saved = true; setTimeout(() => (saved = false), 2000); }
    catch (e) {}
    saving = false;
  }

  let pendingBySource = $derived.by(() => {
    const m: Record<string, number> = {};
    for (const f of pending) m[f.source || 'human'] = (m[f.source || 'human'] || 0) + 1;
    return m;
  });

  async function approveAll(source?: string) {
    busy = true;
    try { await api.approveBulk(source ? { source } : {}); await load(); } catch (e) {}
    busy = false;
  }
</script>

<div class="gov">
  <p class="lead">Set which newly-learned facts go <b>live automatically</b> versus land in a <b>review queue</b> for you to approve. Auto-approve can require a confidence floor — anything below it still waits for review.</p>

  {#if loading}
    <div class="muted">Loading…</div>
  {:else}
    <div class="cards">
      {#each ORDER as s (s)}
        {#if policy[s]}
          <div class="gcard">
            <div class="gc-head">
              <div>
                <div class="gc-ttl">{META[s].label}</div>
                <div class="gc-desc">{META[s].desc}</div>
              </div>
              {#if pendingBySource[s]}<span class="gc-pend">{pendingBySource[s]} pending</span>{/if}
            </div>
            <div class="gc-row">
              <div class="seg">
                <button class="seg-b {policy[s].mode === 'auto' ? 'on' : ''}" onclick={() => (policy[s].mode = 'auto')}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><polyline points="20 6 9 17 4 12"/></svg> Auto-approve</button>
                <button class="seg-b {policy[s].mode === 'review' ? 'on' : ''}" onclick={() => (policy[s].mode = 'review')}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> Review first</button>
              </div>
              {#if META[s].conf && policy[s].mode === 'auto'}
                <label class="conf">
                  Min confidence
                  <input type="range" min="0" max="1" step="0.05" bind:value={policy[s].min} />
                  <b>{Math.round((policy[s].min || 0) * 100)}%</b>
                </label>
              {/if}
            </div>
            {#if pendingBySource[s]}
              <button class="appall" disabled={busy} onclick={() => approveAll(s)}>Approve all {pendingBySource[s]} {META[s].label.toLowerCase()} pending →</button>
            {/if}
          </div>
        {/if}
      {/each}
    </div>

    <div class="foot">
      <button class="save" onclick={save} disabled={saving}>{saving ? 'Saving…' : saved ? 'Saved' : 'Save policy'}</button>
      {#if pending.length}
        <button class="ghost" disabled={busy} onclick={() => approveAll()}>Approve all {pending.length} pending</button>
      {/if}
      <span class="muted" style="margin-left:auto">{pending.length} fact{pending.length === 1 ? '' : 's'} awaiting review</span>
    </div>
    <p class="note">Tip: review individual pending facts (approve/reject/edit) in <b>Brain → Facts</b>. This page sets the default routing + bulk actions.</p>
  {/if}
</div>

<style>
  .gov { max-width: 760px; }
  .lead { font-size: 13.5px; color: var(--muted); line-height: 1.6; margin-bottom: 18px; }
  .cards { display: flex; flex-direction: column; gap: 12px; }
  .gcard { border: 1px solid var(--border); border-radius: 12px; padding: 15px 16px; background: #fff; }
  .gc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
  .gc-ttl { font-size: 14px; font-weight: 600; color: var(--ink); }
  .gc-desc { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .gc-pend { flex: none; font-size: 11px; font-weight: 600; color: #a9742a; background: #fbf1df; border-radius: 99px; padding: 3px 9px; }
  .gc-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 9px; overflow: hidden; }
  .seg-b { font-size: 12.5px; font-weight: 600; padding: 6px 13px; background: #fff; border: none; color: var(--muted); cursor: pointer; }
  .seg-b.on { background: var(--clay); color: #fff; }
  .conf { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
  .conf input { accent-color: var(--clay); }
  .conf b { color: var(--ink); min-width: 34px; }
  .appall { margin-top: 11px; font-size: 12px; font-weight: 600; color: var(--clay); background: transparent; border: none; cursor: pointer; padding: 0; }
  .appall:hover { text-decoration: underline; }
  .foot { display: flex; align-items: center; gap: 10px; margin-top: 18px; }
  .save { font-size: 13px; font-weight: 600; color: #fff; background: var(--clay); border: none; border-radius: 9px; padding: 8px 18px; cursor: pointer; }
  .save:disabled { opacity: .7; }
  .ghost { font-size: 12.5px; font-weight: 600; color: var(--ink); background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 8px 14px; cursor: pointer; }
  .note { font-size: 12px; color: var(--muted); margin-top: 14px; }
  .muted { font-size: 12.5px; color: var(--muted); }
</style>
