/**
 * Short-lived handoff so navigating away from /agent still opens the
 * companion chat (and optionally restores the same thread) — keeps Undo
 * reachable after clicking a Created link.
 */

export const OPEN_AI_CHAT_EVENT = 'integral:open-ai-chat';
const HANDOFF_KEY = 'integral:ai-chat-handoff';
const LAST_THREAD_KEY = 'integral:ai-chat-last-thread';
const HANDOFF_TTL_MS = 60_000;

export type ChatHandoff = {
  threadId?: string | null;
  ts: number;
};

type LastActiveThread = { threadId: string; workspaceId: string | null };

/**
 * Persist the active chat thread so Created-link handoff can restore it.
 *
 * Keyed by workspace: a thread remembered in one workspace must not be
 * re-selected after switching to another — the runtime's mount-time restore
 * used to do exactly that and reopened the previous workspace's conversation.
 * `workspaceId` is optional for callers that do not know it (the dock's
 * handoff listener); an unknown workspace matches any reader.
 */
export function rememberActiveChatThreadId(
  threadId: string | null,
  workspaceId?: string | null,
): void {
  try {
    if (!threadId || threadId.startsWith('local-')) {
      sessionStorage.removeItem(LAST_THREAD_KEY);
      return;
    }
    const payload: LastActiveThread = { threadId, workspaceId: workspaceId ?? null };
    sessionStorage.setItem(LAST_THREAD_KEY, JSON.stringify(payload));
  } catch {
    /* ignore */
  }
}

/**
 * Read the remembered thread. When `workspaceId` is given, a thread recorded
 * under a *different* workspace is ignored; one recorded with no workspace
 * (or read with none) is returned as before.
 */
export function peekLastActiveChatThreadId(workspaceId?: string | null): string | null {
  try {
    const raw = sessionStorage.getItem(LAST_THREAD_KEY);
    if (!raw) return null;
    let parsed: LastActiveThread;
    try {
      const candidate = JSON.parse(raw) as unknown;
      if (!candidate || typeof candidate !== 'object') {
        // Pre-keyed slot held a bare id string.
        parsed = { threadId: raw, workspaceId: null };
      } else {
        parsed = candidate as LastActiveThread;
      }
    } catch {
      parsed = { threadId: raw, workspaceId: null };
    }
    if (!parsed.threadId) return null;
    if (
      workspaceId &&
      parsed.workspaceId &&
      parsed.workspaceId !== workspaceId
    ) {
      return null;
    }
    return parsed.threadId;
  } catch {
    return null;
  }
}

export function requestOpenCompanionChat(opts?: {
  threadId?: string | null;
}): void {
  const payload: ChatHandoff = {
    threadId: opts?.threadId ?? null,
    ts: Date.now(),
  };
  try {
    sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(payload));
  } catch {
    /* private mode / quota — event alone still helps if the dock is mounted */
  }
  window.dispatchEvent(
    new CustomEvent(OPEN_AI_CHAT_EVENT, { detail: payload }),
  );
}

/** Read + clear a still-fresh handoff (or null if missing/expired). */
export function consumeChatHandoff(): ChatHandoff | null {
  try {
    const raw = sessionStorage.getItem(HANDOFF_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(HANDOFF_KEY);
    const parsed = JSON.parse(raw) as ChatHandoff;
    if (!parsed?.ts || Date.now() - parsed.ts > HANDOFF_TTL_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** Peek without clearing (dock open vs runtime thread switch). */
export function peekChatHandoff(): ChatHandoff | null {
  try {
    const raw = sessionStorage.getItem(HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ChatHandoff;
    if (!parsed?.ts || Date.now() - parsed.ts > HANDOFF_TTL_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}
