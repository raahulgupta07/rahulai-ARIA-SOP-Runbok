<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';

  // reactive admin gate (super-admin only) — cachedUser() is null at cold init
  let me = $state<any>(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');

  let err = $state('');
  let loading = $state(true);

  let sectors = $state<any[]>([]);
  let users = $state<any[]>([]);
  let groups = $state<any[]>([]);

  const ROLES = ['user', 'sector_admin', 'admin'];

  // normalize: backend may return a bare array OR {users:[...]} / {sectors:[...]} etc.
  function arr(x: any, key: string): any[] {
    if (Array.isArray(x)) return x;
    if (x && Array.isArray(x[key])) return x[key];
    return [];
  }

  async function load() {
    loading = true;
    err = '';
    try {
      const [s, u, g] = await Promise.all([
        api.adminSectors().catch(() => []),
        api.adminUsers().catch(() => []),
        api.adminGroups().catch(() => [])
      ]);
      sectors = arr(s, 'sectors');
      users = arr(u, 'users');
      groups = arr(g, 'groups');
    } catch (e: any) {
      err = e?.message || 'Failed to load';
    }
    loading = false;
  }

  $effect(() => {
    if (!me) auth.me().then((x) => (me = x)).catch(() => {});
  });
  $effect(() => {
    if (isAdmin) load();
  });

  // ---- sectors ----
  let newSectorName = $state('');
  let newSectorLabel = $state('');
  let addingSector = $state(false);
  async function addSector() {
    const name = newSectorName.trim();
    if (!name) return;
    addingSector = true;
    try {
      await api.adminCreateSector(name, newSectorLabel.trim() || undefined);
      newSectorName = '';
      newSectorLabel = '';
      await load();
    } catch (e: any) { alert(e?.message || 'Failed'); }
    addingSector = false;
  }
  async function delSector(s: any) {
    if (!confirm(`Delete sector "${s.label || s.name}"? Documents and users in it will be unassigned.`)) return;
    try { await api.adminDeleteSector(s.id); await load(); }
    catch (e: any) { alert(e?.message || 'Failed'); }
  }

  // ---- user roles + sector ----
  async function setRole(u: any, role: string) {
    const prev = u.role;
    u.role = role; users = [...users];
    try { const r = await api.adminSetUser(u.id, { role }); if (r) Object.assign(u, r); users = [...users]; }
    catch (e: any) { u.role = prev; users = [...users]; alert(e?.message || 'Failed'); }
  }
  async function setSector(u: any, val: string) {
    const prev = u.sector_id;
    const sector_id = val === '' ? null : Number(val);
    u.sector_id = sector_id; users = [...users];
    try { const r = await api.adminSetUser(u.id, { sector_id }); if (r) Object.assign(u, r); users = [...users]; }
    catch (e: any) { u.sector_id = prev; users = [...users]; alert(e?.message || 'Failed'); }
  }

  // ---- groups ----
  let newGroupName = $state('');
  let newGroupAll = $state(false);
  let addingGroup = $state(false);
  async function addGroup() {
    const name = newGroupName.trim();
    if (!name) return;
    addingGroup = true;
    try {
      await api.adminCreateGroup(name, newGroupAll);
      newGroupName = '';
      newGroupAll = false;
      await load();
    } catch (e: any) { alert(e?.message || 'Failed'); }
    addingGroup = false;
  }
  async function delGroup(g: any) {
    if (!confirm(`Delete group "${g.name}"?`)) return;
    try { await api.adminDeleteGroup(g.id); await load(); }
    catch (e: any) { alert(e?.message || 'Failed'); }
  }
  async function addMember(g: any, uid: string) {
    if (uid === '') return;
    try { await api.adminAddMember(g.id, Number(uid)); await load(); }
    catch (e: any) { alert(e?.message || 'Failed'); }
  }
  async function removeMember(g: any, uid: number) {
    try { await api.adminRemoveMember(g.id, uid); await load(); }
    catch (e: any) { alert(e?.message || 'Failed'); }
  }

  function memberIds(g: any): number[] {
    return (g.members || []).map((m: any) => m.id);
  }
  function nonMembers(g: any): any[] {
    const ids = new Set(memberIds(g));
    return users.filter((u) => !ids.has(u.id));
  }
  function sectorName(id: number | null): string {
    if (id == null) return '';
    const s = sectors.find((x) => x.id === id);
    return s ? (s.label || s.name) : '';
  }
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <p class="ttlsub">Multi-tenant access — define sectors (company / email domain), promote sector-admins, and manage groups (incl. an All-Access group) for folder access.</p>

    {#if !isAdmin}
      <div class="gate">Super-admin only.</div>
    {:else}
      {#if err}<div class="errbar">{err}</div>{/if}

      <!-- ============ SECTORS ============ -->
      <section class="card">
        <div class="chead">
          <div>
            <div class="ctitle">Sectors</div>
            <div class="csub">A sector = a company / email domain. Documents and users belong to one sector.</div>
          </div>
        </div>

        <div class="addrow">
          <input class="inp" bind:value={newSectorName} placeholder="name (e.g. acme.com)" />
          <input class="inp" bind:value={newSectorLabel} placeholder="label (optional, e.g. Acme Corp)" />
          <button class="btn pri" onclick={addSector} disabled={addingSector || !newSectorName.trim()}>Add sector</button>
        </div>

        <div class="tablewrap">
          <table>
            <thead><tr><th>Name</th><th>Label</th><th>Docs</th><th>Users</th><th></th></tr></thead>
            <tbody>
              {#each sectors as s (s.id)}
                <tr>
                  <td class="mono">{s.name}</td>
                  <td>{s.label || '—'}</td>
                  <td class="muted">{s.doc_count ?? 0}</td>
                  <td class="muted">{s.user_count ?? 0}</td>
                  <td class="tr"><button class="lnk dng" onclick={() => delSector(s)}>Delete</button></td>
                </tr>
              {/each}
              {#if !sectors.length}
                <tr><td colspan="5" class="empty-td">{loading ? 'Loading…' : 'No sectors yet.'}</td></tr>
              {/if}
            </tbody>
          </table>
        </div>
      </section>

      <!-- ============ USERS & ROLES ============ -->
      <section class="card">
        <div class="chead">
          <div>
            <div class="ctitle">Users &amp; roles</div>
            <div class="csub">Promote a sector-admin (uploads for their sector) or super-admin (manages everything). Assign each user a sector.</div>
          </div>
        </div>

        <div class="tablewrap">
          <table>
            <thead><tr><th>Email</th><th>Role</th><th>Sector</th></tr></thead>
            <tbody>
              {#each users as u (u.id)}
                <tr>
                  <td>
                    <div class="uem">{u.email}</div>
                    {#if u.name}<div class="uname">{u.name}</div>{/if}
                  </td>
                  <td>
                    <select class="sel" value={u.role} onchange={(e) => setRole(u, (e.target as HTMLSelectElement).value)}>
                      {#each ROLES as r}<option value={r}>{r}</option>{/each}
                    </select>
                  </td>
                  <td>
                    <select class="sel" value={u.sector_id ?? ''} onchange={(e) => setSector(u, (e.target as HTMLSelectElement).value)}>
                      <option value="">— none —</option>
                      {#each sectors as s}<option value={s.id}>{s.label || s.name}</option>{/each}
                    </select>
                  </td>
                </tr>
              {/each}
              {#if !users.length}
                <tr><td colspan="3" class="empty-td">{loading ? 'Loading…' : 'No users.'}</td></tr>
              {/if}
            </tbody>
          </table>
        </div>
      </section>

      <!-- ============ GROUPS ============ -->
      <section class="card">
        <div class="chead">
          <div>
            <div class="ctitle">Groups</div>
            <div class="csub">Group members can be granted folder access. An All-Access group reads every sector.</div>
          </div>
        </div>

        <div class="addrow">
          <input class="inp" bind:value={newGroupName} placeholder="group name" />
          <label class="chk"><input type="checkbox" bind:checked={newGroupAll} /> All-Access (reads every sector)</label>
          <button class="btn pri" onclick={addGroup} disabled={addingGroup || !newGroupName.trim()}>Create group</button>
        </div>

        <div class="glist">
          {#each groups as g (g.id)}
            <div class="gcard">
              <div class="grow1">
                <div class="gname">
                  {g.name}
                  {#if g.all_sectors}<span class="badge">All-Access</span>{/if}
                  <span class="gcount">{g.member_count ?? (g.members?.length ?? 0)} members</span>
                </div>
                <button class="lnk dng" onclick={() => delGroup(g)}>Delete group</button>
              </div>

              <div class="chips">
                {#each (g.members || []) as m (m.id)}
                  <span class="chip">{m.email}<button class="chipx" onclick={() => removeMember(g, m.id)} aria-label="Remove">✕</button></span>
                {/each}
                {#if !(g.members || []).length}<span class="chip-empty">No members yet.</span>{/if}
              </div>

              <div class="addmem">
                <select class="sel" onchange={(e) => { addMember(g, (e.target as HTMLSelectElement).value); (e.target as HTMLSelectElement).value=''; }}>
                  <option value="">+ Add member…</option>
                  {#each nonMembers(g) as u}<option value={u.id}>{u.email}{u.sector_id != null ? ` · ${sectorName(u.sector_id)}` : ''}</option>{/each}
                </select>
              </div>
            </div>
          {/each}
          {#if !groups.length}
            <div class="empty-td">{loading ? 'Loading…' : 'No groups yet.'}</div>
          {/if}
        </div>
      </section>
    {/if}
  </div>
</div>

<style>
  .wrap{max-width:1100px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px; margin-bottom:18px;}
  .gate{font-size:14px; color:var(--muted); background:#fff; border:1px solid var(--border); border-radius:13px; padding:24px; text-align:center;}
  .errbar{font-size:13px; color:#b03a22; background:#fbeae6; border:1px solid #e6bdb2; border-radius:10px; padding:9px 13px; margin-bottom:14px;}

  .card{border:1px solid var(--border); border-radius:13px; background:#fff; padding:18px 18px 6px; margin-bottom:16px;}
  .chead{display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:14px;}
  .ctitle{font-size:15px; font-weight:600; color:var(--ink);}
  .csub{font-size:12px; color:var(--muted); margin-top:3px;}

  .addrow{display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-bottom:14px;}
  .inp{height:38px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; min-width:180px;}
  .inp:focus{border-color:var(--clay);}
  .chk{display:inline-flex; align-items:center; gap:7px; font-size:13px; color:var(--ink); cursor:pointer;}

  .sel{font-size:12.5px; border:1px solid var(--border); border-radius:7px; padding:5px 8px; background:#fff; cursor:pointer; outline:none;}
  .sel:focus{border-color:var(--clay);}

  .tablewrap{border:1px solid var(--border); border-radius:11px; overflow:hidden; margin-bottom:12px;}
  table{width:100%; border-collapse:collapse;}
  th{text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); padding:11px 14px; font-weight:600; border-bottom:1px solid var(--border);}
  td{padding:11px 14px; border-top:1px solid var(--border); font-size:13px; vertical-align:middle;}
  tbody tr:first-child td{border-top:none;}
  td.muted{color:var(--muted);}
  td.mono{font-family:ui-monospace,monospace; font-size:12.5px;}
  td.tr{text-align:right;}
  tr:hover{background:#faf9f7;}
  .empty-td{text-align:center; padding:22px 14px; color:var(--muted); font-size:13px;}

  .uem{font-size:13px; color:var(--ink); font-weight:500;}
  .uname{font-size:11.5px; color:var(--muted); margin-top:1px;}

  .btn{display:inline-flex; align-items:center; justify-content:center; gap:7px; height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff; color:var(--ink); white-space:nowrap;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay); font-weight:600;}
  .btn:disabled{opacity:.45; cursor:default;}
  .lnk{border:none; background:none; cursor:pointer; font-size:12.5px; color:var(--muted); padding:0;}
  .lnk:hover{color:var(--ink);}
  .lnk.dng{color:#b03a22;}
  .lnk.dng:hover{color:#8c2a16;}

  /* groups */
  .glist{display:flex; flex-direction:column; gap:11px; margin-bottom:12px;}
  .gcard{border:1px solid var(--border); border-radius:11px; padding:13px 14px; background:#fafaf8;}
  .grow1{display:flex; align-items:center; justify-content:space-between; gap:10px;}
  .gname{display:flex; align-items:center; gap:9px; font-size:13.5px; font-weight:600; color:var(--ink);}
  .gcount{font-size:11px; font-weight:400; color:var(--muted);}
  .badge{font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; padding:2px 7px; border-radius:6px; background:#eaf0f7; color:#426693;}
  .chips{display:flex; flex-wrap:wrap; gap:7px; margin:11px 0;}
  .chip{display:inline-flex; align-items:center; gap:6px; font-size:12px; padding:4px 5px 4px 10px; border-radius:999px; background:#fff; border:1px solid var(--border); color:var(--ink);}
  .chipx{border:none; background:none; cursor:pointer; color:var(--muted); font-size:11px; line-height:1; padding:2px 4px; border-radius:50%;}
  .chipx:hover{background:#f0efed; color:#b03a22;}
  .chip-empty{font-size:12px; color:var(--muted);}
  .addmem{margin-top:4px;}

  @media (max-width:640px){
    .tablewrap{overflow-x:auto;}
    table{min-width:480px;}
    .inp{min-width:0; flex:1;}
  }
</style>
