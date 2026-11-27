import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useSetCrumbs } from '../context/CrumbsContext';
import { useScope } from '../context/ScopeContext';
import { tracksApi } from '../api';
import { tracksListQueryKey } from '../queryKeys';
import { usePublishPageContext } from '../hooks/usePublishPageContext';

/** The route publishes page context. Layout owns the chat across presentation changes. */
export function AIChatPage() {
  useSetCrumbs([]);
  const { activeWorkspace } = useScope();
  const [searchParams, setSearchParams] = useSearchParams();
  const workspaceId = activeWorkspace?.id ?? null;
  // `/agent` replaces the page the user arrived from, so it must publish its
  // own scoped context instead of inheriting a now-unmounted page's snapshot.
  // The track list stays out of the prompt stub and is available only through
  // `integral_get_page_context` when the resident harness needs it.
  const tracksQuery = useQuery({
    queryKey: [...tracksListQueryKey(''), workspaceId] as const,
    queryFn: () => tracksApi.list({ limit: 100, skipEntryCounts: true }),
    enabled: Boolean(workspaceId),
  });
  const tracks = tracksQuery.data ?? [];

  usePublishPageContext({
    pageKind: 'agent_workspace',
    visibleData: {
      tracks: tracks.map(track => ({
        id: track.id,
        title: track.title || undefined,
      })),
      total_count: tracks.length,
    },
    metadata: {
      active_workspace_id: workspaceId,
      active_workspace_name: activeWorkspace?.name ?? null,
      active_workspace_kind: activeWorkspace?.kind ?? null,
    },
  });
  // A `?thread=` deep link names a thread in the workspace it was opened in.
  // Drop it when the user switches workspaces so neither the boundary key
  // nor the runtime's initial selection carries the stale id across. The
  // first resolution (`null -> ws`) is a mount, not a switch.
  const previousWorkspaceIdRef = useRef<string | null>(workspaceId);
  useEffect(() => {
    const previous = previousWorkspaceIdRef.current;
    previousWorkspaceIdRef.current = workspaceId;
    if (previous == null || workspaceId == null || previous === workspaceId) return;
    if (!searchParams.has('thread')) return;
    const next = new URLSearchParams(searchParams);
    next.delete('thread');
    setSearchParams(next, { replace: true });
    // Only a workspace change should clear the param — not a param change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);
  return null;
}
