<script lang="ts">
  // Shared settings card. Gives every settings page one consistent block:
  // optional title + description + right-aligned actions, then body content.
  let { title = '', desc = '', children, actions }: {
    title?: string; desc?: string; children?: any; actions?: any;
  } = $props();
</script>

<section class="sset">
  {#if title || actions}
    <div class="sset-head">
      <div class="sset-meta">
        {#if title}<h3>{title}</h3>{/if}
        {#if desc}<p>{desc}</p>{/if}
      </div>
      {#if actions}<div class="sset-actions">{@render actions()}</div>{/if}
    </div>
  {/if}
  <div class="sset-body">{@render children?.()}</div>
</section>

<style>
  .sset {
    background: #fff; border: 1px solid var(--border, #efefec); border-radius: 14px;
    padding: 18px 20px; margin-bottom: 16px;
  }
  .sset-head {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 14px;
    margin-bottom: 14px; padding-bottom: 13px; border-bottom: 1px solid var(--border, #eee);
  }
  .sset-meta { min-width: 0; }
  .sset-head h3 { font-size: 14.5px; font-weight: 700; color: var(--ink, #1f1e1d); margin: 0; }
  .sset-head p { font-size: 12.5px; color: var(--muted, #7a756c); margin: 4px 0 0; line-height: 1.5; }
  .sset-actions { flex: none; display: flex; gap: 8px; align-items: center; }
</style>
