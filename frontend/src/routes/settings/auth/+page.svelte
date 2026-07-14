<script lang="ts">
  import { api } from '$lib/api';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  let cfg = $state<any>(null);
  let saved = $state('');
  let toast = $state('');                 // floating success/error toast
  let clean = $state('');                 // snapshot of last-saved config
  let pane = $state<'methods' | 'ldap' | 'sso'>('methods');

  // popup editor: { type:'sso'|'ldap', index:number(-1=new), draft:object }
  let edit = $state<any>(null);
  let modalTest = $state('');

  // unsaved-changes indicator: current config differs from the last save
  let dirty = $derived(!!cfg && JSON.stringify(cfg) !== clean);

  $effect(() => { if (!cfg) api.adminGetAuthConfig().then((c) => { cfg = normalize(c); clean = JSON.stringify(cfg); }); });

  // ensure the multi-provider arrays exist; migrate the legacy single oidc/ldap once
  function normalize(c: any) {
    c.oidc_providers = Array.isArray(c.oidc_providers) ? c.oidc_providers : [];
    c.ldap_directories = Array.isArray(c.ldap_directories) ? c.ldap_directories : [];
    // master on/off per method — default ON when unset
    c.sso_enabled = c.sso_enabled !== false;
    c.ldap_enabled = c.ldap_enabled !== false;
    if (!c.oidc_providers.length && c.oidc?.issuer) {
      c.oidc_providers = [{ id: 'default', name: (c.oidc.provider || 'SSO'), provider: c.oidc.provider || 'generic',
        label: c.oidc.label || '', issuer: c.oidc.issuer, client_id: c.oidc.client_id || '',
        client_secret: c.oidc.client_secret || '', scopes: c.oidc.scopes || 'openid email profile', enabled: !!c.enable_oidc }];
    }
    if (!c.ldap_directories.length && c.ldap?.host) {
      c.ldap_directories = [{ ...c.ldap, id: 'default', name: c.ldap.name || 'LDAP / AD', enabled: !!c.enable_ldap }];
    }
    return c;
  }
  function uid() { try { return crypto.randomUUID().slice(0, 8); } catch { return 'p' + Math.round(performance.now()); } }

  // ---- open the popup ----
  function newProvider() {
    edit = { type: 'sso', index: -1, draft: { id: uid(), name: 'New provider', provider: 'microsoft',
      label: '', issuer: '', client_id: '', client_secret: '', scopes: 'openid email profile', enabled: true } };
    modalTest = '';
  }
  function editProvider(i: number) { edit = { type: 'sso', index: i, draft: { ...cfg.oidc_providers[i] } }; modalTest = ''; }
  function newDirectory() {
    edit = { type: 'ldap', index: -1, draft: { id: uid(), name: 'New directory', host: '', port: 389,
      bind_dn: '', bind_password: '', base_dn: '', username_attr: 'sAMAccountName',
      email_attr: 'mail', name_attr: 'cn', search_filter: '', user_filter: '',
      use_ssl: false, start_tls: false, enabled: true } };
    modalTest = ''; advOpen = false;
  }
  function editDirectory(i: number) {
    edit = { type: 'ldap', index: i, draft: { ...cfg.ldap_directories[i] } }; modalTest = ''; advOpen = false;
  }

  // ---- LDAP modal UX: segmented TLS + advanced toggle
  let advOpen = $state(false);
  let secMode = $derived(edit?.type === 'ldap' && edit.draft
    ? (edit.draft.use_ssl ? 'ldaps' : edit.draft.start_tls ? 'starttls' : 'none') : 'none');
  function setSec(m: string) {
    if (!edit?.draft) return;
    edit.draft.use_ssl = m === 'ldaps';
    edit.draft.start_tls = m === 'starttls';
    edit.draft.port = m === 'ldaps' ? 636 : 389;
  }
  function closeEdit() { edit = null; modalTest = ''; }

  // ---- mutate config ----
  function applyEdit() {
    const key = edit.type === 'sso' ? 'oidc_providers' : 'ldap_directories';
    const list = [...cfg[key]];
    if (edit.index < 0) list.push(edit.draft); else list[edit.index] = edit.draft;
    cfg[key] = list;
  }
  async function saveEdit() { applyEdit(); closeEdit(); await save(); }
  function removeProvider(i: number) { cfg.oidc_providers = cfg.oidc_providers.filter((_: any, x: number) => x !== i); }
  function removeDirectory(i: number) { cfg.ldap_directories = cfg.ldap_directories.filter((_: any, x: number) => x !== i); }

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }
  async function save() {
    saved = 'saving…';
    try {
      cfg = normalize(await api.adminSaveAuthConfig(cfg));
      clean = JSON.stringify(cfg); saved = ''; flash('Saved ✓ — login methods updated');
    } catch (e: any) { saved = ''; flash('Error: ' + e.message); }
  }
  async function testModalDir() {
    modalTest = 'testing…';
    try { const r = await api.adminTestLdap(edit.draft); modalTest = r.ok ? 'Connection OK ✓' : 'Failed: ' + r.error; }
    catch (e: any) { modalTest = 'Error: ' + e.message; }
  }
  const ICONS = [['microsoft', 'Microsoft'], ['google', 'Google'], ['okta', 'Okta'], ['generic', 'SSO']];
  const enabledCount = (arr: any[]) => arr.filter((x) => x.enabled && (x.issuer || x.host)).length;
</script>

{#snippet pico(provider: string)}
  {#if provider === 'microsoft'}
    <svg width="22" height="22" viewBox="0 0 23 23" aria-hidden="true"><path fill="#f25022" d="M1 1h10v10H1z"/><path fill="#7fba00" d="M12 1h10v10H12z"/><path fill="#00a4ef" d="M1 12h10v10H1z"/><path fill="#ffb900" d="M12 12h10v10H12z"/></svg>
  {:else if provider === 'google'}
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 4.75c1.62 0 3.07.56 4.21 1.64l3.15-3.15C17.45 1.46 14.97.5 12 .5A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 6.68 9.14 4.75 12 4.75z"/></svg>
  {:else if provider === 'okta'}
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path fill="#007dc1" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 15a5 5 0 1 1 0-10 5 5 0 0 1 0 10z"/></svg>
  {:else}
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#6b6862" stroke-width="2" aria-hidden="true"><path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z"/><circle cx="12" cy="10" r="2.5"/><path d="M9 16c.5-1.5 1.7-2.5 3-2.5s2.5 1 3 2.5"/></svg>
  {/if}
{/snippet}

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  {#if cfg}
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">How people sign in — local, LDAP and single sign-on.</p>
      <div class="flex items-center gap-3">
        {#if dirty}<span class="unsaved">● Unsaved changes</span>{/if}
        <span class="text-xs" style="color:var(--muted)">{saved}</span>
        <button class="btn pri" class:nudge={dirty} onclick={save}>{saved ? 'Saving…' : 'Save changes'}</button>
      </div>
    </div>

    <div class="toptabs">
      <button class="ptab" class:on={pane === 'methods'} onclick={() => (pane = 'methods')}>Methods</button>
      <button class="ptab" class:on={pane === 'ldap'} onclick={() => (pane = 'ldap')}>LDAP / AD <span class="cnt">{cfg.ldap_directories.length}</span></button>
      <button class="ptab" class:on={pane === 'sso'} onclick={() => (pane = 'sso')}>SSO (OIDC) <span class="cnt">{cfg.oidc_providers.length}</span></button>
    </div>

    <div class="min-w-0">
      {#if pane === 'methods'}
        <!-- method status cards -->
        <div class="lbl">Sign-in methods</div>
        <div class="mcards">
          <div class="mcard">
            <div class="mc-top"><span class="mc-ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg></span>
              <Toggle bind:checked={cfg.enable_local} /></div>
            <div class="mc-nm">Local accounts</div>
            <div class="mc-sub">Email + password{cfg.enable_signup ? ' · self sign-up on' : ' · self sign-up off'}</div>
            <div class="mc-st" class:on={cfg.enable_local}>{cfg.enable_local ? '● Enabled' : '○ Disabled'}</div>
          </div>
          <div class="mcard">
            <div class="mc-top"><span class="mc-ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z"/><circle cx="12" cy="11" r="2"/></svg></span>
              <Toggle bind:checked={cfg.sso_enabled} /></div>
            <div class="mc-nm">SSO (OIDC)</div>
            <div class="mc-sub">{cfg.oidc_providers.length} provider{cfg.oidc_providers.length === 1 ? '' : 's'} · Microsoft · Google · Okta · Keycloak</div>
            {#if !cfg.sso_enabled}
              <div class="mc-st">○ Disabled</div>
            {:else if !cfg.oidc_providers.length}
              <div class="mc-st warn">⚠ On — add a provider</div>
            {:else}
              <div class="mc-st on">● Enabled</div>
            {/if}
            <button type="button" class="mc-link linkbtn" onclick={() => (pane = 'sso')}>Manage providers →</button>
          </div>
          <div class="mcard">
            <div class="mc-top"><span class="mc-ic"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/></svg></span>
              <Toggle bind:checked={cfg.ldap_enabled} /></div>
            <div class="mc-nm">LDAP / Active Directory</div>
            <div class="mc-sub">{cfg.ldap_directories.length} director{cfg.ldap_directories.length === 1 ? 'y' : 'ies'} · bind against your directory</div>
            {#if !cfg.ldap_enabled}
              <div class="mc-st">○ Disabled</div>
            {:else if !cfg.ldap_directories.length}
              <div class="mc-st warn">⚠ On — add a directory</div>
            {:else}
              <div class="mc-st on">● Enabled</div>
            {/if}
            <button type="button" class="mc-link linkbtn" onclick={() => (pane = 'ldap')}>{cfg.ldap_directories.length ? 'Manage directories →' : '+ Add directory'}</button>
          </div>
        </div>

        <div class="tip"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/></svg>
          <span>Front your AD with Keycloak/Entra and use <b>one SSO provider</b> to get AD logins + MFA — no separate LDAP needed.</span></div>

        <Section title="Provisioning" desc="How new directory and SSO users are created on first sign-in.">
          <Row label="Default role for new LDAP / SSO users" hint="Applied on first login">
            <select class="sel" bind:value={cfg.default_role}><option value="user">user</option><option value="pending">pending (approve first)</option><option value="admin">admin</option></select>
          </Row>
          <Row label="First user becomes admin" hint="Whoever signs in first owns the instance"><Toggle bind:checked={cfg.first_user_admin} /></Row>
          <Row label="Merge accounts by email" hint="One person = one account across LDAP & SSO. Only merges a VERIFIED email."><Toggle bind:checked={cfg.merge_by_email} /></Row>
        </Section>

      {:else if pane === 'sso'}
        <div class="lhead"><div><h3>SSO providers</h3><p>Each enabled provider shows as its own button on the login page.</p></div><button class="btn add" onclick={newProvider}>+ Add SSO provider</button></div>
        {#if !cfg.oidc_providers.length}<div class="empty">No SSO providers yet. Click "Add SSO provider".</div>{/if}
        {#each cfg.oidc_providers as p, i (p.id)}
          <div class="rowcard" style="opacity:{p.enabled ? 1 : 0.62}">
            <span class="ico">{@render pico(p.provider)}</span>
            <div class="meta">
              <div class="nm">{p.name || 'Untitled'}
                {#if !p.issuer || !p.client_id}<span class="st warn">⚠ incomplete</span>
                {:else if p.enabled}<span class="st on">● Enabled</span>
                {:else}<span class="st">○ Disabled</span>{/if}
              </div>
              <div class="sub">{p.issuer || 'Not configured — click Configure'}</div>
            </div>
            <Toggle bind:checked={p.enabled} />
            <button class="btn sm" onclick={() => editProvider(i)}>Configure</button>
            <button class="del" onclick={() => removeProvider(i)} title="Delete">✕</button>
          </div>
        {/each}

      {:else}
        <div class="lhead"><div><h3>LDAP / AD directories</h3><p>Bind against one or more directories. Each enabled one shows as a login button.</p></div><button class="btn add" onclick={newDirectory}>+ Add directory</button></div>
        {#if !cfg.ldap_directories.length}<div class="empty">No directories yet. Click "Add directory".</div>{/if}
        {#each cfg.ldap_directories as d, i (d.id)}
          <div class="rowcard" style="opacity:{d.enabled ? 1 : 0.62}">
            <span class="ico"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/></svg></span>
            <div class="meta">
              <div class="nm">{d.name || 'Untitled'}
                {#if !d.host}<span class="st warn">⚠ incomplete</span>
                {:else if d.enabled}<span class="st on">● Enabled</span>
                {:else}<span class="st">○ Disabled</span>{/if}
              </div>
              <div class="sub">{d.host ? `${d.host}:${d.port}` : 'Not configured — click Configure'}</div>
            </div>
            <Toggle bind:checked={d.enabled} />
            <button class="btn sm" onclick={() => editDirectory(i)}>Configure</button>
            <button class="del" onclick={() => removeDirectory(i)} title="Delete">✕</button>
          </div>
        {/each}
      {/if}
    </div>
  </div>
  {/if}

  <!-- ============ POPUP MODAL ============ -->
  {#if edit}
    <div class="scrim" onclick={closeEdit} role="presentation"></div>
    <div class="modal" role="dialog" aria-modal="true" style={edit.type === 'ldap' ? '--mw:820px' : ''}>
      <div class="m-head">
        <div class="m-ttl">{edit.index < 0 ? 'Add' : 'Configure'} {edit.type === 'sso' ? 'SSO provider' : 'LDAP / AD directory'}</div>
        <button class="m-x" onclick={closeEdit} aria-label="Close">✕</button>
      </div>

      <div class="m-body">
        {#if edit.type === 'sso'}
          <div class="fg"><span>Provider icon</span>
            <div class="picker">
              {#each ICONS as [v, l]}
                <button class="sw" class:on={edit.draft.provider === v} onclick={() => (edit.draft.provider = v)} title={l}>{@render pico(v)}</button>
              {/each}
            </div>
          </div>
          <label class="fg"><span>Display name <span class="hint">— shown on the login button</span></span><input bind:value={edit.draft.name} placeholder="Microsoft 365" /></label>
          <label class="fg"><span>Issuer URL</span><input bind:value={edit.draft.issuer} placeholder="https://login.microsoftonline.com/&#123;tenant&#125;/v2.0" /></label>
          <div class="g2">
            <label class="fg"><span>Client ID</span><input bind:value={edit.draft.client_id} /></label>
            <label class="fg"><span>Client secret</span><input type="password" bind:value={edit.draft.client_secret} /></label>
          </div>
          <div class="g2">
            <label class="fg"><span>Scopes</span><input bind:value={edit.draft.scopes} /></label>
            <label class="fg"><span>Button label <span class="hint">blank = auto</span></span><input bind:value={edit.draft.label} placeholder="Continue with Microsoft" /></label>
          </div>
          <div class="redirect">Redirect URI — register at your provider
            <code>{location.origin}/api/auth/oidc/callback</code></div>
        {:else}
          <div class="sect-h">1 · Connection</div>
          <div class="grid-conn">
            <label class="fg"><span>Label</span><input bind:value={edit.draft.name} placeholder="HQ Active Directory" /></label>
            <label class="fg"><span>Host</span><input bind:value={edit.draft.host} placeholder="10.16.73.150" /></label>
            <label class="fg"><span>Port</span><input type="number" bind:value={edit.draft.port} /></label>
            <div class="fg"><span>Security</span>
              <div class="seg">
                <button class="segb" class:on={secMode === 'none'} onclick={() => setSec('none')}>None</button>
                <button class="segb" class:on={secMode === 'starttls'} onclick={() => setSec('starttls')}>StartTLS</button>
                <button class="segb" class:on={secMode === 'ldaps'} onclick={() => setSec('ldaps')}>LDAPS</button>
              </div>
            </div>
          </div>

          <div class="sect-h">2 · Service account <span class="hint">— how Aria reads the directory</span></div>
          <div class="g2w">
            <label class="fg"><span>Bind DN</span><input bind:value={edit.draft.bind_dn} placeholder="cn=svc,ou=Service,dc=chl,dc=local" /></label>
            <label class="fg"><span>Base DN</span><input bind:value={edit.draft.base_dn} placeholder="dc=chl,dc=local" /></label>
          </div>
          <label class="fg" style="max-width:340px"><span>Bind password</span><input type="password" bind:value={edit.draft.bind_password} /></label>

          <div class="sect-h">3 · How users log in <span class="hint">— username OR full email both resolve automatically</span></div>
          <div class="grid3">
            <label class="fg"><span>Username attribute</span><input bind:value={edit.draft.username_attr} placeholder="sAMAccountName" /></label>
            <label class="fg"><span>Email attribute</span><input bind:value={edit.draft.email_attr} placeholder="userPrincipalName" /></label>
            <label class="fg"><span>Name attribute</span><input bind:value={edit.draft.name_attr} placeholder="cn" /></label>
          </div>

          <button class="advtog" onclick={() => (advOpen = !advOpen)}>{advOpen ? '▾' : '▸'} Advanced options</button>
          {#if advOpen}
            <div class="g2">
              <label class="fg"><span>Extra search filter <span class="hint">— optional, ANDed</span></span><input bind:value={edit.draft.search_filter} placeholder="(&(objectClass=user)(userPrincipalName=*))" /></label>
              <label class="fg"><span>Custom user filter <span class="hint">— overrides default; needs &#123;username&#125;</span></span><input bind:value={edit.draft.user_filter} placeholder="(leave blank for the smart default)" /></label>
            </div>
          {/if}
        {/if}
      </div>

      <div class="m-foot">
        {#if edit.type === 'ldap'}
          <button class="btn" onclick={testModalDir}>Test connection</button>
        {:else}<span></span>{/if}
        <div class="m-rt">
          {#if modalTest}<span class="testmsg" class:ok={modalTest.includes('OK')}>{modalTest}</span>{/if}
          <button class="btn ghost" onclick={closeEdit}>Cancel</button>
          <button class="btn pri" onclick={saveEdit}>Save {edit.type === 'sso' ? 'provider' : 'directory'}</button>
        </div>
      </div>
    </div>
  {/if}

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .ttl{font-family:var(--font); font-size:22px; color:var(--ink);}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .unsaved{font-size:12px; font-weight:600; color:#9a6a16;}
  .btn.nudge{box-shadow:0 0 0 3px rgba(194,104,63,.22);}
  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%); z-index:90;
    background:#1c1a17; color:#fff; font-size:13px; font-weight:500; padding:11px 18px; border-radius:11px;
    box-shadow:0 10px 30px rgba(40,35,30,.28); animation:tin .2s ease-out;}
  @keyframes tin{from{opacity:0; transform:translate(-50%,8px);}to{opacity:1; transform:translate(-50%,0);}}

  .toptabs{display:flex; flex-wrap:wrap; gap:6px; border-bottom:1px solid var(--border); padding-bottom:12px; margin-bottom:18px;}
  .ptab{display:inline-flex; align-items:center; gap:7px; font-size:13px; font-weight:600; padding:7px 15px; border-radius:999px; border:none; cursor:pointer; background:transparent; color:var(--muted); transition:background .12s, color .12s;}
  .ptab:hover{background:#f0efed; color:var(--ink);}
  .ptab.on{background:var(--clay); color:#fff;}
  .cnt{font-size:11px; font-weight:700; min-width:18px; height:18px; padding:0 5px; border-radius:9px; background:rgba(0,0,0,.12); display:inline-flex; align-items:center; justify-content:center;}
  .ptab.on .cnt{background:rgba(255,255,255,.28);}

  /* method status cards */
  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:10px;}
  .mcards{display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:14px;}
  .mcard{border:1px solid var(--border); border-radius:13px; padding:15px; background:#fff; text-align:left;}
  .mcard.click{cursor:pointer; transition:border-color .12s, box-shadow .12s;}
  .mcard.click:hover{border-color:#cfcabf;}
  .mcard.focus{border:2px solid #426693;}
  .mc-top{display:flex; align-items:center; justify-content:space-between;}
  .mc-ic{width:36px; height:36px; border-radius:9px; background:#f4f3f0; display:grid; place-items:center;}
  .mc-nm{font-size:14px; font-weight:600; margin-top:11px; color:var(--ink);}
  .mc-sub{font-size:12px; color:var(--muted); margin-top:2px;}
  .mc-st{font-size:11.5px; color:var(--muted); margin-top:9px;}
  .mc-st.on{color:#2f8f5f;}
  .mc-st.warn{color:#c98a2e;}
  .mc-link{font-size:11.5px; color:#426693; margin-top:9px; font-weight:500;}
  .linkbtn{display:block; background:none; border:none; padding:0; cursor:pointer; font-family:inherit;}
  .linkbtn:hover{text-decoration:underline;}
  .badge{font-size:11px; font-weight:600; background:#f0efed; color:var(--muted); padding:3px 9px; border-radius:999px;}
  .badge.info{background:#eaf0f7; color:#426693;}
  .tip{display:flex; gap:9px; align-items:flex-start; background:#eaf0f7; color:#3a5878; border-radius:11px; padding:11px 13px; font-size:12.5px; line-height:1.5; margin-bottom:6px;}
  .tip svg{flex:0 0 auto; margin-top:1px;}

  /* provider / directory list rows */
  .lhead{display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:12px;}
  .lhead h3{font-size:15px; font-weight:600; color:var(--ink);}
  .lhead p{font-size:12.5px; color:var(--muted); margin-top:3px; max-width:440px;}
  .empty{font-size:13px; color:var(--muted); border:1px dashed var(--border); border-radius:12px; padding:18px; text-align:center; margin-bottom:12px;}
  .rowcard{display:flex; align-items:center; gap:13px; border:1px solid var(--border); border-radius:13px; padding:13px 15px; margin-bottom:11px; background:#fff;}
  .ico{width:42px; height:42px; border-radius:10px; background:#f4f3f0; display:grid; place-items:center; flex:0 0 auto;}
  .meta{flex:1; min-width:0;}
  .nm{font-size:14px; font-weight:600; color:var(--ink); display:flex; align-items:center; gap:8px;}
  .sub{font-size:12px; color:var(--muted); margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .st{font-size:11px; font-weight:500; color:var(--muted);}
  .st.on{color:#2f8f5f;} .st.warn{color:#b07a16;}

  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .sel{height:40px; border:1px solid var(--border); border-radius:9px; padding:0 10px; background:#fff; font-size:13.5px; width:100%;}
  .fg{display:block; padding:7px 0;} .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus{border-color:var(--clay);}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}

  /* LDAP modal redesign */
  .sect-h{font-size:11px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); margin:16px 0 8px; padding-bottom:5px; border-bottom:1px solid var(--border);}
  .sect-h .hint{text-transform:none; letter-spacing:0; font-weight:400;}
  .grid-conn{display:grid; grid-template-columns:1.1fr 1.7fr .6fr 1.5fr; gap:14px; align-items:end;}
  .grid3{display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px;}
  .g2w{display:grid; grid-template-columns:1.6fr 1fr; gap:14px;}
  .seg{display:flex; width:100%; border:1px solid var(--border); border-radius:9px; overflow:hidden;}
  .segb{flex:1; height:40px; padding:0 6px; font-size:12.5px; background:#fff; border:0; border-right:1px solid var(--border); cursor:pointer; color:var(--ink);}
  .segb:last-child{border-right:0;}
  .segb.on{background:var(--clay); color:#fff; font-weight:600;}
  .advtog{margin:12px 0 4px; padding:0; background:none; border:0; font-size:12.5px; font-weight:600; color:var(--clay); cursor:pointer;}

  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff;}
  .btn.sm{height:34px; padding:0 13px; white-space:nowrap;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.ghost{border:none; background:transparent; color:var(--muted);}
  .btn.add{background:var(--clay); color:#fff; border-color:var(--clay); white-space:nowrap; font-weight:600;}
  .del{width:32px; height:32px; border-radius:8px; border:1px solid var(--border); background:#fff; color:var(--muted); cursor:pointer; font-size:13px; flex:0 0 auto;}
  .del:hover{background:#fbeae6; color:#b03a22; border-color:#e6bdb2;}

  /* popup modal */
  .scrim{position:fixed; inset:0; background:rgba(28,26,23,.45); z-index:80;}
  .modal{position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); z-index:81; width:min(var(--mw,460px),calc(100vw - 32px)); max-height:92vh; display:flex; flex-direction:column;
    background:#fff; border-radius:16px; box-shadow:0 24px 60px rgba(28,26,23,.32); animation:min .16s ease-out; overflow:hidden;}
  @keyframes min{from{opacity:0; transform:translate(-50%,-46%);}to{opacity:1; transform:translate(-50%,-50%);}}
  .m-head{display:flex; align-items:center; justify-content:space-between; padding:15px 20px; border-bottom:1px solid var(--border);}
  .m-ttl{font-size:15px; font-weight:600; color:var(--ink);}
  .m-x{width:30px; height:30px; border-radius:8px; border:none; background:transparent; color:var(--muted); cursor:pointer; font-size:14px;}
  .m-x:hover{background:#f0efed;}
  .m-body{padding:16px 20px; overflow-y:auto;}
  .m-foot{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:13px 20px; border-top:1px solid var(--border);}
  .m-rt{display:flex; align-items:center; gap:9px;}
  .testmsg{font-size:12px; color:#b07a16;} .testmsg.ok{color:#2f8f5f;}
  .picker{display:flex; gap:8px;}
  .sw{width:40px; height:40px; border-radius:9px; border:1px solid var(--border); background:#fff; display:grid; place-items:center; cursor:pointer;}
  .sw.on{border:2px solid var(--clay);}
  .redirect{background:#f4f3f0; border-radius:9px; padding:9px 11px; font-size:11.5px; color:var(--muted); margin-top:4px;}
  .redirect code{display:block; margin-top:4px; font-size:11px; color:var(--ink); word-break:break-all;}

  @media (max-width:640px){
    .mcards{grid-template-columns:1fr;}
    .g2, .g2w, .grid3, .grid-conn{grid-template-columns:1fr;}
    .lhead{flex-direction:column;}
    .rowcard{flex-wrap:wrap;}
    .meta{flex:1 0 60%;}
    /* mobile = bottom sheet */
    .modal{top:auto; bottom:0; left:0; transform:none; width:100%; max-width:100%; max-height:92vh; border-radius:18px 18px 0 0; animation:sheet .2s ease-out;}
    @keyframes sheet{from{transform:translateY(100%);}to{transform:translateY(0);}}
    .m-foot{flex-wrap:wrap;}
  }
</style>
