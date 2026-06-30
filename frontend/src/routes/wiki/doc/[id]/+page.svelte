<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { md } from '$lib/md';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then(u => me = u).catch(() => {}); });

  let id = $derived(Number($page.params.id));

  let loading = $state(true);
  let failed = $state(false);
  let doc = $state<any>(null);
  let pages = $state<any[]>([]);
  let backlinks = $state<any[]>([]);
  let dependsOn = $state<any[]>([]);

  $effect(() => {
    const did = id;
    if (!me || !did) return;
    let alive = true;
    loading = true; failed = false;
    api.wikiDoc(did).then((d: any) => {
      if (!alive) return;
      doc = d?.doc || null;
      pages = Array.isArray(d?.pages) ? [...d.pages] : [];
      backlinks = Array.isArray(d?.backlinks) ? [...d.backlinks] : [];
      dependsOn = Array.isArray(d?.depends_on) ? [...d.depends_on] : [];
      loading = false;
    }).catch(() => { if (alive) { failed = true; loading = false; } });
    return () => { alive = false; };
  });

  // Intercept clicks on rendered markdown: /wiki/* links → SPA navigate
  function onContentClick(e: MouseEvent) {
    const a = (e.target as HTMLElement)?.closest?.('a');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (href.startsWith('/wiki/')) {
      e.preventDefault();
      goto(href);
    }
  }
</script>

<div class="doc-layout">
  {#if !me}
    <div class="wiki-loading">Loading…</div>
  {:else}
    <div class="doc-main">
      <a class="back" href="/wiki">← Knowledge Base</a>

      {#if loading}
        <div class="wiki-loading">Loading document…</div>
      {:else if failed || !doc}
        <div class="wiki-empty">Not found — this document could not be loaded.</div>
      {:else}
        <header class="dhead">
          <h1>{doc.name}</h1>
          {#if doc.category}<span class="cat">{doc.category}</span>{/if}
        </header>

        {#if pages.length === 0}
          <div class="wiki-empty">This document has no readable pages.</div>
        {:else}
          <div class="pages">
            {#each pages as p (p.page_no)}
              <div class="pageblock">
                <div class="pno">Page {p.page_no}</div>
                <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
                <div class="md" onclick={onContentClick}>{@html md(p.md_linked || '')}</div>
              </div>
            {/each}
          </div>
        {/if}
      {/if}
    </div>

    {#if !loading && !failed && doc}
      <aside class="doc-side">
        <div class="side-sec">
          <h3>Referenced by</h3>
          {#if backlinks.length === 0}
            <div class="side-empty">No backlinks.</div>
          {:else}
            {#each backlinks as b (b.doc_id)}
              <a class="side-link" href={`/wiki/doc/${b.doc_id}`}>
                <span class="sl-name">{b.doc_name}</span>
                {#if b.reason}<span class="sl-reason">{b.reason}</span>{/if}
              </a>
            {/each}
          {/if}
        </div>

        <div class="side-sec">
          <h3>Prerequisites</h3>
          {#if dependsOn.length === 0}
            <div class="side-empty">None.</div>
          {:else}
            {#each dependsOn as d (d.doc_id)}
              <a class="side-link" href={`/wiki/doc/${d.doc_id}`}>
                <span class="sl-name">{d.doc_name}</span>
                {#if d.reason}<span class="sl-reason">{d.reason}</span>{/if}
              </a>
            {/each}
          {/if}
        </div>
      </aside>
    {/if}
  {/if}
</div>

<style>
  .doc-layout { height: 100%; overflow-y: auto; display: flex; gap: 28px; max-width: 1100px; margin: 0 auto; padding: 24px 24px 60px; align-items: flex-start; }
  .doc-main { flex: 1; min-width: 0; }
  .doc-side { width: 250px; flex-shrink: 0; position: sticky; top: 0; }
  @media (max-width: 820px) { .doc-layout { flex-direction: column; } .doc-side { width: 100%; position: static; } }

  .wiki-loading, .wiki-empty { padding: 40px 8px; color: var(--muted, #8a857c); font-size: 14px; }
  .back { display: inline-block; font-size: 13px; color: var(--muted, #8a857c); text-decoration: none; margin-bottom: 18px; }
  .back:hover { color: #c2683f; }

  .dhead { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
  .dhead h1 { font-size: 27px; font-weight: 700; letter-spacing: -.02em; line-height: 1.2; }
  .cat { font-size: 11px; font-weight: 600; color: #3f7fb0; background: #3f7fb01a; padding: 3px 10px; border-radius: 20px; }

  .pages { display: flex; flex-direction: column; gap: 18px; }
  .pageblock { background: #fff; border: 1px solid #e0dfda; border-radius: 12px; padding: 20px 24px; }
  .pno { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--muted, #8a857c); margin-bottom: 12px; }

  .md { font-size: 14.5px; line-height: 1.7; color: var(--ink, #1f1e1d); }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-weight: 700; margin: 1.1em 0 .5em; line-height: 1.3; }
  .md :global(h1) { font-size: 1.4em; }
  .md :global(h2) { font-size: 1.2em; }
  .md :global(h3) { font-size: 1.05em; }
  .md :global(p) { margin: .6em 0; }
  .md :global(ul), .md :global(ol) { margin: .6em 0; padding-left: 1.4em; }
  .md :global(li) { margin: .25em 0; }
  .md :global(a) { color: #c2683f; text-decoration: none; border-bottom: 1px solid #e3c4b6; }
  .md :global(a:hover) { border-bottom-color: #c2683f; }
  .md :global(table) { border-collapse: collapse; margin: .8em 0; width: 100%; font-size: .95em; }
  .md :global(th), .md :global(td) { border: 1px solid #e0dfda; padding: 6px 10px; text-align: left; }
  .md :global(th) { background: #f7f6f3; font-weight: 600; }
  .md :global(code) { background: #f0efed; padding: 1px 5px; border-radius: 4px; font-size: .9em; }
  .md :global(pre) { background: #f7f6f3; padding: 12px 14px; border-radius: 8px; overflow-x: auto; }
  .md :global(pre code) { background: transparent; padding: 0; }
  .md :global(blockquote) { border-left: 3px solid #e0dfda; padding-left: 14px; color: var(--muted, #8a857c); margin: .7em 0; }

  .side-sec { margin-bottom: 22px; }
  .side-sec h3 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--muted, #8a857c); margin-bottom: 10px; }
  .side-empty { font-size: 12.5px; color: var(--muted, #8a857c); }
  .side-link { display: block; background: #fff; border: 1px solid #e0dfda; border-radius: 9px; padding: 9px 12px; text-decoration: none; color: inherit; margin-bottom: 7px; transition: border-color .12s; }
  .side-link:hover { border-color: #c2683f; }
  .sl-name { display: block; font-size: 13px; font-weight: 500; color: #46443f; }
  .sl-reason { display: block; font-size: 11px; color: var(--muted, #8a857c); margin-top: 2px; }
</style>
