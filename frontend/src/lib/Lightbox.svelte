<script lang="ts">
  let { src = $bindable(''), caption = '' } = $props();
</script>

{#if src}
  <button
    class="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6 cursor-zoom-out"
    onclick={() => (src = '')}
    aria-label="Close image">
    <figure class="max-h-full max-w-full flex flex-col items-center gap-2">
      <img src={src} alt={caption} class="max-h-[85vh] max-w-full rounded-lg shadow-2xl object-contain bg-white" />
      {#if caption}<figcaption class="text-stone-200 text-sm">{caption}</figcaption>{/if}
    </figure>
  </button>
{/if}
