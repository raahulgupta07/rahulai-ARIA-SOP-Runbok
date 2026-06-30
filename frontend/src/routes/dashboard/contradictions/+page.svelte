<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';

  // reactive admin guard (cachedUser() is non-reactive / null on cold cache)
  let me = $state(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  type Conflict = {
    id: number;
    entity: string;
    attribute: string;
    doc_a: number; doc_a_name: string; page_a: number; value_a: string;
    doc_b: number; doc_b_name: string; page_b: number; value_b: string;
    severity: string;
    detail: string;
    status: string;
    created_at: string;
  };

  let conflicts = $state<Conflict[]>([]);
  let counts = $state<{ pending: number; resolved: number }>({ pending: 0, resolved: 0 });
  let loading = $state(false);
  let filter = $state<'pending' | 'resolved' | 'all'>('pending');
  let resolving = $state<number | null>(null);

  async function load() {
    if (!isAdmin) return;
    loading = true;
    try {
      const r = await api.getContradictions(filter);
      conflicts = [...(r.conflicts ?? [])];
      counts = { pending: r.counts?.pending ?? 0, resolved: r.counts?.resolved ?? 0 };
    } catch {
      conflicts = [];
    } finally {
      loading = false;
    }
  }

  onMount(load);
  // reload when filter changes (and once isAdmin resolves true)
  let lastKey = '';
  $effect(() => {
    const key = `${isAdmin}:${filter}`;
    if (key === lastKey) return;
    lastKey = key;
    if (isAdmin) load();
  });

  async function resolve(id: number, choice: 'new' | 'old' | 'both' | 'dismiss') {
    if (resolving) return;
    resolving = id;
    try {
      await api.resolveContradiction(id, choice);
      await load();
    } catch {
      // fail-soft: keep the row, just clear the busy state
    } finally {
      resolving = null;
    }
  }

  function sevClass(s: string) {
    const v = (s || '').toLowerCase();
    if (v === 'high' || v === 'critical') return 'sev-high';
    if (v === 'medium' || v === 'med') return 'sev-med';
    return 'sev-low';
  }

  const STATUS_LABEL: Record<string, string> = {
    kept_new: 'Kept new',
    kept_old: 'Kept existing',
    both: 'Both valid',
    dismissed: 'Dismissed'
  };
  function statusLabel(s: string) { return STATUS_LABEL[s] ?? s; }
</script>

{#if !me}
  <div class="muted pad" style="color:#b7b1a4">Loading…</div>
{:else if !isAdmin}
  <div class="muted pad" style="color:#b7b1a4">Admin only.</div>
{:else}
  <div class="cpage">
    <p class="lede">Where two runbooks disagree on the same fact. Pick the source of truth.</p>

    <div class="filters">
      <button class="fpill" class:on={filter === 'pending'} onclick={() => (filter = 'pending')}>
        Pending
        {#if counts.pending}<span class="badge">{counts.pending}</span>{/if}
      </button>
      <button class="fpill" class:on={filter === 'resolved'} onclick={() => (filter = 'resolved')}>
        Resolved
        {#if counts.resolved}<span class="badge soft">{counts.resolved}</span>{/if}
      </button>
      <button class="fpill" class:on={filter === 'all'} onclick={() => (filter = 'all')}>All</button>
    </div>

    {#if loading && conflicts.length === 0}
      <div class="muted pad" style="color:#b7b1a4">Loading contradictions…</div>
    {:else if conflicts.length === 0}
      <div class="empty">
        <div class="echeck">✓</div>
        <div class="etitle">No contradictions found</div>
        <div class="esub">The knowledge base is consistent.</div>
      </div>
    {:else}
      <div class="clist">
        {#each conflicts as c (c.id)}
          <div class="ccard" class:busy={resolving === c.id}>
            <div class="chead">
              <div class="ctitle">
                <b>{c.entity}</b><span class="dot">·</span>{c.attribute}
              </div>
              <span class="sev {sevClass(c.severity)}">{c.severity || 'low'}</span>
            </div>

            <div class="claims">
              <div class="claim">
                <div class="claim-tag">Existing</div>
                <div class="claim-src">{c.doc_b_name} · p.{c.page_b}</div>
                <div class="claim-val">{c.value_b}</div>
              </div>
              <div class="vs">vs</div>
              <div class="claim new">
                <div class="claim-tag">New</div>
                <div class="claim-src">{c.doc_a_name} · p.{c.page_a}</div>
                <div class="claim-val">{c.value_a}</div>
              </div>
            </div>

            {#if c.detail}
              <div class="detail">{c.detail}</div>
            {/if}

            {#if c.status === 'pending'}
              <div class="actions">
                <button class="act primary" disabled={resolving === c.id} onclick={() => resolve(c.id, 'new')}>Keep new</button>
                <button class="act" disabled={resolving === c.id} onclick={() => resolve(c.id, 'old')}>Keep existing</button>
                <button class="act" disabled={resolving === c.id} onclick={() => resolve(c.id, 'both')}>Both valid</button>
                <button class="act ghost" disabled={resolving === c.id} onclick={() => resolve(c.id, 'dismiss')}>Dismiss</button>
                {#if resolving === c.id}<span class="working">Saving…</span>{/if}
              </div>
            {:else}
              <div class="resolved-row">
                <span class="rchip">{statusLabel(c.status)}</span>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .cpage { padding: 4px 2px 40px; }
  .lede { color: #8a857a; font-size: 13.5px; margin: 0 0 16px; }

  .filters { display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }
  .fpill {
    display: inline-flex; align-items: center; gap: 7px;
    background: #fff; border: 1px solid var(--border); color: var(--ink);
    border-radius: 20px; padding: 6px 14px; font-size: 13px; font-weight: 600;
    cursor: pointer;
  }
  .fpill:hover { border-color: #cfcdc6; }
  .fpill.on { background: #1f1e1d; color: #fff; border-color: #1f1e1d; }
  .badge {
    min-width: 18px; text-align: center; background: #c0492f; color: #fff;
    border-radius: 9px; padding: 1px 6px; font-size: 11px; font-weight: 700;
  }
  .badge.soft { background: #e4f1e8; color: #3f8f5f; }
  .fpill.on .badge.soft { background: rgba(255,255,255,.2); color: #fff; }

  .clist { display: flex; flex-direction: column; gap: 13px; }
  .ccard {
    background: #fff; border: 1px solid var(--border); border-radius: 13px;
    padding: 16px 18px; transition: opacity .15s;
  }
  .ccard.busy { opacity: .6; }

  .chead { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 13px; }
  .ctitle { font-size: 15px; color: var(--ink); }
  .ctitle b { font-weight: 700; }
  .ctitle .dot { color: #c4c1b9; margin: 0 7px; }

  .sev { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; padding: 3px 9px; border-radius: 7px; }
  .sev-high { background: #fbe6e1; color: #c0492f; }
  .sev-med { background: #fbeede; color: #9a6a23; }
  .sev-low { background: #eef0ee; color: #6c7a6f; }

  .claims { display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: stretch; }
  .claim { background: #faf9f5; border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; }
  .claim.new { background: #fff7f1; border-color: #f0d3c1; }
  .claim-tag { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: #8a857a; margin-bottom: 5px; }
  .claim.new .claim-tag { color: #c2683f; }
  .claim-src { font-size: 11.5px; color: #9a958a; margin-bottom: 6px; }
  .claim-val { font-size: 14px; color: var(--ink); line-height: 1.4; word-break: break-word; }
  .vs { align-self: center; font-size: 11px; font-weight: 700; color: #bdb9af; text-transform: uppercase; }

  .detail { margin-top: 12px; font-size: 13px; color: #6f6a5f; line-height: 1.5; background: #f7f6f2; border-radius: 9px; padding: 9px 12px; }

  .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
  .act {
    background: #fff; border: 1px solid var(--border); color: var(--ink);
    border-radius: 8px; padding: 7px 13px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .act:hover:not(:disabled) { border-color: #cfcdc6; }
  .act:disabled { opacity: .5; cursor: default; }
  .act.primary { background: #c2683f; border-color: #c2683f; color: #fff; }
  .act.primary:hover:not(:disabled) { background: #a8542f; }
  .act.ghost { color: #9a958a; }
  .working { font-size: 12px; color: #9a958a; }

  .resolved-row { margin-top: 12px; }
  .rchip { display: inline-block; font-size: 11.5px; font-weight: 600; background: #eef0ee; color: #6c7a6f; border-radius: 7px; padding: 4px 11px; }

  .empty { text-align: center; padding: 56px 20px; color: #8a857a; }
  .echeck {
    width: 46px; height: 46px; margin: 0 auto 14px; border-radius: 50%;
    background: #e4f1e8; color: #3f8f5f; font-size: 24px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }
  .etitle { font-size: 16px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
  .esub { font-size: 13px; }

  .pad { padding: 28px 4px; }

  @media (max-width: 640px) {
    .claims { grid-template-columns: 1fr; }
    .vs { display: none; }
  }
</style>
