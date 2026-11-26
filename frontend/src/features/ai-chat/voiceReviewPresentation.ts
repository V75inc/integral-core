import { useSyncExternalStore } from "react";

// Presentation only. All decisions still go through the authorized server.
type CallReview = { owner: symbol; threadId: string; proposal?: string; requiresDetails?: boolean };
let current: CallReview | null = null;
const listeners = new Set<() => void>();

export function presentCallReview(owner: symbol, threadId: string, proposal?: string, requiresDetails = false) {
  current = { owner, threadId, proposal, requiresDetails };
  listeners.forEach(notify => notify());
}

export function clearCallReview(owner: symbol) {
  if (current?.owner !== owner) return;
  current = null;
  listeners.forEach(notify => notify());
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
}

export function useCallReview() {
  return useSyncExternalStore(subscribe, () => current, () => null);
}
