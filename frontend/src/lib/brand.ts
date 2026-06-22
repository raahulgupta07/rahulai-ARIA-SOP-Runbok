import { writable } from 'svelte/store';

// Runtime white-label brand config, served PUBLIC by GET /api/brand.
// The endpoint never raises (returns defaults), so a fetch failure just leaves
// the store null and the UI falls back to the baked-in defaults.
export type Brand = {
  name: string;
  short_name: string;
  tagline: string;
  footer: string;
  assistant_label: string;
  accent: string;
  accent_dk: string;
  logo_url: string;
  mark_url: string;
  favicon_url: string;
  icon192_url: string;
  icon512_url: string;
  custom?: Record<string, any>;
};

export const brand = writable<Brand | null>(null);

export async function loadBrand(): Promise<void> {
  try {
    const r = await fetch('/api/brand');
    if (r.ok) brand.set(await r.json());
  } catch {
    /* keep null → defaults */
  }
}
