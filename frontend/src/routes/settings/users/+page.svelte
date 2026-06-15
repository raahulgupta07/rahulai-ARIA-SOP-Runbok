<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import Section from '$lib/settings/Section.svelte';

  let users = $state<any[]>([]);
  let err = $state('');
  let showAdd = $state(false);
  let nu = $state({ email: '', name: '', password: '', role: 'user' });
  let me = $state<any>(auth.cachedUser());
  let q = $state('');
  let filt = $state<'all' | 'active' | 'disabled' | 'pending'>('all');

  async function load() {
    try { users = (await api.adminUsers()).users || []; } catch (e: any) { err = e.message; }
  }
  $effect(() => { load(); if (!me) auth.me().then((u) => (me = u)); });

  async function patch(u: any, body: any) {
    try { const r = await api.adminPatchUser(u.id, body); Object.assign(u, r); users = [...users]; }
    catch (e: any) { alert(e.message); }
  }
  async function del(u: any) {
    if (!confirm(`Delete ${u.email}?`)) return;
    try { await api.adminDeleteUser(u.id); users = users.filter((x) => x.id !== u.id); }
    catch (e: any) { alert(e.message); }
  }
  async function add() {
    try { await api.adminCreateUser(nu); showAdd = false; nu = { email: '', name: '', password: '', role: 'user' }; load(); }
    catch (e: any) { alert(e.message); }
  }

  const srcColor: Record<string, string> = { local: '#4a5b8a', ldap: '#3a7a5a', oidc: '#8a5a3a' };
  const srcLabel: Record<string, string> = { local: 'local', ldap: 'ldap', oidc: 'sso' };
  function statusOf(u: any) {
    if (u.role === 'pending') return { t: 'awaiting approval', c: '#9a7a2a', dot: '#d3a13e' };
    if (!u.active) return { t: 'disabled', c: '#9a5a4c', dot: '#cf6a4c' };
    return { t: 'active', c: '#3f7a44', dot: '#5fa463' };
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
  let admins = $derived(members.filter((u) => u.role === 'admin').length);
  let locals = $derived(members.filter((u) => u.auth_source === 'local').length);
  let shown = $derived(members.filter((u) => {
    if (filt === 'active' && !(u.active && u.role !== 'pending')) return false;
    if (filt === 'disabled' && !(!u.active && u.role !== 'pending')) return false;
    if (filt === 'pending' && u.role !== 'pending') return false;
    const nd = q.trim().toLowerCase();
    if (nd && !`${u.name || ''} ${u.email}`.toLowerCase().includes(nd)) return false;
    return true;
  }));
</script>

<div class="px-7 py-6">
  {#if err}<div class="mb-4 text-sm" style="color:#cf6a4c">{err}</div>{/if}

  <Section title="Overview" desc="At-a-glance counts across all member accounts. Anonymous embed visitors are excluded.">
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {#each [['Members', total], ['Active', active], ['Admins', admins], ['Local', locals]] as [label, val]}
        <div class="kpi">
          <div class="serif text-[22px] leading-none tnum" style="color:var(--clay)">{val}</div>
          <div class="text-[10.5px] mt-1.5 uppercase tracking-wide" style="color:var(--muted)">{label}</div>
        </div>
      {/each}
    </div>
    {#if visitors > 0}
      <p class="text-[11.5px] mt-3" style="color:var(--muted)">+ {visitors} anonymous embed visitor{visitors === 1 ? '' : 's'} (not counted as members)</p>
    {/if}
  </Section>

  <Section title="Members" desc="Manage who can sign in, their role and status. Identity is keyed by email — same email across LDAP/SSO is one account.">
    {#snippet actions()}
      <button class="btn pri" onclick={() => (showAdd = !showAdd)}>+ Add user</button>
    {/snippet}

    <!-- toolbar -->
    <div class="flex items-center gap-2 mb-3 flex-wrap">
      <div class="seg">
        {#each [['all', `All · ${total}`], ['active', `Active · ${active}`], ['disabled', `Disabled · ${disabled}`], ['pending', `Pending · ${pending}`]] as [k, l]}
          <button onclick={() => (filt = k as any)} class="segb {filt === k ? 'on' : ''}">{l}</button>
        {/each}
      </div>
      <div class="relative ml-auto">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>
        <input bind:value={q} placeholder="Search users" class="h-9 w-52 rounded-[9px] border pl-8 pr-3 text-sm outline-none" style="border-color:var(--border); background:#fff" />
      </div>
    </div>

    {#if showAdd}
      <div class="addform mb-4">
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
          <input class="fld" placeholder="Email" bind:value={nu.email} />
          <input class="fld" placeholder="Name" bind:value={nu.name} />
          <input class="fld" type="password" placeholder="Password" bind:value={nu.password} />
          <select class="fld" bind:value={nu.role}><option value="user">user</option><option value="admin">admin</option></select>
        </div>
        <div class="flex gap-2"><button class="btn pri" onclick={add}>Create</button><button class="btn" onclick={() => (showAdd = false)}>Cancel</button></div>
      </div>
    {/if}

    <div class="tablewrap">
      <table>
        <thead><tr><th>User</th><th>Source</th><th>Role</th><th>Status</th><th>Last login</th><th></th></tr></thead>
        <tbody>
          {#each shown as u (u.id)}
            {@const st = statusOf(u)}
            {@const isMe = me && (u.id === me.id || u.email === me.email)}
            <tr class="urow" style={u.role === 'pending' ? 'background:#FBF3E2' : ''}>
              <td>
                <div class="flex items-center gap-2.5">
                  <div class="av">{(u.name || u.email)[0]?.toUpperCase()}</div>
                  <div><b>{u.name || '—'}{#if isMe}<span class="me">you</span>{/if}</b><br><small>{u.email}</small></div>
                </div>
              </td>
              <td><span class="badge" style="color:{srcColor[u.auth_source]}">● {srcLabel[u.auth_source]}</span></td>
              <td>
                <select class="rolesel" value={u.role} onchange={(e) => patch(u, { role: (e.target as HTMLSelectElement).value })}>
                  <option value="admin">admin</option><option value="user">user</option><option value="pending">pending</option>
                </select>
              </td>
              <td><span class="flex items-center gap-1.5" style="color:{st.c}"><span class="w-1.5 h-1.5 rounded-full" style="background:{st.dot}"></span>{st.t}</span></td>
              <td style="color:var(--muted)">{ago(u.last_login)}</td>
              <td>
                <div class="flex gap-1.5 justify-end">
                  {#if u.role === 'pending'}
                    <button class="btn sm pri" onclick={() => patch(u, { role: 'user' })}>Approve</button>
                  {:else if u.active}
                    <button class="btn sm dng" onclick={() => patch(u, { active: false })} disabled={isMe}>Disable</button>
                  {:else}
                    <button class="btn sm" onclick={() => patch(u, { active: true })}>Enable</button>
                  {/if}
                  <button class="btn sm dng" onclick={() => del(u)} disabled={isMe}>Delete</button>
                </div>
              </td>
            </tr>
          {/each}
          {#if shown.length === 0}
            <tr><td colspan="6" class="text-center py-6" style="color:var(--muted)">No users match.</td></tr>
          {/if}
        </tbody>
      </table>
    </div>
  </Section>
</div>

<style>
  .badge{display:inline-flex; align-items:center; gap:5px; font-size:11.5px; padding:3px 9px; border-radius:99px; border:1px solid var(--border); background:#fff;}
  .kpi{text-align:center; padding:14px 8px; border:1px solid var(--border); border-radius:11px; background:var(--cream);}
  .addform{background:var(--cream); border:1px solid var(--border); border-radius:11px; padding:16px;}
  .tablewrap{border:1px solid var(--border); border-radius:11px; padding:4px 16px; background:#fff;}
  .tnum{font-variant-numeric:tabular-nums;}
  .seg{display:inline-flex; padding:2px; gap:2px; border-radius:9px; background:#e7e3da;}
  .segb{font-size:11.5px; padding:5px 11px; border-radius:7px; border:none; background:none; color:#6f6c65; cursor:pointer;}
  .segb.on{background:#fff; color:var(--clay); font-weight:600; box-shadow: none;}
  table{width:100%; border-collapse:collapse;}
  th{text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); padding:10px; font-weight:600;}
  td{padding:11px 10px; border-top:1px solid var(--line); font-size:13px; vertical-align:middle;}
  td small{color:var(--muted);}
  .urow:hover{background:#faf6f1;}
  .me{display:inline-block; margin-left:6px; font-size:9.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; padding:1px 5px; border-radius:5px; background:#f0efed; color:var(--clay); vertical-align:middle;}
  .av{width:30px; height:30px; border-radius:50%; background:var(--clay); color:#fff; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:600;}
  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.sm{height:30px; padding:0 11px; font-size:12px;}
  .btn.dng{color:#cf6a4c; border-color:#e7cfc6; background:#fff;}
  .btn:disabled{opacity:.4; cursor:default;}
  .fld{height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none;}
  .rolesel{font-size:12px; border:1px solid var(--border); border-radius:7px; padding:3px 6px; background:#fff;}
</style>
