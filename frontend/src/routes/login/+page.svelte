<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth';
  import { api } from '$lib/api';
  import WhatsNew from '$lib/WhatsNew.svelte';

  let ver = $state<any>(null);
  let verOpen = $state(false);

  let cfg = $state<any>({ enable_local: true, enable_signup: true, enable_ldap: false, enable_oidc: false });
  let mode = $state<'login' | 'signup'>('login');
  let ldapMode = $state(false);

  let email = $state('');
  let password = $state('');
  let name = $state('');
  let showPw = $state(false);
  let remember = $state(true);
  let err = $state('');
  let info = $state('');
  let busy = $state(false);

  let stats = $state<any>(null);
  let emailEl: HTMLInputElement | null = null;

  // time-of-day greeting
  const greet = (() => {
    const h = new Date().getHours();
    return h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
  })();

  // animated demo state
  const prompts = [
    'Disable a leaving user account',
    'How do I create a new site in Gold Central?',
    'Night batch job ကျသွားရင် ဘာလုပ်ရမလဲ?',
    'Where is the refund approval flow?'
  ];
  let promptIdx = $state(0);
  let hotTile = $state(0);

  const tiles = [
    { icon: '🔍', t: 'Search SOP' },
    { icon: '📄', t: 'Read page' },
    { icon: '⬇', t: 'Upload doc' },
    { icon: '🖼', t: 'Vision read' },
    { icon: '🕐', t: 'Teach a fact' },
    { icon: '文A', t: 'Translate MY' }
  ];

  async function finish() {
    const u = await auth.me();
    if (u) {
      let dest = '/';
      try { dest = (JSON.parse(localStorage.getItem('aria_prefs') || '{}').landing) || '/'; } catch {}
      goto(dest);
    }
  }

  function persistRemember() {
    try { localStorage.setItem('docsensei_remember', remember ? '1' : '0'); } catch {}
  }

  async function doLocal() {
    persistRemember();
    err = ''; info = ''; busy = true;
    try {
      if (mode === 'signup') {
        const r = await auth.signup(email, password, name);
        if (r.pending) { info = 'Account created — awaiting admin approval.'; mode = 'login'; }
        else await finish();
      } else {
        await auth.login(email, password);
        await finish();
      }
    } catch (e: any) { err = e.message; } finally { busy = false; }
  }

  async function doLdap() {
    persistRemember();
    err = ''; info = ''; busy = true;
    try { await auth.ldap(email, password); await finish(); }
    catch (e: any) { err = e.message; } finally { busy = false; }
  }

  function doSso() { window.location.href = auth.ssoUrl(); }

  function focusEmail() { emailEl?.focus(); }

  onMount(() => {
    // OIDC redirect drops the token in the URL fragment
    if (location.hash.startsWith('#token=')) {
      auth.setToken(decodeURIComponent(location.hash.slice(7)));
      history.replaceState(null, '', '/login');
      finish();
      return;
    }
    const params = new URLSearchParams(location.search);
    if (params.get('pending')) info = 'Account awaiting admin approval.';
    if (params.get('error')) err = params.get('error') || '';

    if (auth.isAuthed()) { finish(); }
    auth.config().then((c) => (cfg = c)).catch(() => {});
    api.version().then((d) => (ver = d)).catch(() => {});
    api.publicStats().then((s) => (stats = s)).catch(() => {});
  });

  // bubble prompt cycle + tile highlight cycle
  $effect(() => {
    const iv = setInterval(() => {
      promptIdx = (promptIdx + 1) % prompts.length;
      hotTile = (hotTile + 1) % tiles.length;
    }, 2600);
    return () => clearInterval(iv);
  });
</script>

<div class="page">
  <div class="topbar"><img src="/brand-logo.png" alt="CityAgent Aria" class="brandlogo" /></div>

  {#if ver}
    <div class="verwrap">
      <button class="verpill" onclick={() => (verOpen = !verOpen)}>
        <span class="vdot"></span>
        v{ver.version}<span class="verextra"> · {ver.sha} · {String(ver.built ?? '').slice(0, 10)}</span>
      </button>
      {#if verOpen}
        <div class="verpop">
          <button class="verx" onclick={() => (verOpen = false)} aria-label="Close">✕</button>
          <WhatsNew compact />
        </div>
      {/if}
    </div>
  {/if}

  <div class="stage">
    <!-- LEFT -->
    <div class="left">
      <h1>Good {greet},<br />sign in to CityAgent Aria</h1>
      <p class="sub">Your runbook intelligence — SOPs, provisioning &amp; user admin, answered with the source page.</p>

      {#if stats}
        <div class="stat">
          <span class="gdot"></span>
          {stats.docs} runbooks · {stats.pages} pages · {stats.sections} sections · data {stats.date}
        </div>
      {/if}

      <div class="card">
        {#if err}<div class="msg err">{err}</div>{/if}
        {#if info}<div class="msg info">{info}</div>{/if}

        {#if cfg.enable_local || ldapMode}
          {#if mode === 'signup' && !ldapMode}
            <input class="inp" placeholder="Full name" bind:value={name} />
          {/if}

          <input class="inp" bind:this={emailEl} placeholder={ldapMode ? 'Username' : 'Email'} bind:value={email}
                 onkeydown={(e) => e.key === 'Enter' && (ldapMode ? doLdap() : doLocal())} />

          <div class="pwrap">
            <input class="inp" type={showPw ? 'text' : 'password'} placeholder="Password" bind:value={password}
                   onkeydown={(e) => e.key === 'Enter' && (ldapMode ? doLdap() : doLocal())} />
            <button type="button" class="show" onclick={() => (showPw = !showPw)}>{showPw ? 'Hide' : 'Show'}</button>
          </div>

          <label class="remember">
            <input type="checkbox" bind:checked={remember} />
            <span class="box {remember ? 'on' : ''}" aria-hidden="true">
              {#if remember}<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>{/if}
            </span>
            Remember me on this device
          </label>

          <button class="btn clay" disabled={busy} onclick={() => (ldapMode ? doLdap() : doLocal())}>
            {busy ? '…' : ldapMode ? 'Continue with LDAP' : mode === 'signup' ? 'Create account' : 'Continue with email'}
          </button>

          {#if cfg.enable_oidc || cfg.enable_ldap}
            <div class="or">OR</div>
          {/if}

          {#if cfg.enable_oidc}
            <button class="btn tinted" onclick={doSso}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2l-2 2m-7.6 7.6a5 5 0 1 1-7 7 5 5 0 0 1 7-7zm3 .1L22 7l-3-3"/></svg>
              Continue with SSO
            </button>
          {/if}
          {#if cfg.enable_ldap}
            <button class="btn" onclick={() => (ldapMode = !ldapMode)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4"/></svg>
              {ldapMode ? 'Use local account' : 'Continue with LDAP / AD'}
            </button>
          {/if}

          {#if mode === 'signup' && !ldapMode}
            <div class="alt">Have an account? <button class="link" onclick={() => (mode = 'login')}>Sign in</button></div>
          {/if}
        {/if}
      </div>
    </div>

    <!-- RIGHT animated capability panel -->
    <div class="right">
      <div class="panel">
        <div class="bubble">{prompts[promptIdx]}</div>

        <div class="grid">
          {#each tiles as tile, i}
            <div class="tile {hotTile === i ? 'hot' : ''}">
              <span class="ic">{tile.icon}</span>
              <span class="t">{tile.t}</span>
              {#if hotTile === i}
                <svg class="cursor" viewBox="0 0 24 24" fill="#1c1b18"><path d="M4 2l16 7-7 2-2 7z"/></svg>
              {/if}
            </div>
          {/each}
        </div>

        <div class="dock">
          <div class="chip">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
            <div><b>New Site Creation · GOLD</b><small>28 SOPs · 253 pages indexed</small></div>
          </div>
          <div class="plus" aria-hidden="true">+</div>
          <button class="go" onclick={focusEmail}>Let's go →</button>
        </div>
      </div>
    </div>
  </div>

  <div class="foot">© 2026 CityAgent Aria · Runbooks &amp; IT Assistance · EN · မြန်မာ</div>
</div>

<style>
  .page{
    --clay:#1a1a18; --clay-soft:#dcdcd8; --ink:#211F1C; --muted:#8a857c;
    --border:#ececea; --line:#ececea; --paper:#fff; --cream:#ffffff; --peach:#f3f3f1;
    --serif:'Tiempos Headline','Tiempos Text',Georgia,'Times New Roman',serif;
    background:#fff; color:var(--ink); min-height:100vh; display:flex; flex-direction:column;
  }
  .topbar{display:flex; align-items:center; gap:11px; padding:22px 28px;}
  .brandlogo{height:68px; width:auto; display:block;}

  .stage{flex:1; display:grid; grid-template-columns:1fr 1fr; gap:48px; max-width:1180px;
    margin:0 auto; width:100%; padding:6px 40px 24px; align-items:center;}
  @media(max-width:900px){ .stage{grid-template-columns:1fr;} .right{display:none;} }

  .left{max-width:560px; text-align:left;}
  h1{font-family:var(--serif); font-weight:400; font-size:46px; line-height:1.08; letter-spacing:-1px; margin-bottom:16px;}
  .sub{color:#5c594f; font-size:16px; line-height:1.6; max-width:430px; margin-bottom:16px;}
  .stat{display:flex; align-items:center; gap:8px; font-size:13px; color:var(--muted); margin-bottom:22px;}
  .gdot{width:8px; height:8px; border-radius:999px; background:#2faf5a; box-shadow:0 0 0 3px rgba(47,175,90,.18);}

  .card{background:var(--paper); border:1px solid var(--border); border-radius:16px; padding:24px; max-width:440px;}
  .msg{font-size:13px; padding:9px 12px; border-radius:9px; margin-bottom:12px;}
  .msg.err{background:#FBEAE5; color:#9a3a24; border:1px solid #ecc9bf;}
  .msg.info{background:#FBF3E2; color:#7a6428; border:1px solid #ecdcae;}

  .inp{width:100%; height:48px; border:1px solid var(--border); border-radius:11px; padding:0 14px;
    font-size:15px; outline:none; background:#fff; margin-bottom:10px;}
  .inp:focus{border-color:var(--clay);}
  .pwrap{position:relative;}
  .pwrap .show{position:absolute; right:13px; top:14px; font-size:13px; color:var(--muted); background:none; border:0; cursor:pointer; font-weight:500;}

  .remember{display:flex; align-items:center; gap:9px; font-size:13.5px; color:#54514a; cursor:pointer; margin:4px 0 16px;}
  .remember input{position:absolute; opacity:0; width:0; height:0;}
  .remember .box{width:18px; height:18px; border-radius:6px; border:1px solid var(--border); background:#fff;
    display:flex; align-items:center; justify-content:center; flex:0 0 auto; transition:.15s;}
  .remember .box.on{background:var(--clay); border-color:var(--clay);}
  .remember .box svg{width:12px; height:12px;}

  .btn{width:100%; height:48px; border-radius:11px; font-size:14.5px; font-weight:500; cursor:pointer;
    display:flex; align-items:center; justify-content:center; gap:9px; border:1px solid var(--border);
    background:#fff; color:var(--ink); transition:.15s; margin-bottom:9px;}
  .btn:hover{background:#f4f2ec;} .btn.tinted{background:#EDE9DF;}
  .btn.clay{background:#1c1b18; color:#fff; border-color:#1c1b18; height:50px;}
  .btn.clay:hover{background:#000;} .btn:disabled{opacity:.5;}
  .btn svg{width:16px; height:16px;}
  .or{display:flex; align-items:center; gap:12px; color:var(--muted); font-size:11px; margin:14px 0; letter-spacing:1px;}
  .or::before,.or::after{content:""; flex:1; height:1px; background:var(--border);}
  .alt{text-align:center; font-size:13px; color:var(--muted); margin-top:8px;}
  .link{background:none; border:0; color:var(--clay); cursor:pointer; font-size:13px; font-weight:500;}

  .foot{color:var(--muted); font-size:12.5px; text-align:center; padding:16px 20px 22px; margin-top:auto;}

  /* RIGHT panel */
  .right{position:relative; height:560px;}
  .panel{position:absolute; inset:0; border:1px solid var(--border); border-radius:22px; background:var(--paper); overflow:hidden;
    background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
    background-size:30px 30px; box-shadow:0 18px 50px rgba(33,31,28,.08);}
  .bubble{position:absolute; top:54px; left:42px; right:42px; background:var(--clay); color:#fff; padding:12px 17px; border-radius:14px;
    font-size:14.5px; line-height:1.4; box-shadow:0 8px 22px rgba(194,104,63,.28);}
  .bubble::after{content:""; position:absolute; left:26px; bottom:-6px; width:12px; height:12px; background:var(--clay); transform:rotate(45deg);}

  .grid{position:absolute; top:160px; left:42px; right:42px; display:grid; grid-template-columns:repeat(3,1fr); gap:14px;}
  .tile{position:relative; background:#fff; border:1px solid var(--border); border-radius:12px; padding:14px; height:94px;
    display:flex; flex-direction:column; justify-content:space-between; transition:.25s;}
  .tile .ic{font-size:20px; line-height:1;}
  .tile .t{font-size:13.5px; color:#39372f; line-height:1.3;}
  .tile.hot{border-color:var(--clay); background:var(--peach); box-shadow:0 8px 22px rgba(194,104,63,.16);}
  .tile .cursor{position:absolute; right:-6px; bottom:-6px; width:18px; height:18px; z-index:9; pointer-events:none;
    filter:drop-shadow(0 1px 2px rgba(0,0,0,.3));}

  .dock{position:absolute; left:42px; right:42px; bottom:40px; display:flex; gap:10px; align-items:center;}
  .chip{flex:1; background:#fff; border:1px solid var(--border); border-radius:13px; padding:11px 14px; display:flex; align-items:center; gap:10px;}
  .chip svg{width:16px; height:16px; color:var(--muted);}
  .chip b{font-size:13.5px; font-weight:500;} .chip small{display:block; font-size:11.5px; color:var(--muted);}
  .plus{width:42px; height:42px; border:1px solid var(--border); border-radius:11px; background:#fff; display:flex; align-items:center; justify-content:center; font-size:20px; color:var(--muted);}
  .go{background:var(--clay-soft); color:#a8542f; border:0; border-radius:11px; padding:0 16px; height:42px; font-size:13.5px; font-weight:600; display:flex; align-items:center; gap:6px; cursor:pointer;}
  .go:hover{background:#e0b9a4;}

  /* version pill + popover */
  .verwrap{position:absolute; top:22px; right:28px; z-index:20;}
  /* mobile: trim the version pill to just the number + shrink the logo so they don't collide */
  @media (max-width:640px){
    .brandlogo{height:40px;}
    .verwrap{top:12px; right:12px;}
    .verpill{font-size:11px; padding:4px 9px;}
    .verextra{display:none;}
    /* fit the whole login on one mobile screen — no scroll, content centred */
    .topbar{padding:12px 18px;}
    .stage{padding:0 18px 12px; gap:0; align-items:center;}
    .left{width:100%;}
    .left h1{font-size:24px; line-height:1.2; margin-bottom:8px;}
    .sub{font-size:14px; line-height:1.45; margin-bottom:10px;}
    .stat{font-size:12px; margin-bottom:12px;}
    .card{padding:16px; max-width:none;}
    .inp{height:42px;}
    .remember{margin:8px 0;}
    .or{margin:8px 0;}
    .foot{padding:8px 18px 12px; font-size:11px;}
  }
  .verpill{display:inline-flex; align-items:center; gap:7px; cursor:pointer;
    background:#fff; border:1px solid var(--border); border-radius:999px;
    padding:6px 13px; font-size:12px; font-weight:500; color:var(--clay);
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    box-shadow:0 1px 3px rgba(0,0,0,.05);}
  .verpill:hover{background:#f3f3f1; border-color:#dcdcd8;}
  .vdot{width:6px; height:6px; border-radius:999px; background:var(--clay);}
  .verpop{position:absolute; top:calc(100% + 9px); right:0; width:300px; max-width:88vw;
    max-height:80vh; overflow-y:auto;
    background:var(--paper); border:1px solid var(--border); border-radius:14px;
    box-shadow:0 14px 34px rgba(33,31,28,.16); padding:16px 16px 18px;}
  .verx{position:absolute; top:9px; right:10px; background:none; border:0; cursor:pointer;
    color:var(--muted); font-size:13px; padding:3px 6px; border-radius:7px;}
  .verx:hover{background:#ece9e0;}
</style>
