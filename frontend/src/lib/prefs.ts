// User preferences — UI-only, persisted to localStorage, applied to <html>.
// No backend; these are per-device display choices. (No dark mode by product rule.)
import { writable } from 'svelte/store';

export type Density = 'comfortable' | 'compact';
export type Landing = '/' | '/dashboard' | '/brain';

export interface Prefs {
  density: Density;
  reduceMotion: boolean;   // force reduced motion regardless of OS setting
  landing: Landing;        // page to open right after login
}

const KEY = 'aria_prefs';
const DEFAULTS: Prefs = { density: 'comfortable', reduceMotion: false, landing: '/' };

function load(): Prefs {
  if (typeof localStorage === 'undefined') return { ...DEFAULTS };
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; }
  catch { return { ...DEFAULTS }; }
}

export function applyPrefs(p: Prefs) {
  if (typeof document === 'undefined') return;
  const el = document.documentElement;
  el.setAttribute('data-density', p.density);
  el.toggleAttribute('data-reduce-motion', !!p.reduceMotion);
}

export const prefs = writable<Prefs>(load());
prefs.subscribe((p) => {
  if (typeof localStorage !== 'undefined') localStorage.setItem(KEY, JSON.stringify(p));
  applyPrefs(p);
});
