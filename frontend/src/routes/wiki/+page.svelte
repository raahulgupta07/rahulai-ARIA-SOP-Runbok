<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then(u => me = u).catch(() => {}); });

  let loading = $state(true);
  let failed = $state(false);
  let entities = $state<any[]>([]);
  let groups = $state<any[]>([]);
  let totals = $state<any>({ entities: 0, docs: 0, mentions: 0 });
  let q = $state('');

  $effect(() => {
    if (!me) return;
    let alive = true;
    loading = true; failed = false;
    api.wikiIndex().then((d: any) => {
      if (!alive) return;
      entities = Array.isArray(d?.entities) ? [...d.entities] : [];
      groups = Array.isArray(d?.catalog?.groups) ? [...d.catalog.groups] : [];
      totals = d?.totals || { entities: 0, docs: 0, mentions: 0 };
      loading = false;
    }).catch(() => { if (alive) { failed = true; loading = false; } });
    return () => { alive = false; };
  });

  let filtered = $derived.by(() => {
    const nd = q.trim().toLowerCase();
    if (!nd) return entities;
    return entities.filter((e) => (e.name || '').toLowerCase().includes(nd));
  });

  function kindColor(kind: string): string {
    const map: Record<string, string> = {
      code: '#7b6bd6', menu_path: '#2f8f83', system: '#3f7fb0',
      screen: '#c98a2e', field: '#3f8f5f', term: '#c2683f'
    };
    return map[kind] || '#8a857c';
  }
  function kindBg(kind: string): string {
    return kindColor(kind) + '1a';
  }
</script>

<div class="wiki-wrap">
  {#if !me}
    <div class="wiki-loading">Loading…</div>
  {:else}
    <header class="wiki-head">
      <h1>Knowledge Base</h1>
      <div class="totals">
        <span><b>{totals.entities ?? 0}</b> entities</span>
        <span class="dot">·</span>
        <span><b>{totals.docs ?? 0}</b> docs</span>
        <span class="dot">·</span>
        <span><b>{totals.mentions ?? 0}</b> mentions</span>
      </div>
    </header>

    {#if loading}
      <div class="wiki-loading">Loading knowledge base…</div>
    {:else if failed}
      <div class="wiki-empty">Could not load the knowledge base. Please try again.</div>
    {:else}
      <div class="searchbox">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
        <input bind:value={q} placeholder="Search entities…" autocomplete="off" />
        {#if q}<button class="clr" onclick={() => (q = '')} aria-label="Clear">×</button>{/if}
      </div>

      <section>
        <h2>Entities {#if q}<span class="cnt">({filtered.length})</span>{/if}</h2>
        {#if filtered.length === 0}
          <div class="wiki-empty">No entities match “{q}”.</div>
        {:else}
          <div class="egrid">
            {#each filtered as e (e.id)}
              <a class="ecard" href={`/wiki/entity/${e.id}`}>
                <div class="ename">{e.name}</div>
                <div class="erow">
                  <span class="kchip" style="color:{kindColor(e.kind)}; background:{kindBg(e.kind)}">{e.kind}</span>
                  <span class="edocs">{e.doc_count} {e.doc_count === 1 ? 'doc' : 'docs'}</span>
                </div>
              </a>
            {/each}
          </div>
        {/if}
      </section>

      {#if groups.length > 0}
        <section>
          <h2>Documents by category</h2>
          {#each groups as g}
            <div class="catblock">
              <div class="catname">{g.category || 'Uncategorized'}</div>
              <div class="doclist">
                {#each (g.docs || []) as d (d.id)}
                  <a class="doclink" href={`/wiki/doc/${d.id}`}>{d.name}</a>
                {/each}
              </div>
            </div>
          {/each}
        </section>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .wiki-wrap {
    height: 100%; overflow-y: auto; max-width: 980px; margin: 0 auto;
    padding: 28px 24px 60px; color: var(--ink, #1f1e1d);
  }
  .wiki-loading, .wiki-empty {
    padding: 40px 8px; color: var(--muted, #8a857c); font-size: 14px;
  }
  .wiki-head { margin-bottom: 20px; }
  .wiki-head h1 { font-size: 30px; font-weight: 700; letter-spacing: -.02em; }
  .totals { margin-top: 6px; font-size: 13px; color: var(--muted, #8a857c); display: flex; gap: 7px; align-items: center; }
  .totals b { color: var(--ink, #1f1e1d); }
  .totals .dot { opacity: .5; }

  .searchbox {
    position: relative; display: flex; align-items: center; gap: 9px;
    background: #fff; border: 1px solid #e0dfda; border-radius: 11px;
    padding: 0 12px; height: 44px; margin-bottom: 26px; color: var(--muted, #8a857c);
  }
  .searchbox input {
    flex: 1; border: none; outline: none; background: transparent;
    font-size: 15px; color: var(--ink, #1f1e1d);
  }
  .searchbox .clr {
    border: none; background: transparent; font-size: 20px; line-height: 1;
    color: var(--muted, #8a857c); cursor: pointer; padding: 0 2px;
  }

  section { margin-bottom: 32px; }
  section h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--muted, #8a857c); margin-bottom: 12px; }
  section h2 .cnt { font-weight: 400; text-transform: none; }

  .egrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .ecard {
    display: block; background: #fff; border: 1px solid #e0dfda; border-radius: 12px;
    padding: 14px 15px; text-decoration: none; color: inherit; transition: border-color .12s, transform .12s;
  }
  .ecard:hover { border-color: #c2683f; transform: translateY(-1px); }
  .ename { font-size: 15px; font-weight: 600; margin-bottom: 9px; line-height: 1.3; }
  .erow { display: flex; align-items: center; gap: 8px; }
  .kchip { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 20px; }
  .edocs { font-size: 12px; color: var(--muted, #8a857c); margin-left: auto; }

  .catblock { margin-bottom: 16px; }
  .catname { font-size: 14px; font-weight: 600; margin-bottom: 7px; color: var(--ink, #1f1e1d); }
  .doclist { display: flex; flex-wrap: wrap; gap: 8px; }
  .doclink {
    font-size: 13px; background: #fff; border: 1px solid #e0dfda; border-radius: 8px;
    padding: 6px 11px; text-decoration: none; color: #46443f; transition: border-color .12s, color .12s;
  }
  .doclink:hover { border-color: #c2683f; color: #c2683f; }
</style>
