<script lang="ts">
  // Real line-icons for folders. stroke = currentColor, so the parent's
  // `color:` (the folder's chosen colour) tints the icon. Rendered as true
  // <svg><path> (NOT {@html}) so the SVG namespace is correct.
  let { name = 'folder', size = 18, strokeWidth = 2 }:
    { name?: string | null; size?: number; strokeWidth?: number } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width={strokeWidth} stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === 'chart'}
    <path d="M5 20V11" /><path d="M12 20V4" /><path d="M19 20v-6" /><line x1="3" y1="20" x2="21" y2="20" />
  {:else if name === 'archive'}
    <rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8" /><path d="M10 12h4" />
  {:else if name === 'doc'}
    <path d="M7 2h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" /><path d="M14 2v4h4" /><path d="M9 13h6" /><path d="M9 17h6" />
  {:else if name === 'database'}
    <ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" /><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
  {:else if name === 'wrench'}
    <path d="M15 4.5a4 4 0 0 0-5.1 5.1L3.5 16v4.5H8l6.4-6.4A4 4 0 0 0 19.5 9l-2.6 2.6-2-2L17.5 7Z" />
  {:else if name === 'cart'}
    <circle cx="9" cy="20" r="1.4" /><circle cx="18" cy="20" r="1.4" /><path d="M2 3h3l2.3 11.3a1 1 0 0 0 1 .8h8.5a1 1 0 0 0 1-.78L21 7.5H6" />
  {:else if name === 'bank'}
    <path d="M12 3l9 5H3l9-5Z" /><path d="M5 8v9" /><path d="M9.5 8v9" /><path d="M14.5 8v9" /><path d="M19 8v9" /><path d="M3 20h18" />
  {:else if name === 'box'}
    <path d="M3 8l9-5 9 5v8l-9 5-9-5Z" /><path d="M3 8l9 5 9-5" /><path d="M12 13v8" />
  {:else if name === 'trending'}
    <path d="M3 17l6-6 4 4 8-8" /><path d="M16 7h5v5" />
  {:else if name === 'gear'}
    <circle cx="12" cy="12" r="3.2" /><path d="M12 2v3.2M12 18.8V22M4.2 4.2l2.3 2.3M17.5 17.5l2.3 2.3M2 12h3.2M18.8 12H22M4.2 19.8l2.3-2.3M17.5 6.5l2.3-2.3" />
  {:else if name === 'puzzle'}
    <path d="M10.3 3a1.7 1.7 0 0 1 3.4 0V4.5H19V9.8h1.5a1.7 1.7 0 0 1 0 3.4H19V19h-5.3v-1.5a1.7 1.7 0 0 0-3.4 0V19H5v-5.3H3.5a1.7 1.7 0 0 1 0-3.4H5V4.5h5.3Z" />
  {:else}
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  {/if}
</svg>
