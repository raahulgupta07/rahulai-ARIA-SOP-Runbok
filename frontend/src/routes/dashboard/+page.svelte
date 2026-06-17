<script lang="ts">
  import { auth, type User } from '$lib/auth';
  import { mcTab } from '$lib/dashstore';
  import { onMount } from 'svelte';
  import Overview from '$lib/dashboard/sections/Overview.svelte';
  import Exec from '$lib/dashboard/sections/Exec.svelte';
  import Users from '$lib/dashboard/sections/Users.svelte';
  import Perf from '$lib/dashboard/sections/Perf.svelte';
  import Knowledge from '$lib/dashboard/sections/Knowledge.svelte';
  import Review from '$lib/dashboard/sections/Review.svelte';
  import System from '$lib/dashboard/sections/System.svelte';
  import Cockpit from '$lib/dash/Cockpit.svelte';

  let me = $state<User | null>(auth.cachedUser());
  let isAdmin = $derived(me?.role === 'admin');
  $effect(() => { if (!me) auth.me().then((u) => (me = u)).catch(() => {}); });

  const COMP: Record<string, any> = {
    overview: Overview, live: Cockpit, exec: Exec, users: Users, perf: Perf,
    knowledge: Knowledge, review: Review, system: System
  };
  // non-admins only ever see Overview (their personal view); force the tab.
  $effect(() => { if (me && !isAdmin && $mcTab !== 'overview') mcTab.set('overview'); });

  // old deep-links (/dashboard#exec) → select that tab
  onMount(() => {
    const h = location.hash.replace('#', '');
    if (h && COMP[h]) mcTab.set(h);
  });

  const Active = $derived(COMP[$mcTab] ?? Overview);
</script>

<!-- one tab visible at a time — no scrolling between sections. -->
{#key $mcTab}
  {@const Comp = Active}
  <div class="mcbody"><Comp /></div>
{/key}
