<script lang="ts">
  import { api } from '$lib/api';
  import { onMount } from 'svelte';

  // per-source approval policy
  type Pol = { mode: 'auto' | 'review'; min: number };
  let policy = $state<Record<string, Pol>>({});
  let pending = $state<any[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let busy = $state(false);
  let toast = $state('');                 // floating success/error toast
  let clean = $state('');                 // snapshot of last-saved policy

  // unsaved-changes indicator: current policy differs from the last save
  let dirty = $derived(!loading && JSON.stringify(policy) !== clean);

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
      clean = JSON.stringify(policy);
      const m = await api.memory('pending');
      pending = m.memory || [];
    } catch (e) { /* fail-soft */ }
    loading = false;
  }
  onMount(load);

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  async function save() {
    saving = true;
    try {
      const r = await api.saveGovernance(policy);
      policy = r.policy || policy;
      clean = JSON.stringify(policy);
      flash('Saved ✓ — approval policy updated');
    } catch (e: any) { flash('Error: ' + (e?.message || 'could not save')); }
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

  function setMode(s: string, mode: 'auto' | 'review') { policy[s].mode = mode; }

  const ICONS: Record<string, string> = {
    human:    'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1',
    doc:      'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h6',
    chat:     'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
    feedback: 'M7 10v12M2 12h3v10H2zM18 22H8.6a2 2 0 0 1-2-1.7L5 10h6l-1-5a2 2 0 0 1 2-2l3 7h4a2 2 0 0 1 2 2.3l-1 7a2 2 0 0 1-2 1.7z'
  };
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Decide which newly-learned facts go live automatically vs. wait for review.</p>
      <div class="flex items-center gap-3">
        {#if dirty}<span class="unsaved">● Unsaved changes</span>{/if}
        <button class="btn pri" class:nudge={dirty} onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    </div>

    {#if loading}
      <div class="muted">Loading…</div>
    {:else}
      <div class="tip" style="margin-bottom:18px">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
        <span>Set per-source routing below. <b>Auto-approve</b> can require a confidence floor — anything below it still lands in <b>Review</b>. Review individual pending facts in <b>Brain → Facts</b>.</span>
      </div>

      <div class="lbl">Fact sources</div>
      <div class="tbl">
        <div class="grow thead">
          <span>Source</span>
          <span>Policy</span>
          <span>Min confidence</span>
          <span class="r">Pending</span>
        </div>
        {#each ORDER as s (s)}
          {#if policy[s]}
            <div class="grow trow">
              <!-- Source -->
              <div class="meta-cell">
                <span class="ico">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d={ICONS[s]}/></svg>
                </span>
                <div class="meta">
                  <div class="nm">{META[s].label}</div>
                  <div class="sub">{META[s].desc}</div>
                </div>
              </div>

              <!-- Policy -->
              <div class="seg">
                <button class="seg-b auto" class:on={policy[s].mode === 'auto'} onclick={() => setMode(s, 'auto')}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Auto
                </button>
                <button class="seg-b review" class:on={policy[s].mode === 'review'} onclick={() => setMode(s, 'review')}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> Review
                </button>
              </div>

              <!-- Min confidence -->
              {#if META[s].conf && policy[s].mode === 'auto'}
                <div class="conf">
                  <input type="range" min="0" max="1" step="0.05" bind:value={policy[s].min} aria-label="Minimum confidence" />
                  <b>{Math.round((policy[s].min || 0) * 100)}%</b>
                </div>
              {:else if META[s].conf}
                <span class="na">Auto-approve off</span>
              {:else}
                <span class="na">— not applicable</span>
              {/if}

              <!-- Pending -->
              <span class="pend">
                {#if pendingBySource[s]}<span class="badge info">{pendingBySource[s]}</span>{:else}<span class="zero">0</span>{/if}
              </span>
            </div>
          {/if}
        {/each}
      </div>

      <div class="foot">
        <button class="btn" disabled={busy || !pending.length} onclick={() => approveAll()}>
          {busy ? 'Working…' : `Bulk-approve pending (${pending.length})`}
        </button>
        <span class="muted" style="margin-left:auto">{pending.length} fact{pending.length === 1 ? '' : 's'} awaiting review</span>
      </div>

      <p class="note">This page sets the default routing + bulk actions. Approve, reject or edit individual facts in <b>Brain → Facts</b>.</p>
    {/if}
  </div>

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .unsaved{font-size:12px; font-weight:600; color:#9a6a16;}
  .btn.nudge{box-shadow:0 0 0 3px rgba(194,104,63,.22);}
  .muted{font-size:12.5px; color:var(--muted);}

  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%); z-index:90;
    background:#1c1a17; color:#fff; font-size:13px; font-weight:500; padding:11px 18px; border-radius:11px;
    box-shadow:0 10px 30px rgba(40,35,30,.28); animation:tin .2s ease-out;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,8px);}to{opacity:1; transform:translate(-50%,0);}}

  .tip{display:flex; gap:9px; align-items:flex-start; background:#eaf0f7; color:#3a5878; border-radius:11px; padding:11px 13px; font-size:12.5px; line-height:1.5;}
  .tip svg{flex:0 0 auto; margin-top:1px;}

  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:10px;}

  /* source rows (governance table) — fixed columns so every row aligns */
  .tbl{border:1px solid var(--border); border-radius:13px; background:#fff; overflow:hidden;}
  .grow{display:grid; grid-template-columns:1fr 196px 232px 60px; gap:16px; align-items:center; padding:14px 16px;}
  .trow{border-top:1px solid var(--line);}
  .thead{padding:9px 16px; background:#f4f3f0; font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; font-weight:600; color:var(--muted);}
  .thead .r{text-align:right;}

  .meta-cell{display:flex; align-items:center; gap:13px; min-width:0;}
  .ico{width:42px; height:42px; border-radius:10px; background:#f4f3f0; display:grid; place-items:center; flex:0 0 auto;}
  .meta{min-width:0;}
  .nm{font-size:14px; font-weight:600; color:var(--ink);}
  .sub{font-size:12px; color:var(--muted); margin-top:2px;}

  .badge{font-size:11px; font-weight:600; background:#f0efed; color:var(--muted); padding:3px 9px; border-radius:999px;}
  .badge.info{background:#eaf0f7; color:#426693;}
  .pend{text-align:right;} .zero{color:var(--muted); font-size:12.5px;}

  /* Auto / Review pill toggle — fixed in the Policy column */
  .seg{display:inline-flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; background:#fff; justify-self:start;}
  .seg-b{display:inline-flex; align-items:center; gap:5px; font-size:12.5px; font-weight:600; padding:6px 13px; background:transparent; border:none; color:var(--muted); cursor:pointer; transition:background .12s, color .12s;}
  .seg-b svg{flex:0 0 auto;}
  .seg-b.auto.on{background:#e3f3e9; color:#2f8f5f;}
  .seg-b.review.on{background:#fbf0dc; color:#c98a2e;}

  /* min-confidence slider — fixed in its column */
  .conf{display:flex; align-items:center; gap:10px;}
  .conf input[type=range]{accent-color:var(--clay); flex:1; min-width:80px; height:6px; cursor:pointer;}
  .conf b{font-size:13px; color:var(--ink); min-width:38px; text-align:right;}
  .na{font-size:12px; color:var(--muted);}

  .foot{display:flex; align-items:center; gap:10px; margin-top:20px;}
  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink);}
  .btn:disabled{opacity:.6; cursor:default;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .note{font-size:12px; color:var(--muted); margin-top:14px;}

  @media (max-width:760px){
    .thead{display:none;}
    .grow{grid-template-columns:1fr; gap:11px;}
    .seg{justify-self:stretch;}
    .seg-b{flex:1; justify-content:center;}
    .pend{text-align:left;}
  }
</style>
