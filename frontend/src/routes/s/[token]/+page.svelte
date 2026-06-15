<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api';
  import { md } from '$lib/md';

  let token = $derived($page.params.token);
  let data = $state<any>(null);
  let err = $state('');
  let needAuth = $state(false);

  onMount(load);
  async function load() {
    err = ''; needAuth = false; data = null;
    try { data = await api.getShare(token); }
    catch (e: any) {
      if (/unauthor|sign in|401/i.test(e?.message || '')) needAuth = true;
      else err = e?.message || 'This link is invalid or was revoked.';
    }
  }
</script>

<div class="wrap">
  <header class="hd">
    <a href="/" class="brand"><img src="/brand-logo.png" alt="City Agent Aria" class="logo" /></a>
    <span class="tag">Shared answer</span>
  </header>

  <main class="body">
    {#if needAuth}
      <div class="msg">
        <h2>Sign in to view</h2>
        <p>This shared answer is visible to members only.</p>
        <a class="btn" href="/login">Sign in</a>
      </div>
    {:else if err}
      <div class="msg"><h2>Link unavailable</h2><p>{err}</p><a class="btn" href="/">Go to Aria</a></div>
    {:else if data}
      <div class="q">{data.question}</div>
      <div class="a md">{@html md(data.answer)}</div>
      {#if data.pages?.length}
        <div class="srcwrap">
          <span class="srclbl">Sources</span>
          <div class="coins">
            {#each data.pages.slice(0, 12) as p, i}
              <a class="coin" href={`/api/pages/${p.page_id ?? p.id}`} target="_blank" rel="noopener"
                 title={`${p.doc_name || p.doc || ''} · page ${p.page_no ?? ''}`}>{i + 1}</a>
            {/each}
          </div>
        </div>
      {/if}
    {:else}
      <div class="msg muted">Loading…</div>
    {/if}
    <div class="foot">Answered by City Agent Aria · cites the source page · verify critical steps against the document</div>
  </main>
</div>

<style>
  :global(html, body) { margin: 0; background: var(--cream, #fff); }
  .wrap { min-height: 100vh; display: flex; flex-direction: column; font-family: 'Hanken Grotesk', -apple-system, system-ui, sans-serif; color: var(--ink, #1a1a18); }
  .hd { display: flex; align-items: center; gap: 12px; padding: 14px 22px; border-bottom: 1px solid var(--border, #ececea); }
  .logo { height: 30px; }
  .tag { font-size: 12px; color: var(--muted, #8b8b85); text-transform: uppercase; letter-spacing: .05em; }
  .body { flex: 1; width: 100%; max-width: 760px; margin: 0 auto; padding: 34px 22px 60px; }
  .q { font-family: 'Fraunces', Georgia, serif; font-size: 24px; line-height: 1.3; color: var(--ink); margin-bottom: 18px; }
  .a { font-size: 15px; line-height: 1.65; color: var(--ink); }
  .srcwrap { margin-top: 22px; padding-top: 14px; border-top: 1px solid var(--border, #ececea); }
  .srclbl { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted, #8b8b85); font-weight: 600; margin-bottom: 8px; }
  .coins { display: flex; flex-wrap: wrap; gap: 7px; }
  .coin { width: 26px; height: 26px; border-radius: 7px; background: #fdeee8; color: #d97757; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; text-decoration: none; border: 1px solid #f6d9cd; }
  .coin:hover { background: #d97757; color: #fff; }
  .msg { text-align: center; padding: 60px 0; }
  .msg h2 { font-family: 'Fraunces', serif; font-size: 22px; margin: 0 0 8px; }
  .msg p { color: var(--muted, #8b8b85); margin: 0 0 18px; }
  .btn { display: inline-block; background: var(--ink, #1a1a18); color: #fff; padding: 9px 20px; border-radius: 9px; text-decoration: none; font-size: 14px; font-weight: 600; }
  .foot { margin-top: 34px; font-size: 11.5px; color: var(--muted, #8b8b85); text-align: center; }
</style>
