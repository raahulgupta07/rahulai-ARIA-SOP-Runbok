<script lang="ts">
  // One settings line: label + hint on the left, control on the right.
  let { label = '', hint = '', stacked = false, children }: {
    label?: string; hint?: string; stacked?: boolean; children?: any;
  } = $props();
</script>

<div class="srow" class:stacked>
  <div class="srow-l">
    <span class="srow-lbl">{label}</span>
    {#if hint}<span class="srow-hint">{hint}</span>{/if}
  </div>
  <div class="srow-c">{@render children?.()}</div>
</div>

<style>
  .srow {
    display: flex; align-items: center; justify-content: space-between; gap: 18px;
    padding: 11px 0; border-bottom: 1px solid var(--border, #ececea);
  }
  .srow:last-child { border-bottom: 0; }
  .srow.stacked { flex-direction: column; align-items: stretch; gap: 8px; }
  .srow-l { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .srow-lbl { font-size: 13.5px; font-weight: 500; color: var(--ink, #1f1e1d); }
  .srow-hint { font-size: 11.5px; color: var(--muted, #7a756c); line-height: 1.45; }
  .srow-c { flex: none; display: flex; align-items: center; gap: 8px; }
  .srow.stacked .srow-c { flex: 1; }
</style>
