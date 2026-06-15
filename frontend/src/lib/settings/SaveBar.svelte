<script lang="ts">
  // Sticky save bar — appears only when there are unsaved changes.
  let { dirty = false, saving = false, onsave, onreset }: {
    dirty?: boolean; saving?: boolean; onsave?: () => void; onreset?: () => void;
  } = $props();
</script>

{#if dirty}
  <div class="savebar">
    <span class="sb-txt"><span class="sb-dot"></span> Unsaved changes</span>
    <div class="sb-btns">
      {#if onreset}<button class="sb-reset" onclick={onreset} disabled={saving}>Reset</button>{/if}
      <button class="sb-save" onclick={onsave} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
    </div>
  </div>
{/if}

<style>
  .savebar {
    position: sticky; bottom: 16px; z-index: 10;
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    background: #211f1c; color: #f3f1ea; border-radius: 12px;
    padding: 11px 16px; margin-top: 18px;
    box-shadow: 0 8px 26px rgba(0,0,0,0.22);
    animation: sbUp 0.28s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  @keyframes sbUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  .sb-txt { font-size: 13px; display: inline-flex; align-items: center; gap: 8px; }
  .sb-dot { width: 8px; height: 8px; border-radius: 50%; background: #e7a44e; flex: none; }
  .sb-btns { display: flex; gap: 8px; }
  .sb-reset { font-size: 12.5px; padding: 7px 14px; border-radius: 8px; background: transparent; color: #cfcabf; border: 1px solid #4a4640; cursor: pointer; }
  .sb-reset:hover { color: #fff; border-color: #6a655c; }
  .sb-save { font-size: 12.5px; font-weight: 600; padding: 7px 16px; border-radius: 8px; background: #1a1a18; color: #fff; border: none; cursor: pointer; }
  .sb-save:hover { background: #000000; }
  .sb-save:disabled, .sb-reset:disabled { opacity: 0.6; cursor: default; }
  @media (prefers-reduced-motion: reduce) { .savebar { animation: none; } }
</style>
