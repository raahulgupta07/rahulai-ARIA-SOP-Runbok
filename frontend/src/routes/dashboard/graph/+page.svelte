<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from '$lib/api';
  import { auth } from '$lib/auth';
  import { pickNode } from '$lib/dashutil';
  import BrainGraph from '$lib/BrainGraph.svelte';

  let me = $state(auth.cachedUser());
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });
  let isAdmin = $derived((me?.role === 'admin' || me?.role === 'superadmin'));
  let view = $state<'brain' | 'people'>('brain');
  let brainData = $state<any>(null);
  let peopleData = $state<any>(null);
  let egoData = $state<any>(null);

  function fetchBrain() { api.brainGraph().then((r) => (brainData = r)).catch(() => {}); }
  function fetchPeople() { api.graphPeople().then((r) => (peopleData = r)).catch(() => {}); }
  function fetchEgo() { api.graphMe().then((r) => (egoData = r)).catch(() => {}); }

  let loaded = false;
  $effect(() => {
    if (loaded) return;
    if (isAdmin) { loaded = true; fetchBrain(); fetchPeople(); }
    else if (me) { loaded = true; fetchEgo(); }
  });

  // live: re-pull periodically so newly-learned docs/facts flash into the brain
  const poll = setInterval(() => {
    if (isAdmin) { view === 'brain' ? fetchBrain() : fetchPeople(); }
    else fetchEgo();
  }, 30000);
  onDestroy(() => clearInterval(poll));

  const active = $derived(isAdmin ? (view === 'brain' ? brainData : peopleData) : egoData);
</script>

<div class="mapbar" style="display:flex; align-items:center; gap:12px;">
  {#if isAdmin}
    <div class="seg-group sm">
      <button class="segb vpill" class:on={view === 'brain'} onclick={() => (view = 'brain')}>Brain</button>
      <button class="segb vpill" class:on={view === 'people'} onclick={() => (view = 'people')}>People</button>
    </div>
    <span class="cnote">
      {#if view === 'brain'}{brainData?.nodes?.length ?? 0} neurons · {brainData?.links?.length ?? 0} synapses — docs · pages · facts
      {:else}Bipartite map — who uses which documents · {peopleData?.counts?.users ?? 0} people · {peopleData?.counts?.docs ?? 0} docs{/if}
    </span>
    <a class="mlink" href="/brain">open in Brain →</a>
  {:else}
    <span class="cnote">Your knowledge brain — docs you used + facts you taught</span>
  {/if}
</div>

<div class="gfull">
  {#if !isAdmin && !me}
    <div class="muted pad" style="color:#b7b1a4">Loading…</div>
  {:else if active && (active.nodes?.length > (isAdmin ? 0 : 1))}
    {#key isAdmin ? view : 'ego'}
      <BrainGraph data={active} fill onpick={pickNode} />
    {/key}
  {:else if active}
    <div class="muted pad" style="color:#b7b1a4">{isAdmin ? 'No data yet.' : 'Ask questions in Chat to grow your brain.'}</div>
  {:else}
    <div class="muted pad" style="color:#b7b1a4">Waking up the brain…</div>
  {/if}
</div>

<style>
  .vpill.on { background: rgba(63, 127, 176, 0.15); color: #3f7fb0; border-color: #3f7fb0; }
</style>
