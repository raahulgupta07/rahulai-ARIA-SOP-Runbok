<script lang="ts">
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth';

  let email = $state('');
  let password = $state('');
  let showPw = $state(false);
  let busy = $state(false);
  let err = $state('');

  async function doLogin() {
    if (!email.trim() || !password) return;
    busy = true; err = '';
    try {
      await auth.login(email.trim(), password, true);   // from_admin → escape hatch
      goto('/');
    } catch (e: any) {
      err = e?.message || 'Sign-in failed';
    } finally {
      busy = false;
    }
  }
</script>

<div class="apage">
  <div class="topbar"><img src="/brand-logo.png" alt="CityAgent Aria" class="brandlogo" /></div>

  <div class="stage">
    <!-- LEFT: admin form -->
    <div class="left">
      <div class="acard">
        <div class="ahead">
          <span class="akey"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.6 7.6a5 5 0 1 1-7 7 5 5 0 0 1 7-7zm3 .1L22 7l-3-3"/></svg></span>
          Admin sign-in
        </div>
        <h1>Super-admin access</h1>
        <p class="asub">Local password — works even when SSO / LDAP are the only methods for everyone else.</p>

        {#if err}<div class="aerr">{err}</div>{/if}

        <input class="ainp" placeholder="Admin email" bind:value={email}
               onkeydown={(e) => e.key === 'Enter' && doLogin()} />
        <div class="apw">
          <input class="ainp" type={showPw ? 'text' : 'password'} placeholder="Password" bind:value={password}
                 onkeydown={(e) => e.key === 'Enter' && doLogin()} />
          <button type="button" class="ashow" onclick={() => (showPw = !showPw)}>{showPw ? 'Hide' : 'Show'}</button>
        </div>

        <button class="abtn" disabled={busy} onclick={doLogin}>{busy ? '…' : 'Sign in as admin'}</button>
        <a class="aback" href="/login">← Back to company sign-in</a>
      </div>
    </div>

    <!-- RIGHT: dark break-glass panel -->
    <div class="right">
      <div class="rpanel">
        <span class="orb"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/><circle cx="12" cy="16" r="1.4"/></svg></span>
        <div class="rttl">Restricted area</div>
        <div class="rtxt">A break-glass entrance for the super-admin. Keep this URL private.</div>
        <div class="rpts">
          <div class="rpt"><svg viewBox="0 0 24 24" fill="none" stroke="#c2683f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.6 7.6a5 5 0 1 1-7 7 5 5 0 0 1 7-7zm3 .1L22 7l-3-3"/></svg> Local password — always available</div>
          <div class="rpt"><svg viewBox="0 0 24 24" fill="none" stroke="#c2683f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/></svg> Every sign-in is audit-logged</div>
          <div class="rpt"><svg viewBox="0 0 24 24" fill="none" stroke="#c2683f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5z"/><path d="M9 12l2 2 4-4"/></svg> Admins only — others rejected</div>
        </div>
      </div>
    </div>
  </div>

  <div class="foot">© 2026 CityAgent Aria · Runbooks &amp; IT Assistance</div>
</div>

<style>
  .apage { min-height: 100vh; background: #fff; color: #211f1c; display: flex; flex-direction: column;
    --clay:#c2683f; --border:#ececea; --muted:#8a857c;
    --serif:var(--font); }
  .topbar { padding: 22px 28px; }
  .brandlogo { height: 68px; width: auto; display: block; }
  .stage { flex: 1; display: grid; grid-template-columns: 1fr 1fr; gap: 48px; max-width: 1180px;
    margin: 0 auto; width: 100%; padding: 6px 40px 24px; align-items: center; }

  .left { max-width: 560px; }
  .acard { border: 1px solid var(--border); border-radius: 16px; padding: 24px; max-width: 440px; }
  .ahead { display: flex; align-items: center; gap: 9px; font-size: 14px; font-weight: 600; color: #46443f; }
  .akey { width: 28px; height: 28px; border-radius: 8px; background: #1c1a17; color: #fff; display: grid; place-items: center; }
  h1 { font-family: var(--serif); font-weight: 400; font-size: 46px; line-height: 1.08; letter-spacing: -1px; margin: 16px 0 12px; }
  .asub { font-size: 16px; color: #5c594f; line-height: 1.6; margin-bottom: 18px; }
  .aerr { background: #fbeae6; color: #b03a22; font-size: 13px; padding: 9px 12px; border-radius: 9px; margin-bottom: 12px; }
  .ainp { width: 100%; height: 48px; padding: 0 14px; border: 1px solid var(--border); border-radius: 11px; font-size: 15px; color: #211f1c; outline: none; background: #fff; }
  .ainp:focus { border-color: var(--clay); }
  .apw { position: relative; margin-top: 10px; }
  .ashow { position: absolute; right: 13px; top: 14px; font-size: 13px; color: var(--muted); background: none; border: 0; cursor: pointer; font-weight: 500; }
  .abtn { width: 100%; height: 50px; margin-top: 16px; border: 0; border-radius: 11px; background: #1c1a17; color: #fff; font-size: 14.5px; font-weight: 500; cursor: pointer; }
  .abtn:hover { background: #000; } .abtn:disabled { opacity: .6; cursor: default; }
  .aback { display: block; text-align: center; margin-top: 16px; font-size: 12.5px; color: var(--muted); text-decoration: none; }
  .aback:hover { color: var(--clay); }

  .right { height: 560px; }
  .rpanel { height: 100%; border: 1px solid #2a2620; border-radius: 22px; background: #1c1a17;
    background-image: linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);
    background-size: 30px 30px; display: flex; flex-direction: column; justify-content: center; gap: 14px; padding: 34px 30px; }
  .orb { width: 56px; height: 56px; border-radius: 50%; background: var(--clay); display: grid; place-items: center; align-self: flex-start;
    box-shadow: 0 0 0 0 rgba(194,104,63,.5); animation: orb-pulse 2.6s ease-in-out infinite; }
  @keyframes orb-pulse { 0%,100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(255,160,90,.45); } 50% { transform: scale(1.07); box-shadow: 0 0 0 13px rgba(255,160,90,0); } }
  .rttl { font-size: 17px; font-weight: 600; color: #fff; }
  .rtxt { color: #bdb6a8; font-size: 12.5px; line-height: 1.5; }
  .rpts { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
  .rpt { display: flex; align-items: center; gap: 9px; color: #cabfb0; font-size: 12.5px; }
  .rpt svg { width: 15px; height: 15px; flex: 0 0 auto; }

  .foot { color: var(--muted); font-size: 12.5px; text-align: center; padding: 16px 20px 22px; }

  @media (max-width: 900px) { .stage { grid-template-columns: 1fr; align-items: center; } .right { display: none; } .left { max-width: none; margin: 0 auto; } }
  @media (max-width: 640px) {
    .topbar { padding: 12px 18px; } .brandlogo { height: 40px; }
    .stage { padding: 0 18px 12px; }
    h1 { font-size: 24px; line-height: 1.2; margin: 8px 0; }
    .asub { font-size: 14px; line-height: 1.45; margin-bottom: 12px; }
    .acard { padding: 16px; max-width: none; }
    .ainp { height: 42px; }
    .foot { padding: 8px 18px 12px; font-size: 11px; }
  }
  @media (prefers-reduced-motion: reduce) { .orb { animation: none; } }
</style>
