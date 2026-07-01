<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';

  let users = $state<any[]>([]);
  let err = $state('');
  let edit = $state<any>(null);          // popup: { index:-1=new, draft:{...} }
  let me = $state<any>(auth.cachedUser());
  let q = $state('');
  let filt = $state<'all' | 'active' | 'disabled' | 'pending'>('all');
  let menuFor = $state<number | null>(null);   // ⋯ actions menu open for this user id

  async function load() {
    try { users = (await api.adminUsers()).users || []; } catch (e: any) { err = e.message; }
  }
  $effect(() => { load(); if (!me) auth.me().then((u) => (me = u)); });

  async function patch(u: any, body: any) {
    try { const r = await api.adminPatchUser(u.id, body); Object.assign(u, r); users = [...users]; }
    catch (e: any) { alert(e.message); }
    menuFor = null;
  }
  async function del(u: any) {
    if (!confirm(`Delete ${u.email}?`)) return;
    try { await api.adminDeleteUser(u.id); users = users.filter((x) => x.id !== u.id); }
    catch (e: any) { alert(e.message); }
    menuFor = null;
  }

  // popup add user
  function newUser() { edit = { index: -1, draft: { email: '', name: '', password: '', role: 'user' } }; }
  function closeEdit() { edit = null; }
  async function add() {
    try { await api.adminCreateUser(edit.draft); closeEdit(); load(); }
    catch (e: any) { alert(e.message); }
  }

  const srcLabel: Record<string, string> = { local: 'local', ldap: 'ldap', oidc: 'sso' };
  function statusOf(u: any) {
    if (u.role === 'pending') return { t: 'Awaiting', c: '#c98a2e', dot: '#d3a13e', pending: true };
    if (!u.active) return { t: 'Disabled', c: '#9a5a4c', dot: '#cf6a4c', pending: false };
    return { t: 'Active', c: '#2f8f5f', dot: '#2f8f5f', pending: false };
  }
  function ago(ts: string | null) {
    if (!ts) return '—';
    const s = (Date.now() - new Date(ts).getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  }

  // real people only — anonymous embed/widget visitors are NOT members
  let members = $derived(users.filter((u) => u.auth_source !== 'embed' && u.role !== 'widget'));
  let visitors = $derived(users.length - members.length);
  let total = $derived(members.length);
  let pending = $derived(members.filter((u) => u.role === 'pending').length);
  let active = $derived(members.filter((u) => u.active && u.role !== 'pending').length);
  let disabled = $derived(members.filter((u) => !u.active && u.role !== 'pending').length);
  let admins = $derived(members.filter((u) => (u.role === 'admin' || u.role === 'superadmin')).length);
  let shown = $derived(members.filter((u) => {
    if (filt === 'active' && !(u.active && u.role !== 'pending')) return false;
    if (filt === 'disabled' && !(!u.active && u.role !== 'pending')) return false;
    if (filt === 'pending' && u.role !== 'pending') return false;
    const nd = q.trim().toLowerCase();
    if (nd && !`${u.name || ''} ${u.email}`.toLowerCase().includes(nd)) return false;
    return true;
  }));
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Manage who can sign in, their role and status — keyed by email across local, LDAP &amp; SSO.</p>
      <button class="btn add" onclick={newUser}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>
        Add user
      </button>
    </div>

    {#if err}<div class="errbar">{err}</div>{/if}

    <!-- KPI strip -->
    <div class="kpis">
      <div class="kpi">
        <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#3a3833" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg></div>
        <div class="k-val">{total}</div>
        <div class="k-lbl">Total</div>
      </div>
      <div class="kpi">
        <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#2f8f5f" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/></svg></div>
        <div class="k-val" style="color:#2f8f5f">{active}</div>
        <div class="k-lbl">Active</div>
      </div>
      <div class="kpi">
        <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#c98a2e" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></div>
        <div class="k-val" style="color:#c98a2e">{pending}</div>
        <div class="k-lbl">Pending</div>
      </div>
      <div class="kpi">
        <div class="k-ic"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#426693" stroke-width="2"><path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6z"/><path d="m9 12 2 2 4-4"/></svg></div>
        <div class="k-val" style="color:#426693">{admins}</div>
        <div class="k-lbl">Admins</div>
      </div>
    </div>

    {#if visitors > 0}
      <p class="vis-note">+ {visitors} anonymous embed visitor{visitors === 1 ? '' : 's'} (not counted as members)</p>
    {/if}

    <!-- toolbar -->
    <div class="toolbar">
      <div class="search">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
        <input bind:value={q} placeholder="Search users" />
      </div>
      <div class="seg">
        {#each [['all', `All · ${total}`], ['active', `Active · ${active}`], ['disabled', `Disabled · ${disabled}`], ['pending', `Pending · ${pending}`]] as [k, l]}
          <button onclick={() => (filt = k as any)} class="segb {filt === k ? 'on' : ''}">{l}</button>
        {/each}
      </div>
    </div>

    <!-- table -->
    <div class="tablewrap">
      <table>
        <thead><tr><th>User</th><th class="c-role">Role</th><th class="c-status">Status</th><th class="c-last">Last login</th><th class="c-act"></th></tr></thead>
        <tbody>
          {#each shown as u (u.id)}
            {@const st = statusOf(u)}
            {@const isMe = me && (u.id === me.id || u.email === me.email)}
            <tr class="urow" class:pendrow={u.role === 'pending'}>
              <td>
                <div class="ucell">
                  <div class="av">{(u.name || u.email)[0]?.toUpperCase()}</div>
                  <div class="uinfo">
                    <div class="unm">{u.name || '—'}{#if isMe}<span class="me">you</span>{/if}</div>
                    <div class="uem">{u.email}<span class="srctag">{srcLabel[u.auth_source] || u.auth_source}</span></div>
                  </div>
                </div>
              </td>
              <td>
                <select class="rolesel" value={u.role} onchange={(e) => patch(u, { role: (e.target as HTMLSelectElement).value })} disabled={isMe} title={isMe ? "You can't change your own role" : ''}>
                  <option value="user">user</option><option value="sector_admin">sector admin</option><option value="admin">admin</option><option value="superadmin">super admin</option><option value="pending">pending</option>
                </select>
              </td>
              <td>
                <span class="pill" class:on={st.t === 'Active'} class:pend={st.pending} class:off={st.t === 'Disabled'}>
                  <span class="dot" style="background:{st.dot}"></span>{st.t}
                </span>
              </td>
              <td class="muted">{ago(u.last_login)}</td>
              <td>
                <div class="actcell">
                  {#if u.role === 'pending'}
                    <button class="btn sm pri" onclick={() => patch(u, { role: 'user' })}>Approve</button>
                  {/if}
                  <button class="kebab" onclick={() => (menuFor = menuFor === u.id ? null : u.id)} aria-label="Actions">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
                  </button>
                  {#if menuFor === u.id}
                    <div class="menu">
                      {#if u.role === 'pending'}
                        <button onclick={() => patch(u, { role: 'user' })}>Approve as user</button>
                      {:else if u.active}
                        <button onclick={() => patch(u, { active: false })} disabled={isMe}>Disable</button>
                      {:else}
                        <button onclick={() => patch(u, { active: true })}>Enable</button>
                      {/if}
                      <button class="dng" onclick={() => del(u)} disabled={isMe}>Delete</button>
                    </div>
                  {/if}
                </div>
              </td>
            </tr>
          {/each}
          {#if shown.length === 0}
            <tr><td colspan="5" class="empty-td">No users match.</td></tr>
          {/if}
        </tbody>
      </table>
    </div>
  </div>

  <!-- click-away closer for the kebab menu -->
  {#if menuFor !== null}
    <div class="menu-scrim" onclick={() => (menuFor = null)} role="presentation"></div>
  {/if}

  <!-- ============ ADD USER MODAL ============ -->
  {#if edit}
    <div class="scrim" onclick={closeEdit} role="presentation"></div>
    <div class="modal" role="dialog" aria-modal="true">
      <div class="m-head">
        <div class="m-ttl">Add user</div>
        <button class="m-x" onclick={closeEdit} aria-label="Close">✕</button>
      </div>
      <div class="m-body">
        <label class="fg"><span>Email</span><input bind:value={edit.draft.email} placeholder="person@company.com" /></label>
        <label class="fg"><span>Name</span><input bind:value={edit.draft.name} placeholder="Full name" /></label>
        <label class="fg"><span>Password</span><input type="password" bind:value={edit.draft.password} /></label>
        <label class="fg"><span>Role</span>
          <select class="sel" bind:value={edit.draft.role}><option value="user">user</option><option value="sector_admin">sector admin</option><option value="admin">admin</option><option value="superadmin">super admin</option></select>
        </label>
      </div>
      <div class="m-foot">
        <span></span>
        <div class="m-rt">
          <button class="btn ghost" onclick={closeEdit}>Cancel</button>
          <button class="btn pri" onclick={add}>Create user</button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .errbar{font-size:13px; color:#b03a22; background:#fbeae6; border:1px solid #e6bdb2; border-radius:10px; padding:9px 13px; margin-bottom:14px;}

  /* KPI strip */
  .kpis{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:6px;}
  .kpi{border:1px solid var(--border); border-radius:13px; padding:14px 15px; background:#fff;}
  .k-ic{width:32px; height:32px; border-radius:9px; background:#f4f3f0; display:grid; place-items:center; margin-bottom:9px;}
  .k-val{font-family:var(--font); font-size:24px; line-height:1; color:var(--ink); font-variant-numeric:tabular-nums;}
  .k-lbl{font-size:10.5px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-top:7px;}
  .vis-note{font-size:11.5px; color:var(--muted); margin-top:9px;}

  /* toolbar */
  .toolbar{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:18px 0 14px;}
  .search{position:relative; display:inline-flex; align-items:center;}
  .search svg{position:absolute; left:10px; pointer-events:none;}
  .search input{height:38px; width:240px; border:1px solid var(--border); border-radius:9px; padding:0 12px 0 32px; font-size:13.5px; background:#fff; outline:none;}
  .search input:focus{border-color:var(--clay);}
  .seg{display:inline-flex; padding:2px; gap:2px; border-radius:9px; background:#e7e3da; margin-left:auto;}
  .segb{font-size:11.5px; padding:6px 12px; border-radius:7px; border:none; background:none; color:#6f6c65; cursor:pointer;}
  .segb.on{background:#fff; color:var(--clay); font-weight:600;}

  /* table */
  .tablewrap{border:1px solid var(--border); border-radius:13px; background:#fff; overflow:hidden;}
  table{width:100%; border-collapse:collapse; table-layout:fixed;}
  .c-role{width:184px;} .c-status{width:120px;} .c-last{width:130px;} .c-act{width:60px;}
  td{overflow:hidden; text-overflow:ellipsis;}
  th{text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); padding:12px 16px; font-weight:600; border-bottom:1px solid var(--border);}
  td{padding:12px 16px; border-top:1px solid var(--border); font-size:13px; vertical-align:middle;}
  td.muted{color:var(--muted);}
  tbody tr:first-child td{border-top:none;}
  .urow:hover{background:#faf9f7;}
  .pendrow{background:#fbf3e2;}
  .pendrow:hover{background:#f8eeda;}
  .empty-td{text-align:center; padding:26px 16px; color:var(--muted);}

  .ucell{display:flex; align-items:center; gap:11px;}
  .av{width:32px; height:32px; border-radius:50%; background:var(--clay); color:#fff; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:600; flex:0 0 auto;}
  .uinfo{min-width:0;}
  .unm{font-size:13.5px; font-weight:600; color:var(--ink); display:flex; align-items:center;}
  .uem{font-size:12px; color:var(--muted); margin-top:1px; display:flex; align-items:center; gap:7px;}
  .srctag{font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.03em; padding:1px 6px; border-radius:5px; background:#f0efed; color:#6f6c65;}
  .me{display:inline-block; margin-left:6px; font-size:9.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; padding:1px 5px; border-radius:5px; background:#eaf0f7; color:#426693;}

  .rolesel{font-size:12px; border:1px solid var(--border); border-radius:7px; padding:4px 7px; background:#fff; cursor:pointer;}
  .rolesel:disabled{opacity:.55; cursor:default;}

  .pill{display:inline-flex; align-items:center; gap:6px; font-size:11.5px; font-weight:600; padding:3px 10px; border-radius:999px; border:1px solid var(--border); background:#f0efed; color:var(--muted);}
  .pill .dot{width:7px; height:7px; border-radius:50%; flex:0 0 auto;}
  .pill.on{background:#fff; color:#2f8f5f; border-color:#cfe6da;}
  .pill.pend{background:#fbf0dc; color:#c98a2e; border-color:#eed9ad;}
  .pill.off{background:#fff; color:#9a5a4c; border-color:#e7cfc6;}

  .actcell{display:flex; align-items:center; gap:8px; justify-content:flex-end; position:relative;}
  .kebab{width:30px; height:30px; border-radius:8px; border:1px solid transparent; background:transparent; color:var(--muted); cursor:pointer; display:grid; place-items:center;}
  .kebab:hover{background:#f0efed; color:var(--ink);}
  .menu{position:absolute; top:34px; right:0; z-index:40; min-width:150px; background:#fff; border:1px solid var(--border); border-radius:11px; box-shadow:0 12px 30px rgba(28,26,23,.16); padding:5px; overflow:hidden;}
  .menu button{display:block; width:100%; text-align:left; font-size:13px; padding:8px 11px; border:none; background:transparent; color:var(--ink); border-radius:7px; cursor:pointer;}
  .menu button:hover{background:#f0efed;}
  .menu button.dng{color:#b03a22;}
  .menu button.dng:hover{background:#fbeae6;}
  .menu button:disabled{opacity:.4; cursor:default;}
  .menu button:disabled:hover{background:transparent;}
  .menu-scrim{position:fixed; inset:0; z-index:39;}

  /* buttons */
  .btn{display:inline-flex; align-items:center; justify-content:center; gap:7px; height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink);}
  .btn.sm{height:30px; padding:0 12px; font-size:12px;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.add{background:var(--clay); color:#fff; border-color:var(--clay); white-space:nowrap; font-weight:600;}
  .btn.ghost{border:none; background:transparent; color:var(--muted);}
  .btn:disabled{opacity:.4; cursor:default;}

  /* modal — copied from auth page */
  .scrim{position:fixed; inset:0; background:rgba(28,26,23,.45); z-index:80;}
  .modal{position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); z-index:81; width:min(440px,calc(100vw - 32px)); max-height:88vh; display:flex; flex-direction:column;
    background:#fff; border-radius:16px; box-shadow:0 24px 60px rgba(28,26,23,.32); animation:min .16s ease-out; overflow:hidden;}
  @keyframes min{from{opacity:0; transform:translate(-50%,-46%);}to{opacity:1; transform:translate(-50%,-50%);}}
  .m-head{display:flex; align-items:center; justify-content:space-between; padding:15px 20px; border-bottom:1px solid var(--border);}
  .m-ttl{font-size:15px; font-weight:600; color:var(--ink);}
  .m-x{width:30px; height:30px; border-radius:8px; border:none; background:transparent; color:var(--muted); cursor:pointer; font-size:14px;}
  .m-x:hover{background:#f0efed;}
  .m-body{padding:16px 20px; overflow-y:auto;}
  .m-foot{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:13px 20px; border-top:1px solid var(--border);}
  .m-rt{display:flex; align-items:center; gap:9px;}
  .fg{display:block; padding:7px 0;} .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input, .sel{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus, .sel:focus{border-color:var(--clay);}

  @media (max-width:640px){
    .kpis{grid-template-columns:repeat(2,minmax(0,1fr));}
    .toolbar .seg{margin-left:0;}
    .search input{width:100%;}
    .search{flex:1;}
    .tablewrap{overflow-x:auto;}
    table{min-width:560px;}
    /* mobile = bottom sheet */
    .modal{top:auto; bottom:0; left:0; transform:none; width:100%; max-width:100%; max-height:92vh; border-radius:18px 18px 0 0; animation:sheet .2s ease-out;}
    @keyframes sheet{from{transform:translateY(100%);}to{transform:translateY(0);}}
    .m-foot{flex-wrap:wrap;}
  }
</style>
