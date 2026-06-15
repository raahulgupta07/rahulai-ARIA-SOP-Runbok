<script lang="ts">
  // Small bar chart: mode='bars' (single series {label,n}) or 'trend' ({label,up,down}).
  import { dmax } from '$lib/dashutil';
  let { data = [], mode = 'bars' } = $props<{ data?: any[]; mode?: 'bars' | 'trend' }>();
  const tmax = $derived(Math.max(1, ...(data || []).map((x: any) => Math.max(x.up || 0, x.down || 0))));
</script>

{#if mode === 'trend'}
  <div class="bars trend">
    {#each data || [] as d}
      <span class="tcol" title="{d.label}: {d.up} up / {d.down} down">
        <span class="tup" style="height:{(d.up / tmax) * 100}%"></span>
        <span class="tdn" style="height:{(d.down / tmax) * 100}%"></span>
      </span>
    {/each}
  </div>
{:else}
  <div class="bars">
    {#each data || [] as d}
      <span class="bar" title="{d.label}: {d.n}" style="height:{Math.max(3, (d.n / dmax(data)) * 100)}%"></span>
    {/each}
  </div>
{/if}
