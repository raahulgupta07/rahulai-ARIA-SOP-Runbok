<script lang="ts">
  // Accessible on/off switch. Two-way bindable: <Toggle bind:checked />
  let { checked = $bindable(false), disabled = false }: {
    checked?: boolean; disabled?: boolean;
  } = $props();
</script>

<button
  type="button"
  role="switch"
  aria-checked={checked}
  class="tgl" class:on={checked} {disabled}
  onclick={() => (checked = !checked)}
>
  <span class="knob"></span>
</button>

<style>
  .tgl {
    width: 40px; height: 23px; border-radius: 99px; background: #d8d3c8;
    border: none; padding: 2px; cursor: pointer; flex: none;
    transition: background 0.18s; position: relative;
  }
  .tgl.on { background: #1a1a18; }
  .tgl:disabled { opacity: 0.5; cursor: not-allowed; }
  .knob {
    display: block; width: 19px; height: 19px; border-radius: 50%; background: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.25); transition: transform 0.18s;
  }
  .tgl.on .knob { transform: translateX(17px); }
</style>
