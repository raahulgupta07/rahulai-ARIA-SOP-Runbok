// Shared dashboard helpers + constants (used across the /dashboard sub-route pages).
import { goto } from '$app/navigation';

export const RANGES = [
  { d: 7, label: '7d' },
  { d: 30, label: '30d' },
  { d: 90, label: '90d' }
];

export const INTENT_LABEL: Record<string, string> = {
  how_to: 'How-to / setup', troubleshoot: 'Troubleshoot', delete_disable: 'Delete / disable',
  where_find: 'Where / find', status_check: 'Status / check', other: 'Other'
};

export const STATUS_DOT: Record<string, string> = {
  active: '#3f8f5f', dormant: '#e6b15c', inactive: '#c0492f', never: '#b7b1a4'
};

export function dmax(a: any[], key = 'n') {
  return Math.max(1, ...(a || []).map((x) => x[key] || 0));
}

export function band(score: number) {
  if (score >= 80) return { c: '#3f8f5f', t: 'Strong' };
  if (score >= 60) return { c: '#c2683f', t: 'Fair' };
  return { c: '#c0492f', t: 'Weak' };
}

export function ago(ts: string) {
  if (!ts) return '';
  const d = new Date(ts).getTime();
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

export function pickNode(n: any) {
  if (!n) return;
  if (n.doc_id) goto('/brain?doc=' + n.doc_id);
  else if (n.fact_id) goto('/brain');
}
