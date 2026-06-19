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

{#snippet copyIcon()}
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
{/snippet}

{#snippet checkIcon()}
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
{/snippet}

{#snippet codecard(label: string, body: string, id: string)}
  <div class="codecard">
    <div class="cc-head">
      <span class="cc-lbl">{label}</span>
      <button class="btn cp" onclick={() => copy(body, id)}>
        {#if copied === id}{@render checkIcon()}Copied{:else}{@render copyIcon()}Copy{/if}
      </button>
    </div>
    <pre><code>{body}</code></pre>
  </div>
{/snippet}

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <p class="ttlsub">Drive the agent from your own apps, scripts or back-end services over plain HTTP — the same REST API the web UI uses.</p>

    <!-- AUTH -->
    <div class="lbl">Authentication</div>
    <p class="desc">Every call (except public page images) needs a <code>Bearer</code> token. There are two ways to get one.</p>

    <div class="card sub">
      <div class="subhd">
        <span class="num">A</span>
        <div>
          <div class="sub-ttl">Member token (a person signs in)</div>
          <div class="sub-desc">POST credentials to the login endpoint and use the returned JWT as a Bearer token.</div>
        </div>
      </div>
      {@render codecard('POST /api/auth/login', loginBlk, 'login')}
    </div>

    <div class="card sub">
      <div class="subhd">
        <span class="num">B</span>
        <div>
          <div class="sub-ttl">Embed key (server-to-server / SSO)</div>
          <div class="sub-desc">
            Create a <code>pk_live_</code> / <code>sk_live_</code> key pair in
            <a href="/settings/embed">Settings &rarr; Embed Widget</a>, then exchange the secret for a
            short-lived visitor token. Good for letting your own back-end speak for a known user.
          </div>
        </div>
      </div>
      {@render codecard('POST /api/embed/token', embedBlk, 'embed')}
    </div>

    <!-- ASK -->
    <div class="lbl">Ask the agent (streaming)</div>
    <p class="desc">
      The core endpoint. Streams back <b>NDJSON</b> &mdash; one JSON object per line in order:
      <code>meta</code> &rarr; many <code>token</code> events &rarr; a final <code>done</code> (with sources, tokens &amp; cost).
      <code>mode</code> is <code>auto</code> | <code>quick</code> | <code>deep</code>.
    </p>
    <div class="card">
      {@render codecard('POST /api/ask/stream', askBlk, 'ask')}
    </div>

    <!-- DOCS -->
    <div class="lbl">List documents</div>
    <p class="desc">Read the indexed runbook corpus (status, page counts, last-used, ingest progress).</p>
    <div class="card">
      {@render codecard('GET /api/documents', docsBlk, 'docs')}
    </div>

    <!-- WIDGET -->
    <div class="lbl">Embed widget (one-liner)</div>
    <p class="desc">
      No code at all &mdash; drop this on any page to get the floating chat bubble. Create a public key (and
      enable any-origin / plug-and-play) in <a href="/settings/embed">Settings &rarr; Embed Widget</a>.
    </p>
    <div class="card">
      {@render codecard('HTML snippet', widgetBlk, 'widget')}
    </div>

    <div class="tip">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/></svg>
      <span>Full reference, SSO passthrough and rate-limit details live in <code>EMBED.md</code> at the repository root.</span>
    </div>
  </div>
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px; margin-bottom:20px; line-height:1.5;}

  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin:24px 0 8px;}
  .desc{font-size:13px; color:var(--muted); line-height:1.6; margin-bottom:12px;}
  .desc a{color:var(--clay); text-decoration:none; font-weight:500;}
  .desc a:hover{text-decoration:underline;}
  code{font-size:11.5px; background:#f3f3f1; color:var(--clay-dk); padding:1px 5px; border-radius:5px;}

  .card{border:1px solid var(--border); border-radius:13px; padding:15px; background:#fff;}
  .card + .card{margin-top:12px;}
  .card.sub{margin-bottom:12px;}

  .subhd{display:flex; align-items:flex-start; gap:11px; margin-bottom:12px;}
  .num{flex:0 0 auto; width:26px; height:26px; border-radius:8px; background:#f4f3f0; color:var(--ink);
    display:grid; place-items:center; font-size:12.5px; font-weight:700;}
  .sub-ttl{font-size:13.5px; font-weight:600; color:var(--ink);}
  .sub-desc{font-size:12.5px; color:var(--muted); line-height:1.55; margin-top:3px;}
  .sub-desc a{color:var(--clay); text-decoration:none; font-weight:500;}
  .sub-desc a:hover{text-decoration:underline;}

  /* code blocks: header strip + pre body */
  .codecard{border:1px solid var(--border); border-radius:10px; overflow:hidden; background:var(--cream);}
  .cc-head{display:flex; align-items:center; justify-content:space-between; gap:10px;
    padding:8px 12px; border-bottom:1px solid var(--border); background:#fff;}
  .cc-lbl{font-size:11.5px; font-weight:600; color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.02em;}
  .codecard pre{margin:0; padding:13px 15px; overflow-x:auto;}
  .codecard code{background:transparent; color:var(--muted); padding:0; font-size:12px; line-height:1.6; white-space:pre;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}

  .btn{height:30px; padding:0 11px; border-radius:8px; font-size:12px; font-weight:600; cursor:pointer;
    border:1px solid var(--border); background:#fff; color:var(--ink);
    display:inline-flex; align-items:center; gap:6px; transition:background .12s, border-color .12s;}
  .btn:hover{background:#f0efed; border-color:#cfcabf;}
  .cp{flex:0 0 auto;}

  .tip{display:flex; gap:9px; align-items:flex-start; background:#eaf0f7; color:#3a5878; border-radius:11px;
    padding:11px 13px; font-size:12.5px; line-height:1.5; margin-top:22px;}
  .tip svg{flex:0 0 auto; margin-top:1px;}
  .tip code{background:rgba(255,255,255,.55); color:#2f4a66;}

  @media (max-width:640px){
    .codecard pre{overflow-x:auto;}
  }
</style>
