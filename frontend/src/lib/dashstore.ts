// Shared dashboard state across the /dashboard sub-routes (the time-range filter).
import { writable, get } from 'svelte/store';
import { api } from './api';

const KEY = 'aria_dash_range';
function initRange(): number {
  if (typeof localStorage !== 'undefined') {
    const v = parseInt(localStorage.getItem(KEY) || '30', 10);
    if ([7, 30, 90].includes(v)) return v;
  }
  return 30;
}

export const range = writable<number>(initRange());

// shared mobile-nav toggle: the global header hamburger sets it true; whichever
// page is mounted (Workspace / Brain / Settings / Chat) opens its rail overlay
// when it's true, and clears it on nav / scrim close. One header burger for all.
export const mobileNav = writable<boolean>(false);
range.subscribe((v) => {
  if (typeof localStorage !== 'undefined') localStorage.setItem(KEY, String(v));
});

// ---- Mission Control tabs ----
// Section tabs live in the dashboard layout (frozen) and drive which section the
// page renders. No scrolling between sections — one tab visible at a time.
export type McTab = { id: string; label: string; roles: ('admin' | 'user')[] };
export const MC_TABS: McTab[] = [
  { id: 'overview',  label: 'Overview',    roles: ['admin', 'user'] },
  { id: 'live',      label: 'Cockpit',     roles: ['admin'] },
  { id: 'exec',      label: 'Exec',        roles: ['admin'] },
  { id: 'users',     label: 'Users',       roles: ['admin'] },
  { id: 'perf',      label: 'Performance', roles: ['admin'] },
  { id: 'accuracy',  label: 'Accuracy',    roles: ['admin'] },
  { id: 'selfheal',  label: 'Self-Heal',   roles: ['admin'] },
  { id: 'knowledge', label: 'Knowledge',   roles: ['admin'] },
  { id: 'review',    label: 'Review',      roles: ['admin'] },
  { id: 'system',    label: 'System',      roles: ['admin'] }
];
const TKEY = 'aria_mc_tab';
function initTab(): string {
  if (typeof localStorage !== 'undefined') {
    const v = localStorage.getItem(TKEY);
    if (v && MC_TABS.some((t) => t.id === v)) return v;
  }
  return 'overview';
}
export const mcTab = writable<string>(initTab());
mcTab.subscribe((v) => {
  if (typeof localStorage !== 'undefined') localStorage.setItem(TKEY, v);
});

// ---- Unified Workspace (Dashboard + Brain in one persistent-rail shell) ----
// kind 'insight' → renders a dashboard section component.
// kind 'knowledge' → renders the embedded Brain hub with a given view.
export type WsItem = {
  id: string; label: string; group: 'Overview' | 'Brain' | 'Insights';
  kind: 'insight' | 'knowledge'; roles: ('admin' | 'user')[];
  badge?: 'health';
  view?: { tab: 'brain' | 'graph' | 'audit'; feed?: 'all' | 'doc' | 'fact' };
};
// One grouped rail: Overview (top) · BRAIN (the Brain tabs as rail rows) · INSIGHTS (dashboard).
export const WS_ITEMS: WsItem[] = [
  { id: 'overview',     label: 'Overview',  group: 'Overview', kind: 'insight',   roles: ['admin'] },
  { id: 'live',         label: 'Cockpit',   group: 'Insights', kind: 'insight', roles: ['admin'] },

  { id: 'brain-docs',   label: 'Documents', group: 'Brain', kind: 'knowledge', roles: ['admin', 'user'], view: { tab: 'brain', feed: 'doc' } },
  { id: 'brain-facts',  label: 'Facts',     group: 'Brain', kind: 'knowledge', roles: ['admin', 'user'], view: { tab: 'brain', feed: 'fact' } },
  { id: 'brain-graph',  label: 'Graph',     group: 'Brain', kind: 'knowledge', roles: ['admin', 'user'], view: { tab: 'graph' } },
  { id: 'brain-health', label: 'Health',    group: 'Brain', kind: 'knowledge', roles: ['admin', 'user'], view: { tab: 'audit' }, badge: 'health' },
  { id: 'dream',        label: 'Self-improvement', group: 'Brain', kind: 'insight', roles: ['admin'] },

  { id: 'exec',      label: 'Exec',        group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'users',     label: 'Users',       group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'perf',      label: 'Performance', group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'knowledge', label: 'Knowledge',   group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'learning',  label: 'Learning',    group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'review',    label: 'Review',      group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'accuracy',  label: 'Accuracy',    group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'selfheal',  label: 'Self-Heal',   group: 'Insights', kind: 'insight', roles: ['admin'] },
  { id: 'system',    label: 'System',      group: 'Insights', kind: 'insight', roles: ['admin'] }
];
const WKEY = 'aria_ws_item';
function initWs(): string {
  if (typeof localStorage !== 'undefined') {
    const v = localStorage.getItem(WKEY);
    if (v && WS_ITEMS.some((t) => t.id === v)) return v;
  }
  return 'brain-docs';   // land on Documents by default
}
// Click bridges: Upload / Teach buttons live in the Workspace top-bar, but the
// actions live inside the embedded Brain. Bumping these signals → Brain reacts.
export const brainUploadSignal = writable(0);
export const brainTeachSignal = writable(0);
// OpenWebUI-style menu (no modal): the Workspace owns the file pickers so the
// browser file dialog fires from a real click; picked files / scan trigger
// bridge into the embedded Brain which does the actual upload + list reload.
export const brainFilesSignal = writable<File[] | null>(null);
export const brainScanSignal = writable(0);
export const brainS3Signal = writable(0);   // bulk import from S3 bucket

export const wsItem = writable<string>(initWs());
wsItem.subscribe((v) => { if (typeof localStorage !== 'undefined') localStorage.setItem(WKEY, v); });

// ---- Shared Brain data bundle (loaded once at the Workspace shell) ----
// The embedded Brain used to fill its own `docs`/`facts`/… via an async load()
// that races first paint and never retriggers on tab switch (single-mounted, no
// {#key}) → landing on Documents showed "No documents yet" until you toggled to
// Facts and back. Fix: ONE shared store, hydrated from localStorage synchronously
// (instant paint) + stale-while-revalidate refetch.
export type BrainData = {
  docs: any[]; facts: any[]; pending: number;
  qaPairs: any[]; qaCounts: { active: number; pending: number; rejected: number };
  stats: any; loaded: boolean; fetchedAt: number;
};
const BKEY = 'aria_brain_cache';
function emptyBrain(): BrainData {
  return { docs: [], facts: [], pending: 0, qaPairs: [], qaCounts: { active: 0, pending: 0, rejected: 0 }, stats: undefined, loaded: false, fetchedAt: 0 };
}
function initBrain(): BrainData {
  if (typeof localStorage !== 'undefined') {
    try {
      const v = JSON.parse(localStorage.getItem(BKEY) || '');
      if (v && typeof v === 'object') {
        const e = emptyBrain();
        return {
          docs: v.docs || e.docs,
          facts: v.facts || e.facts,
          pending: v.pending || e.pending,
          qaPairs: v.qaPairs || e.qaPairs,
          qaCounts: v.qaCounts || e.qaCounts,
          stats: v.stats,
          loaded: !!v.loaded,
          fetchedAt: v.fetchedAt || 0
        };
      }
    } catch {}
  }
  return emptyBrain();
}
export const brainData = writable<BrainData>(initBrain());

let _brainInflight: Promise<void> | null = null;
const BRAIN_TTL_MS = 60000;   // stale-while-revalidate window
export function loadBrainData(force = false): Promise<void> {
  if (_brainInflight) return _brainInflight;            // dedup concurrent callers
  const cur = get(brainData);
  if (!force && cur.loaded && Date.now() - cur.fetchedAt < BRAIN_TTL_MS) {
    return Promise.resolve();                           // fresh enough → skip fetch
  }
  _brainInflight = (async () => {
    try {
      const [docsR, mem, usageR, qaR] = await Promise.all([
        api.documents(),
        api.memory(),
        api.usage().catch(() => ({ stats: undefined })),
        api.qa().catch(() => ({ qa: [], counts: { active: 0, pending: 0, rejected: 0 } }))
      ]);
      const prev = get(brainData);
      const next: BrainData = {
        docs: docsR.docs || [],
        facts: mem.memory || [],
        pending: mem.pending || 0,
        qaPairs: qaR.qa || [],
        qaCounts: qaR.counts || { active: 0, pending: 0, rejected: 0 },
        stats: usageR.stats ?? prev.stats,              // preserve prior stats if usage failed
        loaded: true,
        fetchedAt: Date.now()
      };
      brainData.set(next);
      try { if (typeof localStorage !== 'undefined') localStorage.setItem(BKEY, JSON.stringify(next)); } catch {}
    } finally {
      _brainInflight = null;
    }
  })();
  return _brainInflight;
}

// Cross-links inside dashboard sections used to goto('/dashboard/..') / '/brain',
// which jumps OUT of the unified Workspace. When inside /workspace, switch the
// rail item instead (stay on the same screen); otherwise fall back to navigation.
const WS_HREF: Record<string, string> = {
  '/dashboard': 'overview', '/dashboard/users': 'users', '/dashboard/exec': 'exec',
  '/dashboard/perf': 'overview', '/dashboard/performance': 'overview',
  '/dashboard/knowledge': 'brain-health', '/dashboard/graph': 'brain-graph',
  '/dashboard/review': 'review', '/dashboard/system': 'system',
  '/dashboard/learning': 'learning', '/brain': 'brain-docs'
};
export async function navInsight(href: string) {
  if (typeof location !== 'undefined' && location.pathname.startsWith('/workspace') && href) {
    const base = href.split('#')[0].split('?')[0];
    let id = WS_HREF[base];
    if (!id && base.startsWith('/dashboard/')) id = base.split('/').pop() || '';
    if (id && WS_ITEMS.some((t) => t.id === id)) {
      wsItem.set(id);
      try { history.replaceState(null, '', '/workspace?v=' + id); } catch {}
      return;
    }
  }
  const { goto } = await import('$app/navigation');
  goto(href);
}

// ---- real-time heartbeat ----
// `tick` auto-increments on an interval; dashboard pages reference $tick inside
// their data-loading $effect so they silently re-fetch and the ECharts tween to
// the new values. Visibility-aware: no polling while the tab is hidden, and an
// immediate refresh the moment it becomes visible again.
export const LIVE_MS = 15000;
export const tick = writable(0);

if (typeof window !== 'undefined') {
  let t = 0;
  const bump = () => tick.set(++t);
  // safety/fallback poll — SSE (below) gives near-instant updates; this catches
  // anything SSE misses and covers the case where the stream can't connect.
  setInterval(() => {
    if (document.visibilityState === 'visible') bump();
  }, LIVE_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') bump();
  });
}

// ---- real-time push (SSE) ----
// One long-lived stream that bumps `tick` the moment server-side activity lands,
// so dashboards refresh on real events (~3s) instead of waiting for the poll.
// Auth via the member token; auto-reconnects with backoff; started lazily from
// the dashboard layout so it only runs for admins viewing dashboards.
let _liveStarted = false;
export function startLive() {
  if (_liveStarted || typeof window === 'undefined') return;
  _liveStarted = true;
  const base = location.origin + '/api';
  (async function loop() {
    for (;;) {
      try {
        const token = localStorage.getItem('docsensei_token') || '';
        const r = await fetch(`${base}/stream/dashboard`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {}
        });
        if (!r.ok || !r.body) throw new Error('no stream');
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i: number;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, i);
            buf = buf.slice(i + 2);
            if (frame.startsWith('data:')) tick.update((n) => n + 1);
          }
        }
      } catch {
        /* drop through to reconnect */
      }
      await new Promise((res) => setTimeout(res, 5000));
    }
  })();
}
