import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useThreadRuntime } from '@assistant-ui/react';

import {
  blessStagingToken,
  cancelPromptQueueAll,
  getPromptQueue,
  markPromptWrite,
  resolvePromptQuestion,
  revokeStagingToken,
  type StagingAutonomy,
} from '../../../api/agentive';
import { useChatActivity } from '../AIChatSurface';
import type { PromptItem, PromptQueue } from './types';

function resumeIfNeeded(
  threadRuntime: ReturnType<typeof useThreadRuntime> | null,
  resumeText: string | null | undefined,
) {
  if (!resumeText || !threadRuntime) return;
  try {
    threadRuntime.append({
      role: 'user',
      content: [{ type: 'text', text: resumeText }],
    });
  } catch {
    /* non-fatal */
  }
}

export function usePromptQueue() {
  const { activeThreadId } = useChatActivity();
  const threadRuntime = useThreadRuntime();
  const [queue, setQueue] = useState<PromptQueue | null>(null);
  const [open, setOpen] = useState(false);
  // Read inside the poll interval without making it a dependency (which would
  // tear down and rebuild the listeners on every open/close).
  const openRef = useRef(false);
  openRef.current = open;
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!activeThreadId) {
      setQueue(null);
      setOpen(false);
      return;
    }
    const res = await getPromptQueue(activeThreadId);
    if (!res?.open) {
      setQueue(null);
      setOpen(false);
      return;
    }
    const q = res.queue as unknown as PromptQueue;
    setQueue(q);
    setOpen(true);
    setIndex((i) => {
      const items = q.items || [];
      const pendingIdx = items.findIndex((it) => it.status === 'pending');
      // Only move the user. This ran on every 2s poll and snapped to the first
      // pending item unconditionally, so paging forward to review prompt 3
      // while prompt 1 was still open bounced them back to 1 within two
      // seconds. Stay put while the item under the cursor is still actionable.
      const currentItem = items[i];
      if (currentItem && currentItem.status === 'pending') return i;
      if (pendingIdx >= 0) return pendingIdx;
      return Math.min(i, Math.max(0, (items.length || 1) - 1));
    });
  }, [activeThreadId]);

  useEffect(() => {
    void refresh();
    const onStaging = () => {
      void refresh();
    };
    // Local, same-tab signal emitted by this hook's own actions below.
    window.addEventListener('staging-state-changed', onStaging);
    // Server-driven signals arrive from the agent WebSocket under the
    // `integral:` namespace (see hooks/useAgentiveWebSocket.ts). Listening only
    // for the un-namespaced name meant NO server event ever reached the sheet:
    // a queue opened by a streaming turn appeared only on the 2s poll below,
    // and `composerLocked` (= sheet.open) lagged with it, leaving a window
    // where the user could start a second turn against an open sheet.
    window.addEventListener('integral:staging-state-changed', onStaging);
    window.addEventListener('integral:staging-created', onStaging);
    // Poll lightly while open so tool results from a streaming turn appear.
    // Back off when there is no sheet: the fast cadence only earns its keep
    // while the user is looking at one, but some polling has to continue so a
    // queue opened without a WS event is still noticed.
    const t = window.setInterval(
      () => {
        void refresh();
      },
      openRef.current ? 2000 : 10000,
    );
    return () => {
      window.removeEventListener('staging-state-changed', onStaging);
      window.removeEventListener('integral:staging-state-changed', onStaging);
      window.removeEventListener('integral:staging-created', onStaging);
      window.clearInterval(t);
    };
  }, [refresh]);

  const items = queue?.items ?? [];
  const current: PromptItem | null = items[index] ?? null;

  const applyQueueResult = useCallback(
    (res: {
      queue?: PromptQueue;
      resume_text?: string | null;
      closed?: boolean;
    }) => {
      if (res.closed) {
        setOpen(false);
        setQueue(null);
        resumeIfNeeded(threadRuntime, res.resume_text);
        return;
      }
      if (res.queue) {
        setQueue(res.queue);
        setOpen(res.queue.status === 'open');
        const pendingIdx = res.queue.items.findIndex((it) => it.status === 'pending');
        if (pendingIdx >= 0) setIndex(pendingIdx);
      }
    },
    [threadRuntime],
  );

  const answerQuestion = useCallback(
    async (choices: string[], freeText?: string) => {
      if (!activeThreadId || !current || current.kind !== 'question' || busy) return;
      setBusy(true);
      setError(null);
      try {
        const res = await resolvePromptQuestion({
          threadId: activeThreadId,
          itemId: current.id,
          choices,
          freeText,
        });
        applyQueueResult(res as never);
      } catch {
        setError('Could not record that answer.');
      } finally {
        setBusy(false);
      }
    },
    [activeThreadId, applyQueueResult, busy, current],
  );

  const skipQuestion = useCallback(async () => {
    if (!activeThreadId || !current || current.kind !== 'question' || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await resolvePromptQuestion({
        threadId: activeThreadId,
        itemId: current.id,
        skip: true,
      });
      applyQueueResult(res as never);
    } catch {
      setError('Could not skip.');
    } finally {
      setBusy(false);
    }
  }, [activeThreadId, applyQueueResult, busy, current]);

  const approveWrite = useCallback(
    async (autonomy: StagingAutonomy = 'single') => {
      if (!activeThreadId || !current || current.kind !== 'staged_write' || busy)
        return;
      setBusy(true);
      setError(null);
      try {
        await blessStagingToken(current.token, autonomy);
        const res = await markPromptWrite({
          threadId: activeThreadId,
          token: current.token,
          status: 'approved',
        });
        applyQueueResult(res as never);
        window.dispatchEvent(new Event('staging-state-changed'));
      } catch {
        setError('Could not approve that write.');
      } finally {
        setBusy(false);
      }
    },
    [activeThreadId, applyQueueResult, busy, current],
  );

  const rejectWrite = useCallback(async () => {
    if (!activeThreadId || !current || current.kind !== 'staged_write' || busy)
      return;
    setBusy(true);
    setError(null);
    try {
      await revokeStagingToken(current.token);
      const res = await markPromptWrite({
        threadId: activeThreadId,
        token: current.token,
        status: 'rejected',
      });
      applyQueueResult(res as never);
      window.dispatchEvent(new Event('staging-state-changed'));
    } catch {
      setError('Could not reject that write.');
    } finally {
      setBusy(false);
    }
  }, [activeThreadId, applyQueueResult, busy, current]);

  const cancelAll = useCallback(async () => {
    if (!activeThreadId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await cancelPromptQueueAll(activeThreadId);
      applyQueueResult(res as never);
      window.dispatchEvent(new Event('staging-state-changed'));
    } catch {
      setError('Could not cancel.');
    } finally {
      setBusy(false);
    }
  }, [activeThreadId, applyQueueResult, busy]);

  const page = useMemo(
    () => ({
      index,
      total: items.length,
      canPrev: index > 0,
      canNext: index < items.length - 1,
      prev: () => setIndex((i) => Math.max(0, i - 1)),
      next: () => setIndex((i) => Math.min(items.length - 1, i + 1)),
    }),
    [index, items.length],
  );

  return {
    open,
    queue,
    current,
    items,
    page,
    busy,
    error,
    refresh,
    answerQuestion,
    skipQuestion,
    approveWrite,
    rejectWrite,
    cancelAll,
  };
}
