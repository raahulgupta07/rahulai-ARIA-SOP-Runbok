<script lang="ts">
  // Top-nav Eval page — the Eval section owns its own rail + scroll, so this
  // route just gives it the full height of the layout <main> (overflow-hidden).
  import Eval from '$lib/dashboard/sections/Eval.svelte';
</script>

<div class="evalpage">
  <Eval />
</div>

<style>
  .evalpage { height: 100%; overflow: hidden; }
</style>
