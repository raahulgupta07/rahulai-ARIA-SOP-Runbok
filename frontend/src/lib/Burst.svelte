<script lang="ts">
  // `active` = agent is working → livelier spin + glow. Idle = gentle breathe.
  let { size = 28, class: cls = '', active = false }: { size?: number; class?: string; active?: boolean } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 40 40" class="burst {cls} {active ? 'on' : ''}" style="color:var(--clay)">
  <path class="b-main" fill="currentColor" d="M20 1.5c.6 5.4 1.4 8.3 3.3 10.2 1.9 1.9 4.8 2.7 10.2 3.3-5.4.6-8.3 1.4-10.2 3.3-1.9 1.9-2.7 4.8-3.3 10.2-.6-5.4-1.4-8.3-3.3-10.2-1.9-1.9-4.8-2.7-10.2-3.3 5.4-.6 8.3-1.4 10.2-3.3C18.6 9.8 19.4 6.9 20 1.5Z"/>
  <path class="b-spark" fill="currentColor" d="M31.5 23c.3 2.7.7 4.1 1.6 5.1.95.95 2.4 1.35 5.1 1.6-2.7.3-4.1.7-5.1 1.6-.9 1-1.3 2.4-1.6 5.1-.3-2.7-.7-4.1-1.6-5.1-1-.9-2.4-1.3-5.1-1.6 2.7-.3 4.1-.7 5.1-1.6.9-1 1.3-2.4 1.6-5.1Z"/>
</svg>

<style>
  .burst { transform-origin: 50% 50%; animation: breathe 3.4s ease-in-out infinite; }
  .b-main { transform-origin: 50% 50%; }
  .b-spark { transform-origin: 80% 73%; animation: twinkle 2.6s ease-in-out infinite; }
  @keyframes breathe {
    0%, 100% { transform: scale(1) rotate(0deg); opacity: .92; }
    50%      { transform: scale(1.06) rotate(4deg); opacity: 1; }
  }
  @keyframes twinkle {
    0%, 100% { transform: scale(.6); opacity: .35; }
    50%      { transform: scale(1); opacity: 1; }
  }
  /* working state: faster spin, pulse and a soft coral glow */
  .burst.on { animation: spinpulse 1.6s ease-in-out infinite; filter: drop-shadow(0 0 5px color-mix(in srgb, var(--clay) 60%, transparent)); }
  .burst.on .b-spark { animation: twinkle 0.9s ease-in-out infinite; }
  @keyframes spinpulse {
    0%   { transform: scale(.92) rotate(0deg); }
    50%  { transform: scale(1.12) rotate(180deg); }
    100% { transform: scale(.92) rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    .burst, .burst.on, .b-spark { animation: none; filter: none; }
  }
</style>
