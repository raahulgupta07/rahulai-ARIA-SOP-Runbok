<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  // folded into Mission Control — keep old bookmark working via anchor
  onMount(() => goto('/dashboard#exec', { replaceState: true }));
</script>
<div style="padding:28px 4px;color:var(--muted,#999);font-size:.9rem">Opening Mission Control → exec…</div>
