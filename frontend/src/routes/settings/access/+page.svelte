<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';

  // reactive admin gate (super-admin only) — cachedUser() is null at cold init
  let me = $state<any>(auth.cachedUser());
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));

  let err = $state('');
  let loading = $state(true);

  let sectors = $state<any[]>([]);
  let users = $state<any[]>([]);
  let groups = $state<any[]>([]);
  let rbacOn = $state(false);
  let rbacBusy = $state(false);
  async function toggleRbac() {
    rbacBusy = true;
    try {
      const r: any = await api.adminSetRbac(!rbacOn);
      rbacOn = !!r?.enabled;
    } catch (e: any) { alert(e?.message || 'Failed to toggle multi-tenant access'); }
    rbacBusy = false;
  }

  const ROLES = ['user', 'admin', 'superadmin'];

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
      const [s, u, g, rb] = await Promise.all([
        api.adminSectors().catch(() => []),
        api.adminUsers().catch(() => []),
        api.adminGroups().catch(() => []),
        api.adminRbac().catch(() => ({ enabled: false }))
      ]);
      sectors = arr(s, 'sectors');
      users = arr(u, 'users');
      groups = arr(g, 'groups');
      rbacOn = !!(rb as any)?.enabled;
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
  // Feature catalog (which app tabs a group's members may use). `chat` is the floor.
  const FEATURES = ['chat', 'sources', 'workspace', 'eval', 'wiki'];
  const FEATURE_LABELS: Record<string, string> = {
    chat: 'Chat', sources: 'Sources', workspace: 'Workspace', eval: 'Evaluations', wiki: 'Wiki'
  };

  let groupSearch = $state('');
  let filteredGroups = $derived(
    groups.filter((g) => {
      const q = groupSearch.trim().toLowerCase();
      if (!q) return true;
      return (g.name || '').toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q);
    })
  );

  async function delGroup(g: any) {
    if (!confirm(`Delete group "${g.name}"? Members will lose the access this group granted.`)) return;
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

  // compact summary of a group's app-feature access
  function featSummary(g: any): string {
    const on = new Set<string>(g.features && g.features.length ? g.features : ['chat']);
    const list = FEATURES.filter((f) => on.has(f));
    if (list.length >= FEATURES.length) return 'all app features';
    if (list.length === 1 && list[0] === 'chat') return 'chat only';
    return list.map((f) => FEATURE_LABELS[f] || f).join(', ');
  }

  // ---- create / edit modal ----
  let modalOpen = $state(false);
  let modalTab = $state<'general' | 'permissions'>('general');
  let editingId = $state<number | null>(null); // null = create mode
  let saving = $state(false);
  let nameEl = $state<HTMLInputElement | undefined>();

  // local form state (seeded on open)
  let fName = $state('');
  let fDesc = $state('');
  let fAllSectors = $state(false);
  let fFeat = $state<Record<string, boolean>>({ chat: true, sources: false, workspace: false, eval: false, wiki: false });
  let fManage = $state(false);
  let fTeach = $state(false);

  function openCreate() {
    editingId = null;
    modalTab = 'general';
    fName = ''; fDesc = ''; fAllSectors = false;
    fFeat = { chat: true, sources: false, workspace: false, eval: false, wiki: false };
    fManage = false; fTeach = false;
    modalOpen = true;
  }
  function openEdit(g: any) {
    editingId = g.id;
    modalTab = 'general';
    fName = g.name || ''; fDesc = g.description || ''; fAllSectors = !!g.all_sectors;
    const set = new Set<string>(g.features || []);
    fFeat = { chat: true, sources: set.has('sources'), workspace: set.has('workspace'), eval: set.has('eval'), wiki: set.has('wiki') };
    fManage = !!g.manage_content; fTeach = !!g.teach_knowledge;
    modalOpen = true;
  }
  function closeModal() { modalOpen = false; }
  function resetDefaults() {
    fFeat = { chat: true, sources: false, workspace: false, eval: false, wiki: false };
    fManage = false; fTeach = false;
  }
  async function saveGroup() {
    const name = fName.trim();
    if (!name) { modalTab = 'general'; return; }
    // chat is always included even though its toggle is disabled (it's the floor)
    const features = FEATURES.filter((f) => f === 'chat' || fFeat[f]);
    const body = {
      name,
      description: fDesc.trim(),
      all_sectors: fAllSectors,
      features,
      manage_content: fManage,
      teach_knowledge: fTeach
    };
    saving = true;
    try {
      if (editingId == null) await api.adminCreateGroup(body);
      else await api.adminUpdateGroup(editingId, body);
      modalOpen = false;
      await load();
    } catch (e: any) { alert(e?.message || 'Failed'); }
    saving = false;
  }

  // focus the name field when the modal opens
  $effect(() => { if (modalOpen && nameEl) nameEl.focus(); });
  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && modalOpen) { e.preventDefault(); closeModal(); }
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

      <!-- ============ MULTI-TENANT SWITCH ============ -->
      <section class="card">
        <div class="chead">
          <div>
            <div class="ctitle">Multi-tenant access</div>
            <div class="csub">When ON, users only see documents and folders in their own sector or shared with them. When OFF (single-tenant) everyone sees everything and only admins manage knowledge. Takes effect immediately — no restart.</div>
          </div>
          <button
            onclick={toggleRbac} disabled={rbacBusy}
            role="switch" aria-checked={rbacOn} aria-label="Enable multi-tenant access"
            style="flex:none;width:46px;height:26px;border-radius:999px;border:none;cursor:{rbacBusy ? 'default' : 'pointer'};opacity:{rbacBusy ? 0.6 : 1};position:relative;transition:background .15s;background:{rbacOn ? 'var(--ink,#1a1a18)' : '#cfcdc6'};">
            <span style="position:absolute;top:3px;left:{rbacOn ? '23px' : '3px'};width:20px;height:20px;border-radius:50%;background:#fff;transition:left .15s;"></span>
          </button>
        </div>
        <div style="margin-top:8px;font-size:12.5px;font-weight:600;color:{rbacOn ? '#2f8f6a' : 'var(--muted)'};">
          {rbacOn ? '● Enforcing — sectors & folders are live' : '○ Off — single-tenant (everyone sees all)'}
        </div>
      </section>

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
            <div class="csub">Bundle a set of app tabs and content permissions, then add members. A group grants access on top of a user's chat-only default.</div>
          </div>
          <button class="btn pri" onclick={openCreate}>+ New group</button>
        </div>

        {#if groups.length}
          <div class="addrow">
            <input class="inp" bind:value={groupSearch} placeholder="Search groups…" />
          </div>
        {/if}

        <div class="glist">
          {#each filteredGroups as g (g.id)}
            <div class="gcard">
              <div class="grow1">
                <div class="ginfo">
                  <div class="gname">
                    {g.name}
                    {#if g.all_sectors}<span class="badge">All sectors</span>{/if}
                  </div>
                  {#if g.description}<div class="gdesc">{g.description}</div>{/if}
                  <div class="gmeta">
                    <span class="gfeat">{featSummary(g)}</span>
                    {#if g.manage_content}<span class="tag">Manage content</span>{/if}
                    {#if g.teach_knowledge}<span class="tag">Teach</span>{/if}
                    <span class="gcount">{g.member_count ?? (g.members?.length ?? 0)} members</span>
                  </div>
                </div>
                <div class="gacts">
                  <button class="lnk" onclick={() => openEdit(g)}>Edit</button>
                  <button class="lnk dng" onclick={() => delGroup(g)}>Delete</button>
                </div>
              </div>

              <div class="chips">
                {#each (g.members || []) as m (m.id)}
                  <span class="chip">{m.email}<button class="chipx" onclick={() => removeMember(g, m.id)} aria-label="Remove {m.email}">✕</button></span>
                {/each}
                {#if !(g.members || []).length}<span class="chip-empty">No members yet.</span>{/if}
              </div>

              <div class="addmem">
                <select class="sel" onchange={(e) => { addMember(g, (e.target as HTMLSelectElement).value); (e.target as HTMLSelectElement).value=''; }}>
                  <option value="">+ Add an existing user to this group…</option>
                  {#each nonMembers(g) as u}<option value={u.id}>{u.name || u.email} · {u.role}</option>{/each}
                </select>
                <div class="addmem-hint">Pick a user to grant them this group's access. Users aren't added automatically.</div>
              </div>
            </div>
          {/each}
          {#if !groups.length}
            <div class="empty-td">{loading ? 'Loading…' : 'No groups yet. Create one to grant members extra tabs.'}</div>
          {:else if !filteredGroups.length}
            <div class="empty-td">No groups match “{groupSearch}”.</div>
          {/if}
        </div>
      </section>
    {/if}
  </div>
</div>

<svelte:window onkeydown={onKeydown} />

<!-- pill switch (green when on) -->
{#snippet pill(on: boolean, disabled: boolean, cb: () => void, label: string)}
  <button
    type="button"
    class="tgl"
    class:on={on}
    disabled={disabled}
    role="switch"
    aria-checked={on}
    aria-label={label}
    onclick={cb}
  >
    <span class="knob"></span>
  </button>
{/snippet}

<!-- ============ CREATE / EDIT GROUP MODAL ============ -->
{#if modalOpen}
  <div class="scrim" role="presentation" onclick={closeModal}></div>
  <div class="modal" role="dialog" aria-modal="true" aria-label={editingId == null ? 'New group' : 'Edit group'}>
    <div class="mhead">
      <div class="mttl">{editingId == null ? 'New group' : 'Edit group'}</div>
      <button class="mx" onclick={closeModal} aria-label="Close">✕</button>
    </div>

    <div class="mbody">
      <!-- left sub-nav -->
      <nav class="mnav">
        <button class="mnav-i" class:on={modalTab === 'general'} onclick={() => (modalTab = 'general')}>General</button>
        <button class="mnav-i" class:on={modalTab === 'permissions'} onclick={() => (modalTab = 'permissions')}>Permissions</button>
      </nav>

      <!-- panes -->
      <div class="mpane">
        {#if modalTab === 'general'}
          <div class="fld">
            <label class="flab" for="grp-name">Name</label>
            <input id="grp-name" class="inp full" bind:this={nameEl} bind:value={fName} placeholder="e.g. Analysts" />
          </div>
          <div class="fld">
            <label class="flab" for="grp-desc">Description</label>
            <textarea id="grp-desc" class="ta" bind:value={fDesc} rows="3" placeholder="What this group is for (optional)"></textarea>
          </div>
          <div class="fld">
            <span class="flab">Sector access</span>
            <label class="radio"><input type="radio" name="sect" checked={!fAllSectors} onchange={() => (fAllSectors = false)} /> <span>Own sector only</span></label>
            <label class="radio"><input type="radio" name="sect" checked={fAllSectors} onchange={() => (fAllSectors = true)} /> <span>All sectors <em>(reads every sector)</em></span></label>
          </div>
        {:else}
          <div class="psec">
            <div class="psec-h">App access (tabs)</div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Chat</span><span class="prow-s">Always on — the baseline every member gets</span></div>
              {@render pill(true, true, () => {}, 'Chat (always on)')}
            </div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Sources</span><span class="prow-s">Browse and search the document library</span></div>
              {@render pill(fFeat.sources, false, () => (fFeat.sources = !fFeat.sources), 'Sources')}
            </div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Workspace</span><span class="prow-s">Dashboards, analytics and workspace tools</span></div>
              {@render pill(fFeat.workspace, false, () => (fFeat.workspace = !fFeat.workspace), 'Workspace')}
            </div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Evaluations</span><span class="prow-s">Answer-quality evaluation reports</span></div>
              {@render pill(fFeat.eval, false, () => (fFeat.eval = !fFeat.eval), 'Evaluations')}
            </div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Wiki</span><span class="prow-s">Browsable knowledge wiki</span></div>
              {@render pill(fFeat.wiki, false, () => (fFeat.wiki = !fFeat.wiki), 'Wiki')}
            </div>
          </div>

          <div class="psec">
            <div class="psec-h">Content actions</div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Manage documents</span><span class="prow-s">Upload, move, re-tag and delete documents</span></div>
              {@render pill(fManage, false, () => (fManage = !fManage), 'Manage documents')}
            </div>
            <div class="prow">
              <div class="prow-t"><span class="prow-n">Teach &amp; approve knowledge</span><span class="prow-s">Teach facts and approve pending knowledge</span></div>
              {@render pill(fTeach, false, () => (fTeach = !fTeach), 'Teach and approve knowledge')}
            </div>
          </div>

          <div class="mnote">Settings (auth, storage, RBAC, users) stays super-admin only — never a group option.</div>
          <button class="reset" onclick={resetDefaults}>Reset to defaults</button>
        {/if}
      </div>
    </div>

    <div class="mfoot">
      <button class="btn" onclick={closeModal}>Cancel</button>
      <button class="btn pri" onclick={saveGroup} disabled={saving || !fName.trim()}>{saving ? 'Saving…' : 'Save'}</button>
    </div>
  </div>
{/if}

<style>
  .wrap{max-width:1100px;}
  .addmem-hint{font-size:11px; color:var(--muted); margin-top:5px;}
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
  .grow1{display:flex; align-items:flex-start; justify-content:space-between; gap:10px;}
  .ginfo{min-width:0;}
  .gname{display:flex; align-items:center; gap:9px; font-size:13.5px; font-weight:600; color:var(--ink);}
  .gdesc{font-size:12px; color:var(--muted); margin-top:3px;}
  .gmeta{display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:6px;}
  .gfeat{font-size:11.5px; color:var(--muted);}
  .gcount{font-size:11px; font-weight:400; color:var(--muted);}
  .gacts{display:flex; align-items:center; gap:12px; flex:none;}
  .badge{font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; padding:2px 7px; border-radius:6px; background:#eaf0f7; color:#426693;}
  .tag{font-size:10.5px; font-weight:600; padding:2px 8px; border-radius:999px; background:#e8f4ee; color:#2f8f6a; border:1px solid #cfe9dd;}
  .chips{display:flex; flex-wrap:wrap; gap:7px; margin:11px 0;}
  .chip{display:inline-flex; align-items:center; gap:6px; font-size:12px; padding:4px 5px 4px 10px; border-radius:999px; background:#fff; border:1px solid var(--border); color:var(--ink);}
  .chipx{border:none; background:none; cursor:pointer; color:var(--muted); font-size:11px; line-height:1; padding:2px 4px; border-radius:50%;}
  .chipx:hover{background:#f0efed; color:#b03a22;}
  .chip-empty{font-size:12px; color:var(--muted);}
  .addmem{margin-top:4px;}

  /* pill toggle */
  .tgl{flex:none; width:40px; height:23px; border-radius:999px; border:none; padding:0; position:relative; cursor:pointer; background:#cfcdc6; transition:background .15s;}
  .tgl.on{background:#2f8f6a;}
  .tgl:disabled{cursor:default; opacity:.7;}
  .tgl .knob{position:absolute; top:3px; left:3px; width:17px; height:17px; border-radius:50%; background:#fff; transition:left .15s;}
  .tgl.on .knob{left:20px;}
  .tgl:focus-visible{outline:2px solid var(--clay); outline-offset:2px;}

  /* modal */
  .scrim{position:fixed; inset:0; background:rgba(20,19,17,.34); z-index:60;}
  .modal{position:fixed; z-index:61; top:50%; left:50%; transform:translate(-50%,-50%); width:min(680px,92vw); max-height:88vh; display:flex; flex-direction:column; background:#fff; border:1px solid var(--border); border-radius:15px; overflow:hidden;}
  .mhead{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:15px 18px; border-bottom:1px solid var(--border);}
  .mttl{font-size:15.5px; font-weight:600; color:var(--ink);}
  .mx{border:none; background:none; cursor:pointer; font-size:14px; color:var(--muted); padding:4px 6px; border-radius:7px;}
  .mx:hover{background:#f0efed; color:var(--ink);}
  .mbody{display:flex; min-height:0; flex:1; overflow:hidden;}
  .mnav{flex:none; width:150px; border-right:1px solid var(--border); padding:12px 10px; display:flex; flex-direction:column; gap:4px; background:#fafaf8;}
  .mnav-i{text-align:left; border:none; background:none; cursor:pointer; font-size:13px; color:var(--muted); padding:8px 11px; border-radius:8px; font-weight:500;}
  .mnav-i:hover{background:#efefec; color:var(--ink);}
  .mnav-i.on{background:#eaf0f7; color:#426693; font-weight:600;}
  .mpane{flex:1; min-width:0; overflow-y:auto; padding:18px 20px;}

  .fld{margin-bottom:16px;}
  .flab{display:block; font-size:12px; font-weight:600; color:var(--ink); margin-bottom:6px;}
  .inp.full{width:100%; min-width:0;}
  .ta{width:100%; border:1px solid var(--border); border-radius:9px; padding:9px 12px; font-size:13.5px; background:#fff; outline:none; resize:vertical; font-family:inherit; color:var(--ink);}
  .ta:focus{border-color:var(--clay);}
  .radio{display:flex; align-items:center; gap:8px; font-size:13px; color:var(--ink); cursor:pointer; padding:5px 0;}
  .radio em{color:var(--muted); font-style:normal;}

  .psec{margin-bottom:20px;}
  .psec-h{font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-weight:700; margin-bottom:9px;}
  .prow{display:flex; align-items:center; justify-content:space-between; gap:14px; padding:9px 0; border-top:1px solid var(--border);}
  .prow:first-of-type{border-top:none;}
  .prow-t{min-width:0; display:flex; flex-direction:column; gap:2px;}
  .prow-n{font-size:13px; font-weight:500; color:var(--ink);}
  .prow-s{font-size:11.5px; color:var(--muted);}
  .mnote{font-size:11.5px; color:var(--muted); background:#faf9f7; border:1px solid var(--border); border-radius:9px; padding:9px 12px; margin-bottom:12px;}
  .reset{border:none; background:none; cursor:pointer; font-size:12.5px; color:var(--clay); font-weight:600; padding:0;}
  .reset:hover{text-decoration:underline;}

  .mfoot{flex:none; display:flex; align-items:center; justify-content:flex-end; gap:9px; padding:13px 18px; border-top:1px solid var(--border); background:#fafaf8;}

  @media (max-width:640px){
    .tablewrap{overflow-x:auto;}
    table{min-width:480px;}
    .inp{min-width:0; flex:1;}
    .mbody{flex-direction:column;}
    .mnav{width:auto; flex-direction:row; border-right:none; border-bottom:1px solid var(--border);}
  }
</style>
