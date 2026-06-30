<script lang="ts">
  import { api } from '$lib/api';

  // ---- brand state (loaded from api.brand()) ----
  let brand = $state<any>(null);          // last-loaded server shape (for asset URLs + clean snapshot)
  let saved = $state('');                 // inline saving status
  let toast = $state('');                 // floating success/error toast
  let loading = $state(true);

  // editable form fields
  let name = $state('');
  let short_name = $state('');
  let tagline = $state('');
  let footer = $state('');
  let assistant_label = $state('');
  let accent = $state('#c2683f');

  // chosen logo file + preview
  let logoFile = $state<File | null>(null);
  let logoPreview = $state('');           // object URL of the chosen file
  let dragging = $state(false);
  let fileInput: HTMLInputElement;

  const PRESETS = ['#c2683f', '#426693', '#2f8f83', '#7b6bd6', '#1a1a18'];

  // snapshot of last-saved words+accent → dirty indicator (logo file is also dirty)
  let clean = $state('');
  function snap() {
    return JSON.stringify({ name, short_name, tagline, footer, assistant_label, accent });
  }
  let dirty = $derived(loading ? false : (snap() !== clean || !!logoFile));

  function fill(b: any) {
    name = b?.name ?? '';
    short_name = b?.short_name ?? '';
    tagline = b?.tagline ?? '';
    footer = b?.footer ?? '';
    assistant_label = b?.assistant_label ?? '';
    accent = b?.accent || '#c2683f';
  }

  async function load() {
    loading = true;
    try {
      const b = await api.brand();
      brand = b;
      fill(b);
      clean = snap();
    } catch (e: any) {
      flash('Could not load branding: ' + (e?.message || 'error'));
    } finally {
      loading = false;
    }
  }
  $effect(() => { if (!brand && loading) load(); });

  // ---- logo picking ----
  function pickFile(f: File | null | undefined) {
    if (!f) return;
    if (logoPreview) URL.revokeObjectURL(logoPreview);
    logoFile = f;
    logoPreview = URL.createObjectURL(f);
  }
  function onFileInput(e: Event) {
    const t = e.target as HTMLInputElement;
    pickFile(t.files?.[0]);
  }
  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    pickFile(e.dataTransfer?.files?.[0]);
  }
  function removeLogo() {
    if (logoPreview) URL.revokeObjectURL(logoPreview);
    logoFile = null;
    logoPreview = '';
    if (fileInput) fileInput.value = '';
  }

  // a usable hex (8 chars max, leading #) for live styling — falls back if mid-typing
  let liveAccent = $derived(/^#[0-9a-fA-F]{3,8}$/.test(accent) ? accent : (brand?.accent || '#c2683f'));

  function flash(msg: string) { toast = msg; setTimeout(() => (toast = ''), 2800); }

  async function save() {
    saved = 'saving…';
    try {
      const fd = new FormData();
      if (logoFile) fd.append('logo', logoFile);
      fd.append('name', name);
      fd.append('short_name', short_name);
      fd.append('tagline', tagline);
      fd.append('footer', footer);
      fd.append('assistant_label', assistant_label);
      fd.append('accent', accent);
      const b = await api.saveBrand(fd);
      brand = b;
      fill(b);
      clean = snap();
      removeLogo();           // chosen file is now processed server-side
      saved = '';
      // re-fetch to refresh derived asset URLs (mark/favicon/icons)
      await load();
      flash('Saved ✓ — branding updated');
    } catch (e: any) {
      saved = '';
      flash('Error: ' + (e?.message || 'save failed'));
    }
  }

  // asset tiles — show real URL if present, else accent placeholder
  const ASSETS = $derived([
    { key: 'mark_url',    label: 'Square mark', hint: 'Collapsed rail' },
    { key: 'favicon_url', label: 'Favicon',     hint: 'Browser tab' },
    { key: 'icon192_url', label: 'PWA 192',     hint: 'Home screen' },
    { key: 'icon512_url', label: 'PWA 512',     hint: 'App icon' },
  ]);
</script>

<div class="h-full overflow-y-auto" style="background:var(--cream)">
  <div class="px-7 py-6 wrap">
    <div class="flex items-center justify-between gap-3 mb-5">
      <p class="ttlsub">Upload one logo — name, mark, favicon, app icons and accent colour are generated for you.</p>
      <div class="flex items-center gap-3">
        {#if dirty}<span class="unsaved">● Unsaved changes</span>{/if}
        <span class="text-xs" style="color:var(--muted)">{saved}</span>
        <button class="btn pri" class:nudge={dirty} onclick={save} disabled={loading || !!saved}>{saved ? 'Saving…' : 'Save branding'}</button>
      </div>
    </div>

    {#if loading}
      <div class="empty">Loading branding…</div>
    {:else}
      <div class="grid2">
        <!-- LEFT: editors -->
        <div class="col">
          <!-- logo dropzone -->
          <div class="card">
            <div class="lbl">Your logo</div>
            <input bind:this={fileInput} type="file" accept="image/png,image/svg+xml,image/jpeg,image/webp" onchange={onFileInput} hidden />

            {#if logoPreview}
              <div class="logoset">
                <div class="logobox"><img src={logoPreview} alt="chosen logo preview" /></div>
                <div class="logoinfo">
                  <div class="hint-ok">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
                    Ready — we'll generate the mark, favicon &amp; app icons on save.
                  </div>
                  <div class="logofile">{logoFile?.name}</div>
                  <div class="logoacts">
                    <button class="btn sm" onclick={() => fileInput?.click()}>Replace</button>
                    <button class="btn ghost sm" onclick={removeLogo}>Remove</button>
                  </div>
                </div>
              </div>
            {:else if brand?.logo_url}
              <div class="logoset">
                <div class="logobox"><img src={brand.logo_url} alt="current logo" /></div>
                <div class="logoinfo">
                  <div class="logofile cur">Current logo</div>
                  <div class="logoacts"><button class="btn sm" onclick={() => fileInput?.click()}>Replace logo</button></div>
                </div>
              </div>
            {:else}
              <button
                class="dropzone"
                class:over={dragging}
                onclick={() => fileInput?.click()}
                ondragover={(e) => { e.preventDefault(); dragging = true; }}
                ondragleave={() => (dragging = false)}
                ondrop={onDrop}
              >
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/></svg>
                <div class="dz-t">Drop your logo here</div>
                <div class="dz-s">PNG or SVG · we handle the rest</div>
              </button>
            {/if}
          </div>

          <!-- generated accent + assets -->
          <div class="card">
            <div class="lbl">Generated automatically</div>
            <div class="accrow">
              <span class="swatch" style="background:{liveAccent}"></span>
              <label class="fg hexfg"><span>Accent colour</span><input bind:value={accent} placeholder="#c2683f" spellcheck="false" /></label>
            </div>
            <div class="presets">
              {#each PRESETS as p}
                <button class="preset" class:on={accent.toLowerCase() === p} style="background:{p}" onclick={() => (accent = p)} aria-label={p} title={p}>
                  {#if accent.toLowerCase() === p}
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
                  {/if}
                </button>
              {/each}
            </div>

            <div class="tiles">
              {#each ASSETS as a}
                <div class="tile">
                  <div class="tile-img" style="background:{liveAccent}">
                    {#if brand?.[a.key]}<img src={brand[a.key]} alt={a.label} />{/if}
                  </div>
                  <div class="tile-nm">{a.label}</div>
                  <div class="tile-hint">{a.hint}</div>
                </div>
              {/each}
            </div>
            <div class="tip">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
              <span>These derived assets refresh after you save a new logo.</span>
            </div>
          </div>

          <!-- words -->
          <div class="card">
            <div class="lbl">Words</div>
            <div class="g2">
              <label class="fg"><span>Product name</span><input bind:value={name} placeholder="City Agent Aria" /></label>
              <label class="fg"><span>Short name</span><input bind:value={short_name} placeholder="Aria" /></label>
            </div>
            <label class="fg"><span>Tagline <span class="hint">— shown under the name</span></span><input bind:value={tagline} placeholder="Agent for Runbooks &amp; IT Assistance" /></label>
            <label class="fg"><span>Assistant label <span class="hint">— name shown on each answer</span></span><input bind:value={assistant_label} placeholder="Aria 1.0" /></label>
            <label class="fg"><span>Footer</span><input bind:value={footer} placeholder="© Your Company" /></label>
          </div>
        </div>

        <!-- RIGHT: sticky live preview -->
        <div class="col">
          <div class="preview" style="--acc:{liveAccent}">
            <div class="lbl">Live preview</div>

            <!-- mini header -->
            <div class="pv-card pv-head">
              <span class="pv-logo">
                {#if logoPreview}<img src={logoPreview} alt="" />
                {:else if brand?.mark_url}<img src={brand.mark_url} alt="" />
                {:else if brand?.logo_url}<img src={brand.logo_url} alt="" />
                {:else}<span class="pv-logo-fb">{(short_name || name || 'A').slice(0, 1)}</span>{/if}
              </span>
              <div class="pv-words">
                <div class="pv-nm">{name || 'Product name'}</div>
                <div class="pv-tag">{tagline || 'Tagline goes here'}</div>
              </div>
              <span class="pv-pill">●</span>
            </div>

            <!-- chat bubble -->
            <div class="pv-card pv-chat">
              <div class="pv-q">How do I create a new site?</div>
              <div class="pv-a">
                <span class="pv-alabel">{assistant_label || short_name || 'Assistant'}</span>
                <p>Open <b>Site Setup</b>, fill the required fields, then confirm. Here's the procedure…</p>
                <span class="pv-cite">[p.4]</span>
              </div>
            </div>

            <!-- login card -->
            <div class="pv-card pv-login">
              <span class="pv-logo lg">
                {#if logoPreview}<img src={logoPreview} alt="" />
                {:else if brand?.logo_url}<img src={brand.logo_url} alt="" />
                {:else}<span class="pv-logo-fb">{(short_name || name || 'A').slice(0, 1)}</span>{/if}
              </span>
              <div class="pv-login-ttl">Sign in to {short_name || name || 'your workspace'}</div>
              <div class="pv-field"></div>
              <div class="pv-field"></div>
              <button class="pv-btn">Continue</button>
              <div class="pv-foot">{footer || '© Your Company'}</div>
            </div>
          </div>
        </div>
      </div>
    {/if}
  </div>

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .wrap{max-width:1280px;}
  .ttlsub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .unsaved{font-size:12px; font-weight:600; color:#9a6a16;}
  .empty{font-size:13px; color:var(--muted); border:1px dashed var(--border); border-radius:12px; padding:24px; text-align:center;}

  .grid2{display:grid; grid-template-columns:1fr 380px; gap:18px; align-items:start;}
  .col{display:flex; flex-direction:column; gap:16px; min-width:0;}

  .card{border:1px solid var(--border); border-radius:14px; padding:16px 17px; background:#fff;}
  .lbl{font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; color:var(--muted); margin-bottom:12px;}

  /* dropzone */
  .dropzone{display:flex; flex-direction:column; align-items:center; gap:6px; width:100%; border:2px dashed var(--border); border-radius:13px; padding:30px 16px; background:#faf9f7; color:var(--muted); cursor:pointer; transition:border-color .14s, background .14s, color .14s;}
  .dropzone:hover, .dropzone.over{border-color:var(--clay); background:#fbf3ee; color:var(--clay);}
  .dropzone svg{opacity:.8;}
  .dz-t{font-size:14px; font-weight:600; color:var(--ink); margin-top:4px;}
  .dz-s{font-size:12px; color:var(--muted);}

  .logoset{display:flex; gap:15px; align-items:center;}
  .logobox{width:96px; height:96px; flex:0 0 auto; border:1px solid var(--border); border-radius:12px; background:#fafafa; display:grid; place-items:center; padding:10px;}
  .logobox img{max-width:100%; max-height:100%; object-fit:contain;}
  .logoinfo{flex:1; min-width:0;}
  .hint-ok{display:flex; align-items:center; gap:6px; font-size:12.5px; color:#2f8f5f; font-weight:500; line-height:1.4;}
  .logofile{font-size:12px; color:var(--muted); margin-top:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .logofile.cur{color:var(--ink); font-weight:500;}
  .logoacts{display:flex; gap:8px; margin-top:11px;}

  /* accent + assets */
  .accrow{display:flex; align-items:flex-end; gap:13px;}
  .swatch{width:46px; height:46px; flex:0 0 auto; border-radius:11px; border:1px solid var(--border); box-shadow:inset 0 0 0 1px rgba(255,255,255,.4);}
  .hexfg{flex:1;}
  .presets{display:flex; gap:9px; margin-top:13px;}
  .preset{width:30px; height:30px; border-radius:8px; border:1px solid rgba(0,0,0,.12); cursor:pointer; display:grid; place-items:center; transition:transform .1s;}
  .preset:hover{transform:scale(1.08);}
  .preset.on{box-shadow:0 0 0 2px #fff, 0 0 0 4px rgba(0,0,0,.22);}

  .tiles{display:grid; grid-template-columns:repeat(4,1fr); gap:11px; margin-top:16px;}
  .tile{text-align:center;}
  .tile-img{width:100%; aspect-ratio:1; border-radius:11px; border:1px solid var(--border); display:grid; place-items:center; overflow:hidden;}
  .tile-img img{width:100%; height:100%; object-fit:contain; background:#fff;}
  .tile-nm{font-size:11.5px; font-weight:600; color:var(--ink); margin-top:6px;}
  .tile-hint{font-size:10.5px; color:var(--muted);}

  .tip{display:flex; gap:8px; align-items:flex-start; background:#f4f3f0; color:var(--muted); border-radius:10px; padding:9px 11px; font-size:12px; line-height:1.5; margin-top:14px;}
  .tip svg{flex:0 0 auto; margin-top:2px;}

  /* words */
  .hint{font-weight:400; color:var(--muted); font-size:11px;}
  .fg{display:block; padding:7px 0;}
  .fg span{display:block; font-size:12px; color:var(--muted); margin-bottom:5px;}
  .fg input{width:100%; height:40px; border:1px solid var(--border); border-radius:9px; padding:0 12px; font-size:13.5px; background:#fff; outline:none; box-sizing:border-box;}
  .fg input:focus{border-color:var(--clay);}
  .g2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}

  /* buttons (match auth page) */
  .btn{height:38px; padding:0 16px; border-radius:9px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:#fff;}
  .btn.sm{height:32px; padding:0 13px; font-size:12.5px;}
  .btn.pri{background:var(--clay); color:#fff; border-color:var(--clay);}
  .btn.pri:disabled{opacity:.6; cursor:default;}
  .btn.ghost{border:none; background:transparent; color:var(--muted);}
  .btn.nudge{box-shadow:0 0 0 3px rgba(194,104,63,.22);}

  /* ---- live preview (right, sticky) ---- */
  .preview{position:sticky; top:14px; border:1px solid var(--border); border-radius:16px; padding:15px; background:#faf9f7; display:flex; flex-direction:column; gap:12px;}
  .pv-card{border:1px solid var(--border); border-radius:12px; background:#fff;}

  .pv-head{display:flex; align-items:center; gap:11px; padding:11px 13px;}
  .pv-logo{width:34px; height:34px; flex:0 0 auto; border-radius:9px; overflow:hidden; display:grid; place-items:center; background:#f4f3f0;}
  .pv-logo img{max-width:100%; max-height:100%; object-fit:contain;}
  .pv-logo.lg{width:48px; height:48px; border-radius:12px; margin:0 auto;}
  .pv-logo-fb{font-weight:700; font-size:15px; color:var(--acc);}
  .pv-words{flex:1; min-width:0;}
  .pv-nm{font-size:14px; font-weight:600; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .pv-tag{font-size:11px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .pv-pill{font-size:9px; color:var(--acc);}

  .pv-chat{padding:13px;}
  .pv-q{display:inline-block; background:var(--acc); color:#fff; font-size:12.5px; padding:7px 12px; border-radius:13px 13px 13px 3px; margin-bottom:10px;}
  .pv-a{font-size:12.5px; color:var(--ink); line-height:1.5;}
  .pv-alabel{display:inline-block; font-size:10px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--acc); margin-bottom:4px;}
  .pv-a p{margin:0;}
  .pv-a b{color:var(--ink);}
  .pv-cite{display:inline-block; font-size:10px; font-weight:700; color:var(--acc); background:color-mix(in srgb, var(--acc) 12%, #fff); padding:1px 6px; border-radius:5px; margin-top:6px;}

  .pv-login{padding:18px 16px; text-align:center; display:flex; flex-direction:column; align-items:center; gap:9px;}
  .pv-login-ttl{font-size:13.5px; font-weight:600; color:var(--ink);}
  .pv-field{width:100%; height:32px; border:1px solid var(--border); border-radius:8px; background:#fafafa;}
  .pv-btn{width:100%; height:36px; border:none; border-radius:9px; background:var(--acc); color:#fff; font-size:13px; font-weight:600; cursor:default;}
  .pv-foot{font-size:10.5px; color:var(--muted); margin-top:2px;}

  @media (max-width:980px){
    .grid2{grid-template-columns:1fr;}
    .preview{position:static;}
  }
  @media (max-width:640px){
    .g2{grid-template-columns:1fr;}
    .tiles{grid-template-columns:repeat(2,1fr);}
    .logoset{flex-direction:column; align-items:flex-start;}
  }
</style>
