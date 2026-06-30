<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { page } from '$app/stores';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then(u => me = u).catch(() => {}); });

  let id = $derived(Number($page.params.id));

  let loading = $state(true);
  let failed = $state(false);
  let entity = $state<any>(null);
  let mentions = $state<any[]>([]);
  let claims = $state<any[]>([]);
  let related = $state<any[]>([]);
  let conflicts = $state<any[]>([]);

  $effect(() => {
    const eid = id;
    if (!me || !eid) return;
    let alive = true;
    loading = true; failed = false;
    api.wikiEntity(eid).then((d: any) => {
      if (!alive) return;
      entity = d?.entity || null;
      mentions = Array.isArray(d?.mentions) ? [...d.mentions] : [];
      claims = Array.isArray(d?.claims) ? [...d.claims] : [];
      related = Array.isArray(d?.related) ? [...d.related] : [];
      conflicts = Array.isArray(d?.conflicts) ? [...d.conflicts] : [];
      loading = false;
    }).catch(() => { if (alive) { failed = true; loading = false; } });
    return () => { alive = false; };
  });

  function kindColor(kind: string): string {
    const map: Record<string, string> = {
      code: '#7b6bd6', menu_path: '#2f8f83', system: '#3f7fb0',
      screen: '#c98a2e', field: '#3f8f5f', term: '#c2683f'
    };
    return map[kind] || '#8a857c';
  }
  function kindBg(kind: string): string { return kindColor(kind) + '1a'; }
</script>

<div class="wiki-wrap">
  {#if !me}
    <div class="wiki-loading">Loading…</div>
  {:else}
    <a class="back" href="/wiki">← Knowledge Base</a>

    {#if loading}
      <div class="wiki-loading">Loading entity…</div>
    {:else if failed || !entity}
      <div class="wiki-empty">Not found — this entity could not be loaded.</div>
    {:else}
      <header class="ehead">
        <h1>{entity.name}</h1>
        <span class="kchip" style="color:{kindColor(entity.kind)}; background:{kindBg(entity.kind)}">{entity.kind}</span>
      </header>

      {#if mentions.length === 0 && claims.length === 0 && related.length === 0 && conflicts.length === 0}
        <div class="wiki-empty">No mentions, claims or relationships recorded for this entity yet.</div>
      {/if}

      {#if conflicts.length > 0}
        <section>
          <h2 class="warn">Conflicts</h2>
          <div class="conflicts">
            {#each conflicts as c (c.id)}
              <a class="conflict" href="/dashboard/contradictions">
                <div class="cf-attr">{c.attribute}</div>
                <div class="cf-body">
                  <span class="cf-val">{c.value_a}</span>
                  <span class="cf-src">({c.doc_a_name})</span>
                  <span class="cf-vs">vs</span>
                  <span class="cf-val">{c.value_b}</span>
                  <span class="cf-src">({c.doc_b_name})</span>
                </div>
                <span class="cf-status">{c.status}</span>
              </a>
            {/each}
          </div>
        </section>
      {/if}

      {#if mentions.length > 0}
        <section>
          <h2>Appears in</h2>
          <div class="rows">
            {#each mentions as m (m.doc_id + '-' + (m.page_id ?? ''))}
              <a class="row" href={`/wiki/doc/${m.doc_id}`}>
                <span class="row-name">{m.doc_name}</span>
                {#if m.category}<span class="cat">{m.category}</span>{/if}
              </a>
            {/each}
          </div>
        </section>
      {/if}

      {#if claims.length > 0}
        <section>
          <h2>Asserted values</h2>
          <table class="claims">
            <thead>
              <tr><th>Attribute</th><th>Value</th><th>Source</th></tr>
            </thead>
            <tbody>
              {#each claims as c}
                <tr class:superseded={!!c.superseded_at}>
                  <td>{c.attribute}</td>
                  <td>
                    <span class="cv">{c.value}</span>
                    {#if c.superseded_at}<span class="sup-tag">superseded</span>{/if}
                  </td>
                  <td>
                    <a class="src-doc" href={`/wiki/doc/${c.doc_id}`}>{c.doc_name}</a>
                    {#if c.page_no != null}<span class="pg">p.{c.page_no}</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </section>
      {/if}

      {#if related.length > 0}
        <section>
          <h2>Related</h2>
          <div class="chips">
            {#each related as r (r.id)}
              <a class="rchip" href={`/wiki/entity/${r.id}`}>
                <span class="rk-dot" style="background:{kindColor(r.kind)}"></span>
                {r.name}
                <span class="shared">{r.shared}</span>
              </a>
            {/each}
          </div>
        </section>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .wiki-wrap {
    height: 100%; overflow-y: auto; max-width: 900px; margin: 0 auto;
    padding: 24px 24px 60px; color: var(--ink, #1f1e1d);
  }
  .wiki-loading, .wiki-empty { padding: 40px 8px; color: var(--muted, #8a857c); font-size: 14px; }
  .back { display: inline-block; font-size: 13px; color: var(--muted, #8a857c); text-decoration: none; margin-bottom: 18px; }
  .back:hover { color: #c2683f; }

  .ehead { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; flex-wrap: wrap; }
  .ehead h1 { font-size: 30px; font-weight: 700; letter-spacing: -.02em; }
  .kchip { font-size: 12px; font-weight: 600; padding: 3px 11px; border-radius: 20px; }

  section { margin-bottom: 30px; }
  section h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--muted, #8a857c); margin-bottom: 12px; }
  section h2.warn { color: #c0492f; }

  .rows { display: flex; flex-direction: column; gap: 7px; }
  .row {
    display: flex; align-items: center; gap: 10px; background: #fff;
    border: 1px solid #e0dfda; border-radius: 10px; padding: 11px 14px;
    text-decoration: none; color: inherit; transition: border-color .12s;
  }
  .row:hover { border-color: #c2683f; }
  .row-name { font-size: 14px; font-weight: 500; }
  .cat { margin-left: auto; font-size: 11px; font-weight: 600; color: #3f7fb0; background: #3f7fb01a; padding: 2px 9px; border-radius: 20px; }

  table.claims { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e0dfda; border-radius: 10px; overflow: hidden; font-size: 13.5px; }
  table.claims th { text-align: left; font-weight: 600; color: var(--muted, #8a857c); font-size: 11px; text-transform: uppercase; letter-spacing: .03em; padding: 10px 14px; border-bottom: 1px solid #e0dfda; }
  table.claims td { padding: 10px 14px; border-bottom: 1px solid #f0efed; vertical-align: top; }
  table.claims tr:last-child td { border-bottom: none; }
  tr.superseded .cv { text-decoration: line-through; opacity: .6; }
  .sup-tag { margin-left: 8px; font-size: 10px; font-weight: 600; color: #c98a2e; background: #c98a2e1a; padding: 1px 7px; border-radius: 20px; vertical-align: middle; }
  .src-doc { color: #46443f; text-decoration: none; }
  .src-doc:hover { color: #c2683f; }
  .pg { margin-left: 6px; font-size: 11px; color: var(--muted, #8a857c); }

  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .rchip {
    display: inline-flex; align-items: center; gap: 7px; background: #fff;
    border: 1px solid #e0dfda; border-radius: 20px; padding: 6px 12px;
    text-decoration: none; color: #46443f; font-size: 13px; transition: border-color .12s, color .12s;
  }
  .rchip:hover { border-color: #c2683f; color: #c2683f; }
  .rk-dot { width: 8px; height: 8px; border-radius: 50%; }
  .shared { font-size: 11px; color: var(--muted, #8a857c); background: #f0efed; border-radius: 20px; padding: 0 7px; }

  .conflicts { display: flex; flex-direction: column; gap: 9px; }
  .conflict {
    display: block; background: #fff; border: 1px solid #f0c9be; border-left: 3px solid #c0492f;
    border-radius: 10px; padding: 12px 15px; text-decoration: none; color: inherit; position: relative;
  }
  .conflict:hover { background: #fdf6f4; }
  .cf-attr { font-size: 13px; font-weight: 600; margin-bottom: 5px; }
  .cf-body { font-size: 13px; line-height: 1.6; padding-right: 70px; }
  .cf-val { font-weight: 600; }
  .cf-src { color: var(--muted, #8a857c); }
  .cf-vs { color: #c0492f; font-weight: 600; margin: 0 4px; }
  .cf-status { position: absolute; top: 12px; right: 14px; font-size: 10px; font-weight: 600; color: #c0492f; background: #c0492f1a; padding: 2px 8px; border-radius: 20px; text-transform: uppercase; }
</style>
