import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useThreadRuntime } from '@assistant-ui/react';

import {
  blessStagingToken,
  cancelPromptQueueAll,
  getPromptQueue,
  getStagingTokenState,
  markPromptWrite,
  resolvePromptQuestion,
  revokeStagingToken,
} from '../../../api/agentive';
import { useConfirm } from '../../../context/ConfirmContext';
import { useChatActivity } from '../AIChatSurface';
import type { PromptItem, PromptQueue } from './types';

export function resumeIfNeeded(
  threadRuntime: ReturnType<typeof useThreadRuntime> | null,
  resumeText: string | null | undefined,
  appendAssistantNote: (text: string) => void,
) {
  if (!resumeText || !threadRuntime) return;
  try {
    // Keep the resolved review visible as an assistant note. The runtime
    // continuation starts separately and never appends a user utterance.
    appendAssistantNote(resumeText);
    const messages = threadRuntime.getState().messages;
    const parentId = messages[messages.length - 1]?.id ?? null;
    threadRuntime.startRun({
      parentId,
      sourceId: null,
      runConfig: { custom: { hostAction: 'prompt_sheet_resume' } },
    });
  } catch {
    /* non-fatal */
  }
}

export function usePromptQueue() {
  const { activeThreadId, appendAssistantNote, isThreadStreaming } =
    useChatActivity();
  const threadRuntime = useThreadRuntime();
  const confirm = useConfirm();
  const [queue, setQueue] = useState<PromptQueue | null>(null);
  const [open, setOpen] = useState(false);
  // Read inside the poll interval without making it a dependency (which would
  // tear down and rebuild the listeners on every open/close).
  const openRef = useRef(false);
  openRef.current = open;
  const [index, setIndex] = useState(0);
  const resumedRefreshes = useRef(new Set<string>());
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
      const resumeKey = `${activeThreadId}:${res?.resume_text ?? ''}`;
      if (res?.resume_text && !resumedRefreshes.current.has(resumeKey)) {
        resumedRefreshes.current.add(resumeKey);
        // A clear chat approval is applied before its ordinary native turn
        // continues. The continuation belongs to that run; do not launch a
        // second Prompt Sheet resume while its stream is active.
        if (!isThreadStreaming(activeThreadId)) {
          resumeIfNeeded(threadRuntime, res.resume_text, appendAssistantNote);
        }
      }
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
  }, [activeThreadId, appendAssistantNote, isThreadStreaming, threadRuntime]);

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
        resumeIfNeeded(threadRuntime, res.resume_text, appendAssistantNote);
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
    async () => {
      if (!activeThreadId || !current || current.kind !== 'staged_write' || busy)
        return;
      setBusy(true);
      setError(null);
      try {
        const staged = await getStagingTokenState(current.token);
        if (!staged) {
          throw new Error(
            'Could not verify the impact of this change. Refresh and try again.',
          );
        }
        const requiresStrongConfirmation =
          staged.requires_strong_confirmation;
        const strongConfirmation = requiresStrongConfirmation
          ? await confirm({
              title: 'Confirm high-impact change',
              message:
                'Review the affected records, access, or structure, then confirm this exact action: ' +
                (current.summary || 'Staged change'),
              confirmLabel: 'Confirm change',
              variant: 'danger',
            })
          : false;
        if (requiresStrongConfirmation && !strongConfirmation) return;
        const blessRes = await blessStagingToken(current.token, {
          strongConfirmation,
        });
        if (!blessRes.ok) {
          throw new Error(blessRes.message || 'Approval failed.');
        }
        const exec = blessRes.execute_result as
          | (Record<string, unknown> & {
              error?: unknown;
              filed?: boolean;
              skipped?: boolean;
              message?: unknown;
            })
          | undefined;
        const execFailed =
          !!exec &&
          (!!exec.error || exec.filed === false || exec.skipped === true);
        if (execFailed) {
          const msg =
            typeof exec?.message === 'string' && exec.message.trim()
              ? exec.message
              : 'The change was approved but the write was refused.';
          setError(msg);
          window.dispatchEvent(new Event('staging-state-changed'));
          return;
        }
        if (!exec) {
          setError(
            'Approval was recorded. Application is still pending; refresh to check its result.',
          );
          window.dispatchEvent(new Event('staging-state-changed'));
          return;
        }
        // mark-write only when the executor consumed the token — a merely
        // blessed card must stay in the sheet (state_mismatch → 422).
        const stagedState = (blessRes.staged_change as { state?: string } | undefined)
          ?.state;
        if (stagedState && stagedState !== 'consumed') {
          const lastErr = (
            blessRes.staged_change as {
              last_error?: { message?: string } | null;
            }
          )?.last_error;
          setError(
            lastErr?.message?.trim() ||
              'Approved, but the write has not completed yet.',
          );
          window.dispatchEvent(new Event('staging-state-changed'));
          return;
        }
        const res = await markPromptWrite({
          threadId: activeThreadId,
          token: current.token,
          status: 'approved',
        });
        if (res.error) throw new Error(res.detail || 'The change has not finished applying.');
        applyQueueResult(res as never);
        window.dispatchEvent(new Event('staging-state-changed'));
      } catch (err: unknown) {
        const detail =
          err && typeof err === 'object' && 'response' in err
            ? (err as { response?: { data?: { message?: string; detail?: string } } })
                .response?.data
            : undefined;
        setError(
          detail?.message ||
            detail?.detail ||
            (err instanceof Error ? err.message : '') ||
            'Could not approve that write.',
        );
      } finally {
        setBusy(false);
      }
    },
    [activeThreadId, applyQueueResult, busy, confirm, current],
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
