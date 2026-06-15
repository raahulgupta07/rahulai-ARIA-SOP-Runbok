<script lang="ts">
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>(null);
  let saved = $state('');
  let ldapTest = $state('');
  let pane = $state<'methods' | 'ldap' | 'sso'>('methods');

  $effect(() => { if (!cfg) api.adminGetAuthConfig().then((c) => (cfg = c)); });

  async function save() {
    saved = 'saving…';
    try { cfg = await api.adminSaveAuthConfig(cfg); saved = 'Saved ✓'; setTimeout(() => (saved = ''), 1800); }
    catch (e: any) { saved = 'Error: ' + e.message; }
  }
  async function testLdap() {
    ldapTest = 'testing…';
    try { const r = await api.adminTestLdap(cfg); ldapTest = r.ok ? 'Connection OK ✓' : 'Failed: ' + r.error; }
    catch (e: any) { ldapTest = 'Error: ' + e.message; }
  }
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  {#if cfg}
  <div class="px-7 py-6">
    <!-- action bar -->
    <div class="flex items-center justify-end gap-3 mb-5">
      <span class="text-xs" style="color:var(--muted)">{saved}</span>
      <button class="btn pri" onclick={save}>Save changes</button>
    </div>

    <!-- top pill tabs -->
    <div class="toptabs">
      <button class="ptab" class:on={pane === 'methods'} onclick={() => (pane = 'methods')}>Methods</button>
      <button class="ptab" class:on={pane === 'ldap'} onclick={() => (pane = 'ldap')}>LDAP / AD <span class="dot" style="background:{cfg.enable_ldap ? '#5fa463' : '#cbc6ba'}"></span></button>
      <button class="ptab" class:on={pane === 'sso'} onclick={() => (pane = 'sso')}>SSO (OIDC) <span class="dot" style="background:{cfg.enable_oidc ? '#5fa463' : '#cbc6ba'}"></span></button>
    </div>
    <div class="tabsub">{pane === 'methods' ? 'Toggles & provisioning' : pane === 'ldap' ? 'Directory bind' : 'Keycloak · Azure · Google'}</div>

    <div class="grid grid-cols-1 gap-6">
      <!-- active pane -->
      <div class="min-w-0">
        {#if pane === 'methods'}
          <Section title="Local login" desc="Toggle what appears on the login page. Keep at least one method on.">
            <Row label="Local accounts (email + password)" hint="Built-in users, bcrypt passwords">
              <Toggle bind:checked={cfg.enable_local} />
            </Row>
            <Row label="Allow self sign-up" hint="Off = only admins create local users">
              <Toggle bind:checked={cfg.enable_signup} />
            </Row>
            <Row label="LDAP / Active Directory" hint="Bind against your directory">
              <Toggle bind:checked={cfg.enable_ldap} />
            </Row>
            <Row label="SSO (OIDC — Keycloak / Azure / Google)" hint="Single sign-on via OpenID Connect">
              <Toggle bind:checked={cfg.enable_oidc} />
            </Row>
          </Section>

          <Section title="Provisioning" desc="How new directory and SSO users are created on their first sign-in.">
            <Row label="Default role for new LDAP / SSO users" hint="Applied on first login">
              <select class="sel" bind:value={cfg.default_role}><option value="user">user</option><option value="pending">pending (approve first)</option><option value="admin">admin</option></select>
            </Row>
            <Row label="First user becomes admin" hint="Whoever signs in first owns the instance">
              <Toggle bind:checked={cfg.first_user_admin} />
            </Row>
            <Row label="Merge accounts by email" hint="One person = one account across LDAP & SSO. Only merges a VERIFIED email — leave OFF if your provider doesn't verify emails (account-takeover risk).">
              <Toggle bind:checked={cfg.merge_by_email} />
            </Row>
          </Section>

        {:else if pane === 'ldap'}
          <Section title="LDAP" desc="Service-account bind → search user → rebind to verify the password.">
            {#snippet actions()}
              {#if cfg.enable_ldap}<span class="on-b">enabled</span>{:else}<span class="off">disabled</span>{/if}
            {/snippet}
            <div style="opacity:{cfg.enable_ldap ? 1 : 0.5}">
              <div class="g2">
                <label class="fg"><span>Host</span><input bind:value={cfg.ldap.host} placeholder="ldap://ad.company.local" /></label>
                <label class="fg"><span>Port</span><input type="number" bind:value={cfg.ldap.port} /></label>
              </div>
              <label class="fg"><span>Bind DN</span><input bind:value={cfg.ldap.bind_dn} placeholder="cn=svc,ou=Service,dc=company,dc=local" /></label>
              <label class="fg"><span>Bind password</span><input type="password" bind:value={cfg.ldap.bind_password} /></label>
              <div class="g2">
                <label class="fg"><span>Base DN</span><input bind:value={cfg.ldap.base_dn} placeholder="ou=Staff,dc=company,dc=local" /></label>
                <label class="fg"><span>User filter <span class="hint">AD: (sAMAccountName=&#123;username&#125;)</span></span><input bind:value={cfg.ldap.user_filter} /></label>
              </div>
              <div class="g2">
                <label class="fg"><span>Email attribute</span><input bind:value={cfg.ldap.email_attr} /></label>
                <label class="fg"><span>Name attribute</span><input bind:value={cfg.ldap.name_attr} /></label>
              </div>
              <div class="flex gap-2 items-center mt-1">
                <button class="btn" onclick={testLdap}>Test connection</button>
                <span class="text-xs" style="color:var(--muted)">{ldapTest}</span>
              </div>
            </div>
          </Section>

        {:else}
          <Section title="Single sign-on (SSO)" desc="Works with any OIDC provider — Keycloak, Microsoft Entra / Azure AD, Google.">
            {#snippet actions()}
              {#if cfg.enable_oidc}<span class="on-b">enabled</span>{:else}<span class="off">disabled</span>{/if}
            {/snippet}
            <div style="opacity:{cfg.enable_oidc ? 1 : 0.5}">
              <label class="fg"><span>Issuer URL</span><input bind:value={cfg.oidc.issuer} placeholder="https://keycloak.company.com/realms/docsensei" /></label>
              <div class="g2">
                <label class="fg"><span>Client ID</span><input bind:value={cfg.oidc.client_id} /></label>
                <label class="fg"><span>Client secret</span><input type="password" bind:value={cfg.oidc.client_secret} /></label>
              </div>
              <label class="fg"><span>Scopes</span><input bind:value={cfg.oidc.scopes} /></label>
              <label class="fg"><span>Redirect URI <span class="hint">register this at your provider</span></span>
                <input readonly style="color:var(--muted)" value="{location.origin}/api/auth/oidc/callback" /></label>
              <p class="hs" style="margin-top:8px;margin-bottom:0">Entra / Azure issuer: <code>https://login.microsoftonline.com/&#123;tenant&#125;/v2.0</code></p>
            </div>
          </Section>
        {/if}
      </div>
    </div>
  </div>
  {/if}
</div>

<style>
  /* top pill tabs (match Embed page) */
  .toptabs{display:flex; flex-wrap:wrap; gap:6px; border-bottom:1px solid var(--border); padding-bottom:12px;}
  .ptab{display:inline-flex; align-items:center; gap:7px; font-size:13px; font-weight:600; padding:7px 15px; border-radius:999px; border:none; cursor:pointer; background:transparent; color:var(--muted); transition:background .12s, color .12s;}
  .ptab:hover{background:#f0efed; color:var(--ink);}
  .ptab.on{background:var(--clay); color:#fff;}
  .tabsub{font-size:12px; color:var(--muted); margin:12px 0 14px;}
  .dot{width:7px; height:7px; border-radius:50%; display:inline-block;}
  .hs{font-size:12.5px; color:var(--muted); margin-bottom:13px;}
  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .off{font-size:9.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; padding:1px 6px; border-radius:5px; background:#f0efed; color:var(--muted);}
  .on-b{font-size:9.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; padding:1px 6px; border-radius:5px; background:#e3f0e3; color:#3f7a45;}
  .sel{height:36px; border:1px solid var(--border); border-radius:8px; padding:0 10px; background:#fff; font-size:13px;}
  .fg{display:block; padding:10px 0;} .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none;}
  .fg input:focus{border-color:var(--clay);}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}
  code{font-size:11.5px; background:#f3f3f1; color:var(--clay-dk); padding:1px 5px; border-radius:5px;}
  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
</style>
