<script lang="ts">
  import { onMount } from 'svelte';

  let host = $state('');
  let copied = $state('');
  onMount(() => { host = location.origin; });

  function copy(text: string, id: string) {
    navigator.clipboard.writeText(text).then(() => {
      copied = id;
      setTimeout(() => { if (copied === id) copied = ''; }, 1600);
    }).catch(() => {});
  }

  // code blocks (built reactively off host)
  let loginBlk = $derived(
`curl -X POST ${host}/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"you@company.com","password":"••••••••"}'
# -> { "token": "eyJhbGci..." }   use as: Authorization: Bearer <token>`);

  let embedBlk = $derived(
`curl -X POST ${host}/api/embed/token \\
  -H "Authorization: Bearer sk_live_YOUR_SECRET" \\
  -H "Content-Type: application/json" \\
  -d '{"visitor_id":"user-123"}'
# -> short-lived visitor token (use as Bearer for the chat endpoints)`);

  let askBlk = $derived(
`curl -N -X POST ${host}/api/ask/stream \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"q":"How do I create a new site?","mode":"auto"}'`);

  let docsBlk = $derived(
`curl ${host}/api/documents \\
  -H "Authorization: Bearer YOUR_TOKEN"`);

  // built as a JS string so the parser never treats it as markup
  let widgetBlk = $derived(
`<script src="${host}/widget.js" data-key="pk_live_..." async><\/script>`);
</script>

<div class="px-7 py-6 wrap">
  <p class="lead">
    Drive the agent from your own apps, scripts or back-end services over plain HTTP.
    Everything below is the same REST API the web UI uses &mdash; point any HTTP client at it.
  </p>

  <!-- AUTH -->
  <section class="sec">
    <h2>1 &middot; Authentication</h2>
    <p class="desc">Every call (except public page images) needs a <code>Bearer</code> token. There are two ways to get one.</p>

    <div class="sub">
      <div class="subhd">A. Member token (a person signs in)</div>
      <p class="desc">POST credentials to the login endpoint and use the returned JWT as a Bearer token.</p>
      <div class="codecard">
        <button class="cp" onclick={() => copy(loginBlk, 'login')}>{copied === 'login' ? 'Copied' : 'Copy'}</button>
        <pre><code>{loginBlk}</code></pre>
      </div>
    </div>

    <div class="sub">
      <div class="subhd">B. Embed key (server-to-server / SSO)</div>
      <p class="desc">
        Create a <code>pk_live_</code> / <code>sk_live_</code> key pair in
        <a href="/settings/embed">Settings &rarr; Embed Widget</a>, then exchange the secret for a
        short-lived visitor token. Good for letting your own back-end speak for a known user.
      </p>
      <div class="codecard">
        <button class="cp" onclick={() => copy(embedBlk, 'embed')}>{copied === 'embed' ? 'Copied' : 'Copy'}</button>
        <pre><code>{embedBlk}</code></pre>
      </div>
    </div>
  </section>

  <!-- ASK -->
  <section class="sec">
    <h2>2 &middot; Ask the agent (streaming)</h2>
    <p class="desc">
      The core endpoint. Streams back <b>NDJSON</b> &mdash; one JSON object per line in order:
      <code>meta</code> &rarr; many <code>token</code> events &rarr; a final <code>done</code> (with sources, tokens &amp; cost).
      <code>mode</code> is <code>auto</code> | <code>quick</code> | <code>deep</code>.
    </p>
    <div class="codecard">
      <button class="cp" onclick={() => copy(askBlk, 'ask')}>{copied === 'ask' ? 'Copied' : 'Copy'}</button>
      <pre><code>{askBlk}</code></pre>
    </div>
  </section>

  <!-- DOCS -->
  <section class="sec">
    <h2>3 &middot; List documents</h2>
    <p class="desc">Read the indexed runbook corpus (status, page counts, last-used, ingest progress).</p>
    <div class="codecard">
      <button class="cp" onclick={() => copy(docsBlk, 'docs')}>{copied === 'docs' ? 'Copied' : 'Copy'}</button>
      <pre><code>{docsBlk}</code></pre>
    </div>
  </section>

  <!-- WIDGET -->
  <section class="sec">
    <h2>4 &middot; Embed widget (one-liner)</h2>
    <p class="desc">
      No code at all &mdash; drop this on any page to get the floating chat bubble. Create a public key (and
      enable any-origin / plug-and-play) in <a href="/settings/embed">Settings &rarr; Embed Widget</a>.
    </p>
    <div class="codecard">
      <button class="cp" onclick={() => copy(widgetBlk, 'widget')}>{copied === 'widget' ? 'Copied' : 'Copy'}</button>
      <pre><code>{widgetBlk}</code></pre>
    </div>
  </section>

  <p class="foot">Full reference, SSO passthrough and rate-limit details live in <code>EMBED.md</code> at the repository root.</p>
</div>

<style>
  .wrap { max-width: 880px; }
  .lead { font-size: 14px; color: var(--ink); line-height: 1.6; margin-bottom: 6px; }
  .sec { padding: 22px 0; border-top: 1px solid #efefec; }
  .sec:first-of-type { border-top: none; padding-top: 10px; }
  h2 { font-size: 15px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
  .desc { font-size: 13px; color: var(--muted); line-height: 1.6; margin-bottom: 12px; }
  .sub { margin-top: 16px; }
  .subhd { font-size: 12.5px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
  .desc a { color: var(--clay); text-decoration: none; font-weight: 500; }
  .desc a:hover { text-decoration: underline; }
  code { font-size: 11.5px; background: #f3f3f1; color: var(--clay-dk); padding: 1px 5px; border-radius: 5px; }
  .codecard { position: relative; background: #1f1e1d; border-radius: 10px; overflow: hidden; }
  .codecard pre { margin: 0; padding: 14px 16px; overflow-x: auto; }
  .codecard code { background: transparent; color: #e9e6df; padding: 0; font-size: 12px; line-height: 1.6; white-space: pre; }
  .cp { position: absolute; top: 8px; right: 8px; height: 26px; padding: 0 10px; font-size: 11px; font-weight: 600;
        color: #e9e6df; background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.16);
        border-radius: 6px; cursor: pointer; transition: background .12s; z-index: 1; }
  .cp:hover { background: rgba(255,255,255,.20); }
  .foot { font-size: 12.5px; color: var(--muted); margin-top: 22px; padding-top: 16px; border-top: 1px solid #efefec; }
  .foot code { font-size: 11.5px; }
</style>
