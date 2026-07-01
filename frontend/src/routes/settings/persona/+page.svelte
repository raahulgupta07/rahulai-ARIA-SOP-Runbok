<script lang="ts">
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { onMount } from 'svelte';

  // reactive me / admin gate
  let me = $state<any>(auth.cachedUser());
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  $effect(() => { auth.me().then((u) => (me = u)).catch(() => {}); });

  type Persona = {
    enabled: boolean;
    name: string;
    role: string;
    covers: string[];
    tone: 'formal' | 'warm' | 'casual';
    length: 'brief' | 'full' | 'exhaustive';
    audience: 'end-user' | 'it' | 'mixed';
    rules: string[];
    greeting: string;
    version: number;
    generated_from: string;
  };

  const BLANK: Persona = {
    enabled: false, name: 'Aria', role: 'IT runbook assistant', covers: [],
    tone: 'warm', length: 'full', audience: 'mixed', rules: [], greeting: '',
    version: 0, generated_from: '—',
  };

  let loading = $state(true);
  let saving = $state(false);
  let generating = $state(false);
  let toast = $state('');
  let history = $state<any[]>([]);

  // editable local copy
  let p = $state<Persona>({ ...BLANK });
  // chip / rule input drafts
  let coverDraft = $state('');

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2600); }

  function normalize(raw: any): Persona {
    return {
      ...BLANK,
      ...(raw || {}),
      covers: Array.isArray(raw?.covers) ? [...raw.covers] : [],
      rules: Array.isArray(raw?.rules) ? [...raw.rules] : [],
    };
  }

  async function load() {
    loading = true;
    try {
      const r = await api.getPersona();
      p = normalize(r.persona);
    } catch { /* fail-soft → blank defaults */ }
    try {
      const h = await api.personaHistory();
      history = h.history || [];
    } catch { /* fail-soft */ }
    loading = false;
  }
  onMount(load);

  async function revert() {
    coverDraft = '';
    await load();
    flash('Reverted to saved persona');
  }

  async function save() {
    saving = true;
    try {
      const r = await api.savePersona({ ...p, covers: [...p.covers], rules: [...p.rules] });
      p = normalize(r.persona);
      flash(`Persona saved · v${p.version}`);
      try { history = (await api.personaHistory()).history || []; } catch {}
    } catch { flash('Could not save — try again'); }
    saving = false;
  }

  async function generate() {
    generating = true;
    try {
      const r = await api.generatePersona();
      // fill the form with the draft — do NOT auto-save
      p = normalize(r.persona);
      flash('Draft ready — review, then Save');
    } catch { flash('Could not scan knowledge — try again'); }
    generating = false;
  }

  // covers chips
  function addCover() {
    const v = coverDraft.trim();
    if (!v) return;
    if (!p.covers.includes(v)) p.covers = [...p.covers, v];
    coverDraft = '';
  }
  function removeCover(i: number) { p.covers = p.covers.filter((_, idx) => idx !== i); }

  // behaviour rules
  function addRule() { p.rules = [...p.rules, '']; }
  function setRule(i: number, v: string) { p.rules = p.rules.map((r, idx) => (idx === i ? v : r)); }
  function removeRule(i: number) { p.rules = p.rules.filter((_, idx) => idx !== i); }

  const TONES = [
    { v: 'formal', label: 'Formal' },
    { v: 'warm', label: 'Warm' },
    { v: 'casual', label: 'Casual' },
  ] as const;
  const LENGTHS = [
    { v: 'brief', label: 'Brief' },
    { v: 'full', label: 'Full' },
    { v: 'exhaustive', label: 'Exhaustive' },
  ] as const;
  const AUDIENCES = [
    { v: 'end-user', label: 'End user' },
    { v: 'it', label: 'IT staff' },
    { v: 'mixed', label: 'Mixed' },
  ] as const;

  // ── live preview (pure client-side string composition) ──
  let previewAnswer = $derived(buildAnswer(p));
  let previewRefusal = $derived(buildRefusal(p));

  function buildAnswer(x: Persona): string {
    const tone = x.tone;
    const len = x.length;
    if (len === 'brief') {
      if (tone === 'casual') return 'Settings → Users → Add user. Done.';
      if (tone === 'formal') return 'Navigate to Settings → Users and select Add user.';
      return 'Open Settings → Users, then Add user.';
    }
    if (len === 'exhaustive') {
      if (tone === 'formal')
        return 'To add a user: (1) Navigate to Settings. (2) Open the Users section. (3) Select Add user. (4) Enter the email, name and role. (5) Confirm to save. The account is created immediately and the user is notified.';
      if (tone === 'casual')
        return "Sure! Head to Settings → Users → Add user, pop in their email, name and role, then hit save — they're in right away.";
      return 'Happy to help — go to Settings → Users → Add user, fill in the email, name and role, then save. The new account is ready immediately.';
    }
    // full
    if (tone === 'formal') return 'Navigate to Settings → Users → Add user, enter the email, name and role, then confirm.';
    if (tone === 'casual') return "Easy — Settings → Users → Add user, fill in their details and save.";
    return 'To add a user, open Settings → Users → Add user, enter their details and save.';
  }

  function buildRefusal(x: Persona): string {
    const who = x.name || 'I';
    if (x.tone === 'formal') return `That question falls outside the documented runbooks, so ${who === 'I' ? 'I cannot' : who + ' cannot'} answer it from the knowledge base.`;
    if (x.tone === 'casual') return `Hmm, that's not something the runbooks cover — I can't help with that one.`;
    return `That's outside what the runbooks cover, so I'm not able to answer it from the knowledge base.`;
  }

  let avatarLetter = $derived((p.name || '?').trim().charAt(0).toUpperCase() || '?');
</script>

{#if toast}<div class="toast">{toast}</div>{/if}

{#if !me}
  <div class="muted">Loading…</div>
{:else if !isAdmin}
  <div class="muted">Admin only.</div>
{:else if loading}
  <div class="muted">Loading…</div>
{:else}
  <div class="ppad">
  <p class="lead">
    The agent's <b>persona</b> — its identity and voice, generated from your company documents.
    It shapes <i>how</i> answers sound (tone, length, who they're written for); it never changes
    the grounding or the answer rules.
  </p>

  <!-- ===== TOP IDENTITY CARD ===== -->
  <div class="topcard">
    <div class="avatar">{avatarLetter}</div>
    <div class="idmeta">
      <div class="idname">{p.name || 'Unnamed agent'}</div>
      <div class="idrole">{p.role || 'No role set'}</div>
      <div class="idfrom">generated from {p.generated_from || '—'} · v{p.version}</div>
    </div>
    <button class="gen" disabled={generating} onclick={generate}>
      {generating ? 'Scanning…' : '✨ Generate from knowledge'}
    </button>
  </div>

  <!-- ===== ENABLED ===== -->
  <div class="row">
    <div class="info">
      <div class="rt">Persona enabled</div>
      <div class="rd">
        When ON, every answer is delivered in this identity and voice. When OFF, the agent
        uses its default voice.
      </div>
      {#if !p.enabled}
        <div class="offnote">Persona off — the agent uses its default voice.</div>
      {/if}
    </div>
    <button class="sw" class:on={p.enabled}
      role="switch" aria-checked={p.enabled} aria-label="Toggle persona"
      onclick={() => (p.enabled = !p.enabled)}>
      <span class="knob"></span>
    </button>
  </div>

  <!-- ===== IDENTITY ===== -->
  <div class="sect">
    <div class="sect-h">Identity</div>
    <div class="grid">
      <label class="fld">
        <span>Name</span>
        <input bind:value={p.name} placeholder="Aria" />
      </label>
      <label class="fld">
        <span>Role</span>
        <input bind:value={p.role} placeholder="IT runbook assistant" />
      </label>
    </div>

    <!-- COVERS chips -->
    <div class="fld">
      <span>Covers <small>(the topics this agent handles)</small></span>
      <div class="chips">
        {#each p.covers as c, i (i)}
          <span class="chip">{c}<button class="chipx" aria-label="Remove" onclick={() => removeCover(i)}>✕</button></span>
        {/each}
        {#if !p.covers.length}<span class="chip-empty">No topics yet.</span>{/if}
      </div>
      <div class="addrow">
        <input bind:value={coverDraft} placeholder="e.g. user administration"
          onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCover(); } }} />
        <button class="addbtn" onclick={addCover}>Add</button>
      </div>
    </div>
  </div>

  <!-- ===== VOICE ===== -->
  <div class="sect">
    <div class="sect-h">Voice</div>
    <p class="lead2">How answers sound — tone, length, and who they're written for.</p>

    <div class="vrow">
      <div class="vlab">Tone</div>
      <div class="radios">
        {#each TONES as t}
          <button class="radio" class:on={p.tone === t.v} onclick={() => (p.tone = t.v)}>{t.label}</button>
        {/each}
      </div>
    </div>
    <div class="vrow">
      <div class="vlab">Length</div>
      <div class="radios">
        {#each LENGTHS as l}
          <button class="radio" class:on={p.length === l.v} onclick={() => (p.length = l.v)}>{l.label}</button>
        {/each}
      </div>
    </div>
    <div class="vrow">
      <div class="vlab">Audience</div>
      <div class="radios">
        {#each AUDIENCES as a}
          <button class="radio" class:on={p.audience === a.v} onclick={() => (p.audience = a.v)}>{a.label}</button>
        {/each}
      </div>
    </div>
  </div>

  <!-- ===== BEHAVIOUR RULES ===== -->
  <div class="sect">
    <div class="sect-h">Behaviour rules</div>
    <p class="lead2">Voice-level do's and don'ts layered on top of the answer rules (never grounding).</p>
    {#each p.rules as r, i (i)}
      <div class="rulerow">
        <input value={r} oninput={(e) => setRule(i, (e.currentTarget as HTMLInputElement).value)}
          placeholder="e.g. Always greet the user by name when known" />
        <button class="rulex" aria-label="Remove rule" onclick={() => removeRule(i)}>✕</button>
      </div>
    {/each}
    {#if !p.rules.length}<div class="muted2">No rules yet.</div>{/if}
    <button class="addrule" onclick={addRule}>+ add rule</button>
  </div>

  <!-- ===== GREETING ===== -->
  <div class="sect">
    <div class="sect-h">Greeting</div>
    <label class="fld">
      <span>One sentence the agent opens with</span>
      <input bind:value={p.greeting} placeholder="Hi, I'm Aria — how can I help with your runbooks today?" />
    </label>
  </div>

  <!-- ===== LIVE PREVIEW ===== -->
  <div class="sect">
    <div class="sect-h">Live preview</div>
    <div class="pv">
      <div class="pv-lab">Preview · reflects your edits</div>
      <div class="pv-card">
        {#if p.greeting.trim()}
          <div class="pv-greet"><span class="pv-av">{avatarLetter}</span>{p.greeting}</div>
        {/if}
        <div class="pv-qa">
          <div class="pv-q">Q: How do I add a user?</div>
          <div class="pv-a">A: {previewAnswer}</div>
        </div>
        <div class="pv-qa">
          <div class="pv-q">Q: What's the weather today?</div>
          <div class="pv-a refuse">A: {previewRefusal}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== FOOTER ===== -->
  <div class="foot">
    <button class="ghost" disabled={saving} onclick={revert}>Revert</button>
    <button class="save" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save'}</button>
  </div>

  <!-- ===== SELF-IMPROVEMENT (placeholder, P2 stub) ===== -->
  <div class="sect">
    <div class="sect-h">Self-improvement</div>
    <p class="lead2">Coming next — the persona will learn from feedback and self-correct (eval-gated).</p>
    <button class="save" disabled>Run review</button>

    <div class="hist-h">Version history</div>
    {#if history.length}
      <div class="hist">
        {#each history as h, i (i)}
          <div class="hrow">
            <span class="hver">v{h.version}</span>
            <span class="hfrom">{h.generated_from || '—'}</span>
            <span class="hrole">{h.role || ''}</span>
          </div>
        {/each}
      </div>
    {:else}
      <div class="muted2">No previous versions yet.</div>
    {/if}
  </div>
  </div>
{/if}

<style>
  .ppad { padding: 6px 28px 32px; max-width: 1040px; }
  .lead { color: #6b675e; font-size: 13px; max-width: 660px; margin: 0 0 18px; line-height: 1.55; }
  .muted { color: #8a857c; font-size: 13px; }
  .muted2 { color: #8a857c; font-size: 12.5px; margin: 6px 0; }

  /* top identity card */
  .topcard { display: flex; align-items: center; gap: 16px; max-width: 720px; padding: 16px 18px; border: 1px solid #e9e6dd; border-radius: 14px; background: #fff; }
  .avatar { flex: none; width: 52px; height: 52px; border-radius: 50%; background: #f6e9e2; color: #c2683f; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 700; }
  .idmeta { flex: 1; min-width: 0; }
  .idname { font-size: 16px; font-weight: 700; color: #1f1e1d; }
  .idrole { font-size: 13px; color: #46443f; margin-top: 1px; }
  .idfrom { font-size: 11.5px; color: #8a857c; margin-top: 3px; }
  .gen { flex: none; background: #c2683f; color: #fff; border: none; border-radius: 9px; padding: 9px 16px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .gen:hover { background: #a8542f; }
  .gen:disabled { opacity: .6; cursor: default; }

  /* enabled row (matches behaviour page) */
  .row { display: flex; align-items: flex-start; gap: 18px; max-width: 720px; padding: 16px; border: 1px solid #e9e6dd; border-radius: 14px; background: #fff; margin-top: 16px; }
  .info { flex: 1; min-width: 0; }
  .rt { font-weight: 600; font-size: 14px; color: #1f1e1d; }
  .rd { color: #6b675e; font-size: 12.5px; line-height: 1.5; margin-top: 4px; }
  .offnote { color: #8a857c; font-size: 12px; margin-top: 8px; font-style: italic; }
  .sw { flex: none; width: 46px; height: 26px; border-radius: 999px; border: none; background: #d8d3c7; cursor: pointer; position: relative; transition: background .16s; margin-top: 2px; }
  .sw.on { background: #3f8f5f; }
  .knob { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.2); transition: left .16s; }
  .sw.on .knob { left: 23px; }

  /* sections */
  .sect { max-width: 720px; margin-top: 26px; padding-top: 20px; border-top: 1px solid #e9e6dd; }
  .sect-h { font-size: 15px; font-weight: 700; color: #1f1e1d; }
  .lead2 { color: #6b675e; font-size: 12.5px; line-height: 1.5; margin: 4px 0 14px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .fld { display: block; margin-bottom: 12px; }
  .fld > span { display: block; font-size: 12.5px; font-weight: 600; color: #2a2824; margin-bottom: 5px; }
  .fld > span small { font-weight: 400; color: #8a857c; }
  .fld input { width: 100%; box-sizing: border-box; border: 1px solid #e0dfda; border-radius: 9px; padding: 8px 11px; font: inherit; font-size: 13px; color: #1f1e1d; background: #fff; }
  .fld input:focus { outline: none; border-color: #c2683f; }

  /* chips */
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; min-height: 24px; }
  .chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: #1f1e1d; background: #f6e9e2; border: 1px solid #ecccbd; border-radius: 8px; padding: 4px 8px; }
  .chipx { border: none; background: none; color: #a8542f; cursor: pointer; font-size: 11px; line-height: 1; padding: 0; }
  .chip-empty { font-size: 12px; color: #8a857c; }
  .addrow { display: flex; gap: 8px; }
  .addrow input { flex: 1; box-sizing: border-box; border: 1px solid #e0dfda; border-radius: 9px; padding: 8px 11px; font: inherit; font-size: 13px; color: #1f1e1d; background: #fff; }
  .addrow input:focus { outline: none; border-color: #c2683f; }
  .addbtn { flex: none; background: #f0eee6; border: 1px solid #e0dfda; border-radius: 9px; padding: 8px 14px; font: inherit; font-weight: 600; font-size: 13px; color: #2a2824; cursor: pointer; }
  .addbtn:hover { background: #e9e6dd; }

  /* voice radios */
  .vrow { display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }
  .vlab { flex: none; width: 86px; font-size: 12.5px; font-weight: 600; color: #2a2824; }
  .radios { display: flex; gap: 6px; flex-wrap: wrap; }
  .radio { background: #fff; border: 1px solid #e0dfda; border-radius: 9px; padding: 7px 14px; font: inherit; font-size: 13px; color: #46443f; cursor: pointer; transition: background .14s, color .14s, border-color .14s; }
  .radio:hover { background: #faf9f5; }
  .radio.on { background: #f6e9e2; border-color: #ecccbd; color: #c2683f; font-weight: 600; }

  /* rules */
  .rulerow { display: flex; gap: 8px; margin-bottom: 8px; }
  .rulerow input { flex: 1; box-sizing: border-box; border: 1px solid #e0dfda; border-radius: 9px; padding: 8px 11px; font: inherit; font-size: 13px; color: #1f1e1d; background: #fff; }
  .rulerow input:focus { outline: none; border-color: #c2683f; }
  .rulex { flex: none; border: 1px solid #e0dfda; background: #fff; border-radius: 9px; width: 36px; color: #a8542f; cursor: pointer; }
  .rulex:hover { background: #f6e9e2; }
  .addrule { background: #f0eee6; border: 1px solid #e0dfda; border-radius: 9px; padding: 7px 14px; font: inherit; font-weight: 600; font-size: 13px; color: #2a2824; cursor: pointer; }
  .addrule:hover { background: #e9e6dd; }

  /* preview */
  .pv-lab { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: #8a857c; font-weight: 600; margin-bottom: 6px; }
  .pv-card { border: 1px solid #e9e6dd; border-radius: 12px; background: #faf9f5; padding: 14px 16px; font-size: 13px; line-height: 1.6; color: #2a2824; }
  .pv-greet { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #1f1e1d; padding-bottom: 10px; margin-bottom: 10px; border-bottom: 1px solid #e9e6dd; }
  .pv-av { flex: none; width: 24px; height: 24px; border-radius: 50%; background: #f6e9e2; color: #c2683f; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
  .pv-qa { margin-bottom: 10px; }
  .pv-qa:last-child { margin-bottom: 0; }
  .pv-q { color: #6b675e; font-size: 12.5px; }
  .pv-a { color: #1f1e1d; margin-top: 2px; }
  .pv-a.refuse { color: #6b675e; }

  /* footer */
  .foot { display: flex; gap: 10px; max-width: 720px; margin-top: 24px; }
  .save { background: #1f1e1d; color: #fff; border: none; border-radius: 9px; padding: 9px 18px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .save:disabled { opacity: .55; cursor: default; }
  .ghost { background: #fff; color: #2a2824; border: 1px solid #e0dfda; border-radius: 9px; padding: 9px 18px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
  .ghost:hover { background: #faf9f5; }
  .ghost:disabled { opacity: .55; cursor: default; }

  /* version history */
  .hist-h { font-size: 12.5px; font-weight: 700; color: #2a2824; margin: 18px 0 8px; }
  .hist { border: 1px solid #e9e6dd; border-radius: 12px; background: #fff; overflow: hidden; }
  .hrow { display: flex; align-items: center; gap: 12px; padding: 9px 14px; font-size: 12.5px; border-bottom: 1px solid #f0eee6; }
  .hrow:last-child { border-bottom: none; }
  .hver { flex: none; font-weight: 700; color: #c2683f; }
  .hfrom { flex: 1; min-width: 0; color: #46443f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hrole { color: #8a857c; }

  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); background: #1f1e1d; color: #fff; padding: 9px 16px; border-radius: 10px; font-size: 13px; z-index: 60; box-shadow: 0 8px 24px rgba(0,0,0,.2); }
</style>
