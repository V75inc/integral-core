// One browser-owned call. The transport remains in its mounted conversation;
// this registry pins its owner and coordinates deliberate departures.
import { useSyncExternalStore } from "react";

export type VoiceSession = {
  owner: symbol;
  surface: "dock" | "home";
  workspaceId: string;
  threadId: string;
  close: (refresh?: boolean) => void;
};

let active: VoiceSession | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach(listener => listener());
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (!active) return;
  event.preventDefault();
  event.returnValue = "";
};
const pageHide = () => active?.close(false);
export const peekVoiceSession = () => active;
export function claimVoiceSession(session: VoiceSession) {
  if (active) return false;
  active = session;
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("pagehide", pageHide);
  notify();
  return true;
}
export function updateVoiceSessionThread(owner: symbol, threadId: string) {
  if (active?.owner !== owner || active.threadId === threadId) return;
  active = { ...active, threadId };
  notify();
}
export function releaseVoiceSession(owner: symbol) {
  if (active?.owner !== owner) return;
  active = null;
  window.removeEventListener("beforeunload", beforeUnload);
  window.removeEventListener("pagehide", pageHide);
  notify();
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
export const useVoiceSession = () => useSyncExternalStore(subscribe, peekVoiceSession, () => null);
