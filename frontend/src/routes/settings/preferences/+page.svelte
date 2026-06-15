<script lang="ts">
  import { prefs, type Density, type Landing } from '$lib/prefs';
  import Section from '$lib/settings/Section.svelte';
  import Row from '$lib/settings/Row.svelte';
  import Toggle from '$lib/settings/Toggle.svelte';

  // bind directly to the store's current value via local mirrors that write back.
  let density = $state<Density>($prefs.density);
  let reduceMotion = $state<boolean>($prefs.reduceMotion);
  let landing = $state<Landing>($prefs.landing);

  // every change is applied + persisted instantly (no save bar needed for prefs)
  $effect(() => { prefs.set({ density, reduceMotion, landing }); });

  const DENSITIES: { v: Density; label: string; hint: string }[] = [
    { v: 'comfortable', label: 'Comfortable', hint: 'Roomy spacing' },
    { v: 'compact', label: 'Compact', hint: 'More on screen' }
  ];
  const LANDINGS: { v: Landing; label: string }[] = [
    { v: '/', label: 'Chat' },
    { v: '/dashboard', label: 'Dashboard' },
    { v: '/brain', label: 'Brain' }
  ];

  // when rendered inside the Settings Overview, drop the page padding wrapper
  let { embedded = false } = $props<{ embedded?: boolean }>();
</script>

<div class={embedded ? '' : 'px-7 py-6'}>
  <Section title="Appearance" desc="Display choices saved on this device only.">
    <Row label="Density" hint="How tightly cards and rows are packed.">
      <div class="seg">
        {#each DENSITIES as d}
          <button class="segb" class:on={density === d.v} onclick={() => (density = d.v)} title={d.hint}>{d.label}</button>
        {/each}
      </div>
    </Row>
    <Row label="Reduce motion" hint="Turn off animations and live tickers.">
      <Toggle bind:checked={reduceMotion} />
    </Row>
  </Section>

  <Section title="Defaults" desc="Where the app takes you.">
    <Row label="Open after login" hint="The first page you land on each session.">
      <div class="seg">
        {#each LANDINGS as l}
          <button class="segb" class:on={landing === l.v} onclick={() => (landing = l.v)}>{l.label}</button>
        {/each}
      </div>
    </Row>
  </Section>

  <p class="text-[12px]" style="color:var(--muted)">Preferences apply instantly and are stored in this browser. They don't change anyone else's experience.</p>
</div>

<style>
  .seg { display: inline-flex; background: var(--sand); border: 1px solid var(--border); border-radius: 9px; padding: 2px; }
  .segb { font-size: 13px; padding: 6px 14px; border-radius: 7px; color: var(--muted); background: transparent; border: none; cursor: pointer; transition: background .14s, color .14s; }
  .segb.on { background: #fff; color: #1a1a18; font-weight: 600; box-shadow: none; }
</style>
