import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef } from 'react';
import { fetchEventsPage, openEventStream } from '../api/events';
import {
  invalidateAfterAgentWrite,
  invalidateAfterChangeEvent,
} from '../services/graphMutationInvalidation';
import type { StagedChange } from '../features/ai-chat/staging/types';
import { useAuth } from '../context/AuthContext';
import { parseChangeEventWireMessage } from '../utils/changeEvent';

const SEEN_EVENT_CAP = 200;
const SAFETY_POLL_INTERVAL_MS = 15_000;

/**
 * Subscribes to backend ChangeEvents and agent staging completions, then
 * invalidates React Query caches so open entry dialogs and list pages refresh
 * after the resident agent (or any writer) mutates data.
 */
export function useChangeEventInvalidation(): void {
  const { token, user } = useAuth();
  const queryClient = useQueryClient();
  const principalId = user?.user_id ?? user?.id;
  const seenIdsRef = useRef(new Set<string>());
  const pollCursorRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);

  const rememberEvent = useCallback((id: string): boolean => {
    const seen = seenIdsRef.current;
    if (seen.has(id)) return false;
    seen.add(id);
    if (seen.size > SEEN_EVENT_CAP) {
      const drop = [...seen].slice(0, seen.size - SEEN_EVENT_CAP);
      for (const old of drop) seen.delete(old);
    }
    return true;
  }, []);

  const onChangeEvent = useCallback(
    async (raw: unknown) => {
      const evt = parseChangeEventWireMessage(raw);
      if (!evt?.id || !rememberEvent(evt.id)) return;
      await invalidateAfterChangeEvent(queryClient, evt);
    },
    [queryClient, rememberEvent],
  );

  const pollOnce = useCallback(async () => {
    if (!principalId || pollInFlightRef.current) return;
    pollInFlightRef.current = true;
    try {

      while (true) {
        const page = await fetchEventsPage({
          since: pollCursorRef.current,
          scope: `user:${principalId}`,
          limit: 50,
        });
        for (const evt of page.events) {
          if (!evt.id || !rememberEvent(evt.id)) continue;
          await invalidateAfterChangeEvent(queryClient, evt);
        }
        pollCursorRef.current = page.next_cursor;
        if (!page.has_more || !page.next_cursor) break;
      }
    } catch {
      /* silent — WS or next poll will retry */
    } finally {
      pollInFlightRef.current = false;
    }
  }, [principalId, queryClient, rememberEvent]);

  useEffect(() => {
    if (!token || !principalId) return undefined;

    let ws: WebSocket | null = null;
    let cancelled = false;
    let safetyPollTimer: ReturnType<typeof setInterval> | null = null;

    void (async () => {
      try {
        ws = await openEventStream(`user:${principalId}`);
        if (cancelled) {
          ws.close();
          return;
        }
        ws.onmessage = (event: MessageEvent) => {
          try {
            const raw = JSON.parse(String(event.data)) as unknown;
            void onChangeEvent(raw);
          } catch {
            /* ignore malformed payloads */
          }
        };
        ws.onerror = () => {
          /* best-effort — polling fallback still runs */
        };
      } catch {
        /* WS unavailable — polling fallback still runs */
      }
    })();

    safetyPollTimer = setInterval(() => {
      void pollOnce();
    }, SAFETY_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (safetyPollTimer !== null) {
        clearInterval(safetyPollTimer);
      }
      if (ws) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [token, principalId, onChangeEvent, pollOnce]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<StagedChange>).detail;
      if (!detail || detail.state !== 'consumed') return;
      void invalidateAfterAgentWrite(queryClient, { staged: detail });
    };
    window.addEventListener('integral:staging-state-changed', handler);
    return () => {
      window.removeEventListener('integral:staging-state-changed', handler);
    };
  }, [queryClient]);
}
