// Shared chat state so the global left rail (layout) and the chat page agree on
// the conversation list + which one is active. The rail OWNS the list UI; the
// chat page owns the thread. They talk through these stores.
import { writable } from 'svelte/store';
import { api } from './api';

export type Conv = { id: number; title: string; updated_at: string };

export const convs = writable<Conv[]>([]);
export const activeConvId = writable<number | null>(null);
export const newChatSignal = writable(0);   // bumped → chat page clears to a fresh thread

export async function reloadConvs() {
  try { convs.set((await api.conversations()).conversations || []); } catch {}
}
export function openConvId(id: number) { activeConvId.set(id); }
export function triggerNewChat() { activeConvId.set(null); newChatSignal.update((n) => n + 1); }
