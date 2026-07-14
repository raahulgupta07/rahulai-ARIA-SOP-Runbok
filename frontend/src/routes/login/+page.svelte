<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth';
  import { api } from '$lib/api';
  import WhatsNew from '$lib/WhatsNew.svelte';
  import { brand, loadBrand } from '$lib/brand';

  let ver = $state<any>(null);
  let verOpen = $state(false);

  // runtime white-label — login has its OWN local --clay token, so feed it the
  // brand accent + brand strings/logo, falling back to today's defaults.
  let accent = $derived($brand?.accent || '#1a1a18');
  let accentSoft = $derived($brand ? `color-mix(in srgb, ${$brand.accent} 12%, #fff)` : '#f3f3f1');
  let brandName = $derived($brand?.name || 'CityAgent Aria');
  let brandLogo = $derived($brand?.logo_url || '/brand-logo.png');
  let brandTagline = $derived(
    $brand?.tagline ||
    'Your runbook intelligence — SOPs, provisioning & user admin, answered with the source page.'
  );
  let brandFooter = $derived(
    $brand?.footer || '© 2026 CityAgent Aria · Runbooks & IT Assistance · EN · မြန်မာ'
  );

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

  let ldapDir = $state<string | undefined>(undefined);   // selected LDAP directory id
  async function doLdap() {
    persistRemember();
    err = ''; info = ''; busy = true;
    try { await auth.ldap(email, password, ldapDir); await finish(); }
    catch (e: any) { err = e.message; } finally { busy = false; }
  }

  function doSso(pid?: string) { window.location.href = auth.ssoUrl(pid); }
  function openLdap(dirId?: string) { ldapDir = dirId; ldapMode = true; }

  function focusEmail() { emailEl?.focus(); }

  onMount(() => {
    loadBrand();
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
    auth.config().then((c) => {
      cfg = c;
      // LDAP-first: when LDAP is enabled it is the PRIMARY login; local email
      // becomes the secondary "Continue with email instead" option.
      if (c.enable_ldap) { ldapMode = true; ldapDir = c.ldap_dirs?.[0]?.id; }
    }).catch(() => {});
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

<div class="page" style="--clay:{accent}; --peach:{accentSoft};">
  <div class="topbar"><img src={brandLogo} alt={brandName} class="brandlogo" /></div>

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
      <h1>Good {greet},<br />sign in to {brandName}</h1>
      <p class="sub">{brandTagline}</p>

      {#if stats}
        <div class="stat">
          <span class="gdot"></span>
          {stats.docs} runbooks · {stats.pages} pages · {stats.sections} sections · data {stats.date}
        </div>
      {/if}

      <div class="card">
        {#if err}<div class="msg err">{err}</div>{/if}
        {#if info}<div class="msg info">{info}</div>{/if}

        {#if (cfg.enable_local && !ldapMode) || ldapMode}
          {#if mode === 'signup' && !ldapMode}
            <input class="inp" placeholder="Full name" bind:value={name} />
          {/if}

          <input class="inp" bind:this={emailEl} placeholder={ldapMode ? 'Username or email' : 'Email'} bind:value={email}
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
        {/if}

        {#if (cfg.sso_providers?.length) || (ldapMode ? cfg.enable_local : (cfg.ldap_dirs?.length))}
          <div class="or">OR</div>
        {/if}

        <!-- SSO buttons are ALWAYS shown (not gated behind ldapMode) -->
        {#if cfg.enable_oidc}
          {#each (cfg.sso_providers ?? []) as p, i}
            <button class="btn tinted" class:primary={!cfg.enable_local && !ldapMode && i === 0} onclick={() => doSso(p.id)}>
              {#if p.provider === 'microsoft'}
                <svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" fill="#f25022"/><rect x="13" y="3" width="8" height="8" fill="#7fba00"/><rect x="3" y="13" width="8" height="8" fill="#00a4ef"/><rect x="13" y="13" width="8" height="8" fill="#ffb900"/></svg>
              {:else if p.provider === 'google'}
                <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M21.6 12.2c0-.6-.1-1.2-.2-1.8H12v3.4h5.4a4.6 4.6 0 0 1-2 3v2.5h3.2c1.9-1.7 3-4.3 3-7.1z"/><path fill="#34A853" d="M12 22c2.7 0 5-1 6.6-2.7l-3.2-2.5c-.9.6-2 1-3.4 1-2.6 0-4.8-1.7-5.6-4.1H3.1v2.6A10 10 0 0 0 12 22z"/><path fill="#FBBC05" d="M6.4 13.7a6 6 0 0 1 0-3.8V7.3H3.1a10 10 0 0 0 0 9z"/><path fill="#EA4335" d="M12 6.1c1.5 0 2.8.5 3.8 1.5l2.8-2.8A10 10 0 0 0 3.1 7.3l3.3 2.6C7.2 7.8 9.4 6.1 12 6.1z"/></svg>
              {:else if p.provider === 'okta'}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="8"/></svg>
              {:else}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2l-2 2m-7.6 7.6a5 5 0 1 1-7 7 5 5 0 0 1 7-7zm3 .1L22 7l-3-3"/></svg>
              {/if}
              {p.label}
            </button>
          {/each}
        {/if}

        {#if ldapMode}
          {#if cfg.enable_local}
            <button class="btn" onclick={() => { ldapMode = false; ldapDir = undefined; }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v16H4z M4 8l8 5 8-5"/></svg>
              Continue with email instead
            </button>
          {/if}
        {:else}
          {#each (cfg.ldap_dirs ?? []) as d}
            <button class="btn" onclick={() => openLdap(d.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4"/></svg>
              Continue with {d.name}
            </button>
          {/each}
        {/if}

        {#if mode === 'signup' && !ldapMode && cfg.enable_local}
          <div class="alt">Have an account? <button class="link" onclick={() => (mode = 'login')}>Sign in</button></div>
        {/if}

        <!-- Admin escape hatch is NOT advertised publicly. It only surfaces as a
             recovery link when no sign-in method is enabled; otherwise admins reach
             it directly at /login/admin. -->
        {#if !cfg.enable_local && !cfg.enable_ldap && !cfg.enable_oidc}
          <div class="msg info">No sign-in methods are enabled.</div>
          <a class="adminlink" href="/login/admin">Admin sign-in →</a>
        {/if}
      </div>
    </div>

    <!-- RIGHT animated capability panel -->
    <div class="right">
      <div class="panel">
        <div class="plive"><span class="plive-dot"></span> live · answering from your runbooks</div>
        <div class="bubble">{prompts[promptIdx]}</div>
        <div class="answer-rich">Finance lead → AP. Steps 1–4 on <span class="cite">p.6</span> of the Refund SOP.</div>
        <div class="typing"><span class="td"></span><span class="td"></span><span class="td"></span></div>

        <div class="chips">
          {#each tiles as tile, i}
            <div class="chip2 {hotTile === i ? 'hot' : ''}">
              <span class="ic">{tile.icon}</span><span class="t">{tile.t}</span>
            </div>
          {/each}
        </div>

        <div class="pstats">
          <span><b>{stats?.docs ?? 27}</b> runbooks</span>
          <span><b>{stats?.pages ?? 231}</b> pages</span>
          <span><b>SSO</b> ready</span>
        </div>
      </div>
    </div>
  </div>

  <div class="foot">{brandFooter}</div>
</div>

<style>
  .page{
    --clay:#1a1a18; --clay-soft:#dcdcd8; --ink:#211F1C; --muted:#8a857c;
    --border:#ececea; --line:#ececea; --paper:#fff; --cream:#ffffff; --peach:#f3f3f1;
    --serif:var(--font);
    background:#fff; color:var(--ink); min-height:100vh; display:flex; flex-direction:column;
    overflow-x:hidden;   /* kill any right-panel / horizontal bleed on small screens */
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
  /* when SSO is the only sign-in method, promote the first provider to a solid primary */
  .btn.tinted.primary{background:#1c1b18; color:#fff; border-color:#1c1b18; height:50px; font-weight:600;}
  .btn.tinted.primary:hover{background:#000;}
  .btn.clay{background:#1c1b18; color:#fff; border-color:#1c1b18; height:50px;}
  .btn.clay:hover{background:#000;} .btn:disabled{opacity:.5;}
  .btn svg{width:16px; height:16px;}
  .or{display:flex; align-items:center; gap:12px; color:var(--muted); font-size:11px; margin:14px 0; letter-spacing:1px;}
  .or::before,.or::after{content:""; flex:1; height:1px; background:var(--border);}
  .alt{text-align:center; font-size:13px; color:var(--muted); margin-top:8px;}
  .link{background:none; border:0; color:var(--clay); cursor:pointer; font-size:13px; font-weight:500;}

  .foot{color:var(--muted); font-size:12.5px; text-align:center; padding:16px 20px 22px; margin-top:auto;}

  /* RIGHT panel — dark, animated, content-rich */
  .right{position:relative; height:560px;}
  .panel{position:absolute; inset:0; border:1px solid #2a2620; border-radius:22px; background:#1c1a17; overflow:hidden;
    background-image:linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);
    background-size:30px 30px; padding:30px 28px; display:flex; flex-direction:column; gap:13px;}
  .plive{display:flex; align-items:center; gap:8px; color:#bdb6a8; font-size:12px;}
  .plive-dot{width:8px; height:8px; border-radius:50%; background:#ffba7d; animation:lg-pulse 1.8s ease-in-out infinite;}
  @keyframes lg-pulse{0%,100%{transform:scale(1);opacity:.9}50%{transform:scale(1.18);opacity:1}}
  .bubble{align-self:flex-start; max-width:80%; background:#2a2620; color:#ece6da; padding:10px 14px; border-radius:14px 14px 14px 4px; font-size:13px; line-height:1.4;}
  .answer-rich{align-self:flex-end; max-width:82%; background:var(--clay); color:#fff; padding:10px 14px; border-radius:14px 14px 4px 14px; font-size:13px; line-height:1.5;}
  .answer-rich .cite{background:rgba(255,255,255,.25); padding:1px 5px; border-radius:4px; font-size:12px;}
  .typing{align-self:flex-start; background:#2a2620; padding:10px 14px; border-radius:14px; display:flex; gap:5px;}
  .typing .td{width:6px; height:6px; border-radius:50%; background:#bdb6a8; animation:lg-dot 1.2s infinite;}
  .typing .td:nth-child(2){animation-delay:.15s;} .typing .td:nth-child(3){animation-delay:.3s;}
  @keyframes lg-dot{0%,80%,100%{transform:translateY(0);opacity:.45}40%{transform:translateY(-4px);opacity:1}}

  .chips{display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:auto;}
  .chip2{display:flex; align-items:center; gap:9px; background:#23201b; border:1px solid #312c25; border-radius:10px; padding:10px 12px; transition:.3s;}
  .chip2 .ic{font-size:16px; line-height:1;}
  .chip2 .t{font-size:12px; color:#cabfb0;}
  .chip2.hot{border-color:var(--clay); background:#2e251f; box-shadow:0 0 0 1px var(--clay);}
  .chip2.hot .t{color:#ffd9c4;}
  .pstats{display:flex; gap:18px; border-top:1px solid #2a2620; padding-top:12px; color:#8a8276; font-size:11.5px;}
  .pstats b{color:#ece6da; font-weight:600;}
  @media (prefers-reduced-motion: reduce){ .plive-dot,.typing .td{animation:none;} }

  /* version pill + popover */
  .adminlink{display:block; text-align:center; margin-top:16px; font-size:12.5px; color:var(--muted); text-decoration:none;}
  .adminlink:hover{color:var(--clay);}
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
