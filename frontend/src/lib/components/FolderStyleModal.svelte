<script lang="ts">
  // ── FolderStyleModal — customise a folder's COLOR + ICON ──
  // Self-contained Svelte 5 (runes). No external deps.
  // CSS-token landmine (see sources/+page.svelte): app.css :root does NOT define
  // --line/--ink/--muted/--coral/etc — they are declared LOCALLY on the root
  // elements below (.scrim/.modal) so this component renders correctly no matter
  // where it's mounted. Only --brand / --navpill / --hover exist globally.

  type Saved = { color: string | null; icon: string | null };

  let {
    open = false,
    name = '',
    color = null,
    icon = null,
    onsave,
    onclose,
  }: {
    open?: boolean;
    name?: string;
    color?: string | null;
    icon?: string | null;
    onsave: (v: Saved) => void;
    onclose: () => void;
  } = $props();

  // ── swatch palette (label via title attr) ──
  const COLORS: { hex: string; label: string }[] = [
    { hex: '#c2683f', label: 'Coral' },
    { hex: '#3f7fb0', label: 'Blue' },
    { hex: '#2f8f83', label: 'Teal' },
    { hex: '#7b6bd6', label: 'Violet' },
    { hex: '#c98a2e', label: 'Amber' },
    { hex: '#3f8f5f', label: 'Green' },
    { hex: '#c0492f', label: 'Red' },
    { hex: '#6b7280', label: 'Slate' },
  ];
  const ICONS = ['📁', '📊', '🗂️', '🧾', '🗄️', '🔧', '🛒', '🏦', '📦', '📈', '⚙️', '🧩'];

  // ── local selection state ──
  let selColor = $state<string | null>(color);
  let selIcon = $state<string | null>(icon);

  // reset from props each time the modal (re)opens for a folder
  let wasOpen = $state(false);
  $effect(() => {
    if (open && !wasOpen) {
      selColor = color;
      selIcon = icon;
    }
    wasOpen = open;
  });

  // live-preview icon = chosen icon or default folder glyph
  let previewIcon = $derived(selIcon || '📁');

  function pickColor(hex: string | null) { selColor = hex; }
  function pickIcon(ic: string | null) { selIcon = ic; }

  function save() {
    onsave({ color: selColor, icon: selIcon });
    onclose();   // close on save (parent may also close; harmless)
  }
</script>

{#if open}
  <div class="scrim" role="button" tabindex="-1" aria-label="Close"
    onclick={onclose}
    onkeydown={(e) => { if (e.key === 'Escape') onclose(); }}></div>

  <div class="modal" role="dialog" aria-modal="true" aria-label="Folder style"
    onkeydown={(e) => { if (e.key === 'Escape') onclose(); }}>
    <div class="head">
      <h2>Customise folder</h2>
      <button type="button" class="x" title="Close" aria-label="Close" onclick={onclose}>✕</button>
    </div>

    <!-- live preview -->
    <div class="preview" style="--pc:{selColor || '#8b8b85'}">
      <span class="bar"></span>
      <span class="pico" style="color:{selColor || 'var(--ink)'}">{previewIcon}</span>
      <span class="pname">{name || 'Folder'}</span>
    </div>

    <!-- COLOR -->
    <div class="sect">
      <div class="lbl">Color</div>
      <div class="swatches">
        {#each COLORS as c (c.hex)}
          <button type="button" class="sw" class:on={selColor === c.hex}
            style="--c:{c.hex}" title={c.label} aria-label={c.label}
            aria-pressed={selColor === c.hex}
            onclick={() => pickColor(c.hex)}>
            {#if selColor === c.hex}<span class="ck">✓</span>{/if}
          </button>
        {/each}
        <button type="button" class="none" class:on={selColor === null}
          title="Default color" aria-pressed={selColor === null}
          onclick={() => pickColor(null)}>None</button>
      </div>
    </div>

    <!-- ICON -->
    <div class="sect">
      <div class="lbl">Icon</div>
      <div class="icons">
        {#each ICONS as ic (ic)}
          <button type="button" class="ib" class:on={selIcon === ic}
            title={ic} aria-label={'Icon ' + ic} aria-pressed={selIcon === ic}
            onclick={() => pickIcon(ic)}>{ic}</button>
        {/each}
        <button type="button" class="none" class:on={selIcon === null}
          title="Default icon" aria-pressed={selIcon === null}
          onclick={() => pickIcon(null)}>None</button>
      </div>
    </div>

    <div class="actions">
      <button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
      <button type="button" class="btn-save" onclick={save}>Save</button>
    </div>
  </div>
{/if}

<style>
  /* Local token block (app.css :root lacks these) — declared on the modal roots
     so nothing collapses to white-on-white wherever this renders. */
  .scrim, .modal {
    --paper: #ffffff; --sand: #f9f9f8; --cream: #ffffff;
    --ink: #1a1a18; --muted: #8b8b85; --line: #e0dfda;
    --coral: var(--brand, #c2683f);
  }

  .scrim { position: fixed; inset: 0; background: rgba(31, 30, 29, .32); z-index: 80; border: none; }

  .modal {
    position: fixed; z-index: 81; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: min(440px, calc(100vw - 32px)); background: var(--paper);
    border: 1px solid var(--line); border-radius: 16px; padding: 18px 22px 18px;
    max-height: calc(100vh - 40px); overflow-y: auto;
  }

  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .head h2 { margin: 0; font-size: 17px; font-weight: 650; color: var(--ink); }
  .x { border: none; background: none; font-size: 14px; color: var(--muted); cursor: pointer; padding: 4px 8px; border-radius: 8px; line-height: 1; }
  .x:hover { background: var(--sand); }

  /* live preview folder chip */
  .preview {
    display: flex; align-items: center; gap: 10px; position: relative;
    background: var(--sand); border: 1px solid var(--line); border-radius: 11px;
    padding: 11px 13px 11px 15px; margin-bottom: 16px; overflow: hidden;
  }
  .preview .bar { position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--pc); }
  .pico { font-size: 20px; line-height: 1; flex: none; }
  .pname { font-size: 14px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .sect { margin-bottom: 16px; }
  .lbl { font-size: 12.5px; font-weight: 600; color: var(--muted); margin-bottom: 9px; }

  /* color swatches */
  .swatches { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }
  .sw {
    width: 30px; height: 30px; border-radius: 999px; border: 2px solid transparent;
    background: var(--c); cursor: pointer; position: relative; padding: 0;
    display: inline-flex; align-items: center; justify-content: center;
    transition: transform .12s, box-shadow .12s;
  }
  .sw:hover { transform: scale(1.08); }
  .sw.on { box-shadow: 0 0 0 2px var(--paper), 0 0 0 4px var(--c); }
  .sw .ck { color: #fff; font-size: 14px; font-weight: 700; line-height: 1; text-shadow: 0 1px 2px rgba(0,0,0,.35); }

  /* icon grid */
  .icons { display: grid; grid-template-columns: repeat(7, 1fr); gap: 7px; }
  .ib {
    aspect-ratio: 1 / 1; min-height: 36px; border: 1px solid var(--line);
    background: var(--paper); border-radius: 10px; font-size: 18px; line-height: 1;
    cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
    transition: background .12s, border-color .12s, transform .12s;
  }
  .ib:hover { background: var(--sand); transform: translateY(-1px); }
  .ib.on { border-color: var(--coral); background: color-mix(in srgb, var(--coral) 12%, #fff); box-shadow: 0 0 0 1px var(--coral) inset; }

  /* shared "None" chip */
  .none {
    border: 1px dashed var(--line); background: var(--paper); border-radius: 999px;
    padding: 6px 13px; font: inherit; font-size: 12.5px; font-weight: 600;
    color: var(--muted); cursor: pointer; height: 30px;
    display: inline-flex; align-items: center; transition: background .12s, color .12s, border-color .12s;
  }
  .icons .none { border-radius: 10px; height: auto; min-height: 36px; grid-column: span 2; justify-content: center; }
  .none:hover { background: var(--sand); }
  .none.on { border-style: solid; border-color: var(--coral); color: var(--coral); background: color-mix(in srgb, var(--coral) 10%, #fff); }

  .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
  .btn-cancel { border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 9px 15px; font: inherit; font-size: 13px; font-weight: 600; color: #4a463e; cursor: pointer; }
  .btn-cancel:hover { background: var(--sand); }
  .btn-save { border: none; background: var(--coral); color: #fff; border-radius: 9px; padding: 9px 18px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-save:hover { background: color-mix(in srgb, var(--coral) 86%, #000); }

  @media (prefers-reduced-motion: reduce) {
    .sw, .ib, .none { transition: none; }
    .sw:hover, .ib:hover { transform: none; }
  }
</style>
