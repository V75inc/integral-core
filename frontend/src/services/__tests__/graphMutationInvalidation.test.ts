import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import {
  COMMENT_REFETCH_EVENT,
  dispatchEntryRefetch,
  ENTRY_REFETCH_EVENT,
  invalidateAfterAgentWrite,
  invalidateAfterChangeEvent,
  invalidateAfterChangeEvents,
  refreshCommittedTrackEntries,
  shouldCountTrackPage,
} from '../graphMutationInvalidation';
import type { StagedChange } from '../../features/ai-chat/staging/types';
import { parseChangeEventWireMessage } from '../../utils/changeEvent';

function staged(partial: Partial<StagedChange>): StagedChange {
  return {
    _kind: 'staged_change',
    token: 'tok',
    kind: 'update_entry',
    summary: 'Update entry',
    diff_human: '…',
    diff_machine: { op: 'update_entry', track_id: 'n.Track.1', entry_id: 'n.Entry.1' },
    state: 'consumed',
    created_at: '2026-01-01T00:00:00Z',
    expires_at: '2026-01-01T00:10:00Z',
    autonomy_grant_used: false,
    ...partial,
  };
}

describe('invalidateAfterChangeEvent', () => {
  it.each(['comment.create', 'comment.update', 'comment.delete'])('refreshes entry comments and related lists after %s', async action => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const handler = vi.fn();
    window.addEventListener(COMMENT_REFETCH_EVENT, handler);
    try {
      await invalidateAfterChangeEvent(qc, {
        id: 'evt-comment',
        ts: '2026-01-01T00:00:00Z',
        actor_kind: 'user',
        actor_id: 'user-a',
        action,
        resource_type: 'Comment',
        resource_id: 'n.Comment.1',
        scope: 'track:n.Track.abc',
        after: { _entry_id: 'n.Entry.42' },
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['feed'] });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ['track', 'n.Track.abc', 'entries'],
      });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(
        (handler.mock.calls[0][0] as CustomEvent<{ entryId: string }>).detail
          .entryId,
      ).toBe('n.Entry.42');
    } finally {
      window.removeEventListener(COMMENT_REFETCH_EVENT, handler);
    }
  });

  it('refreshes the notification list when a notification is created', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const handler = vi.fn();
    window.addEventListener(COMMENT_REFETCH_EVENT, handler);

    try {
      await invalidateAfterChangeEvent(qc, {
        id: 'evt-notification',
        ts: '2026-01-01T00:00:00Z',
        actor_kind: 'system',
        actor_id: 'system',
        action: 'notification.create',
        resource_type: 'Notification',
        resource_id: 'n.Notification.1',
        scope: 'user:user-b',
        after: {
          type: 'mention',
          entry_id: 'n.Entry.42',
        },
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['notifications'] });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(
        (handler.mock.calls[0][0] as CustomEvent<{ entryId: string }>).detail
          .entryId,
      ).toBe('n.Entry.42');
    } finally {
      window.removeEventListener(COMMENT_REFETCH_EVENT, handler);
    }
  });

  it('invalidates feed and track entry queries on entry.update', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterChangeEvent(qc, {
      id: 'evt-1',
      ts: '2026-01-01T00:00:00Z',
      actor_kind: 'agent',
      actor_id: 'agent-1',
      action: 'entry.update',
      resource_type: 'Entry',
      resource_id: 'n.Entry.1',
      scope: 'track:n.Track.abc',
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['feed'] });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['track', 'n.Track.abc', 'entries'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboard-data'],
    });
  });

  it('dispatches entry refetch on entry.update', async () => {
    const qc = new QueryClient();
    const handler = vi.fn();
    window.addEventListener(ENTRY_REFETCH_EVENT, handler);
    try {
      await invalidateAfterChangeEvent(qc, {
        id: 'evt-2',
        ts: '2026-01-01T00:00:00Z',
        actor_kind: 'agent',
        actor_id: 'agent-1',
        action: 'entry.update',
        resource_id: 'n.Entry.99',
        scope: 'track:n.Track.abc',
      });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(
        (handler.mock.calls[0][0] as CustomEvent<{ entryId: string }>).detail
          .entryId,
      ).toBe('n.Entry.99');
    } finally {
      window.removeEventListener(ENTRY_REFETCH_EVENT, handler);
    }
  });

  it('does not dispatch entry refetch on entry.delete', async () => {
    const qc = new QueryClient();
    const handler = vi.fn();
    window.addEventListener(ENTRY_REFETCH_EVENT, handler);
    try {
      await invalidateAfterChangeEvent(qc, {
        id: 'evt-3',
        ts: '2026-01-01T00:00:00Z',
        actor_kind: 'agent',
        actor_id: 'agent-1',
        action: 'entry.delete',
        resource_id: 'n.Entry.99',
        scope: 'track:n.Track.abc',
      });
      expect(handler).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(ENTRY_REFETCH_EVENT, handler);
    }
  });

  it('marks a deleted track stale without refetching it', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterChangeEvent(qc, {
      id: 'evt-track-delete',
      ts: '2026-01-01T00:00:00Z',
      actor_kind: 'user',
      actor_id: 'user-a',
      action: 'track.delete',
      resource_type: 'Track',
      resource_id: 'n.Track.deleted',
      scope: 'track:n.Track.deleted',
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['feed'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['workspaces'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['tracks'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['dashboard-data'] });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['track', 'n.Track.deleted'],
      refetchType: 'none',
    });
    expect(invalidateSpy).not.toHaveBeenCalledWith({
      queryKey: ['track', 'n.Track.deleted', 'entries'],
    });
  });

  it('invalidates dashboards on dashboard.update', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterChangeEvent(qc, {
      id: 'evt-4',
      ts: '2026-01-01T00:00:00Z',
      actor_kind: 'agent',
      actor_id: 'agent-1',
      action: 'dashboard.update',
      resource_type: 'Dashboard',
      resource_id: 'n.Dashboard.1',
      scope: 'app:n.WorkspaceApp.abc',
    });

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboards', 'n.WorkspaceApp.abc'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboard-data', 'n.WorkspaceApp.abc'],
    });
  });
});

describe('invalidateAfterAgentWrite', () => {
  it('invalidates feed and track entries for update_entry staging', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterAgentWrite(qc, {
      staged: staged({ kind: 'update_entry' }),
      executeResult: { entry: { id: 'n.Entry.1', track_id: 'n.Track.abc' } },
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['feed'] });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['track', 'n.Track.abc', 'entries'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboard-data'],
    });
  });

  it('invalidates dashboards for create_dashboard staging', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterAgentWrite(qc, {
      staged: staged({
        kind: 'create_dashboard',
        diff_machine: { op: 'create_dashboard', app_id: 'n.WorkspaceApp.abc' },
      }),
    });

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboards', 'n.WorkspaceApp.abc'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboard-data', 'n.WorkspaceApp.abc'],
    });
  });

  it('invalidates dashboard-data for update_entry when app_id is present', async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    await invalidateAfterAgentWrite(qc, {
      staged: staged({
        kind: 'update_entry',
        diff_machine: {
          op: 'update_entry',
          track_id: 'n.Track.1',
          entry_id: 'n.Entry.1',
          app_id: 'n.WorkspaceApp.xyz',
        },
      }),
      executeResult: { entry: { id: 'n.Entry.1', track_id: 'n.Track.1' } },
    });

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['dashboard-data', 'n.WorkspaceApp.xyz'],
    });
  });

  it('dispatches entry refetch for consumed update_entry staging', async () => {
    const qc = new QueryClient();
    const handler = vi.fn();
    window.addEventListener(ENTRY_REFETCH_EVENT, handler);
    try {
      await invalidateAfterAgentWrite(qc, {
        staged: staged({ kind: 'update_entry' }),
        executeResult: { entry: { id: 'n.Entry.1', track_id: 'n.Track.abc' } },
      });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(
        (handler.mock.calls[0][0] as CustomEvent<{ entryId: string }>).detail
          .entryId,
      ).toBe('n.Entry.1');
    } finally {
      window.removeEventListener(ENTRY_REFETCH_EVENT, handler);
    }
  });
});

describe('dispatchEntryRefetch', () => {
  it('emits integral:entry-refetch with entryId detail', () => {
    const handler = vi.fn();
    window.addEventListener(ENTRY_REFETCH_EVENT, handler);
    try {
      dispatchEntryRefetch('n.Entry.42');
      expect(handler).toHaveBeenCalledTimes(1);
      expect(
        (handler.mock.calls[0][0] as CustomEvent<{ entryId: string }>).detail
          .entryId,
      ).toBe('n.Entry.42');
    } finally {
      window.removeEventListener(ENTRY_REFETCH_EVENT, handler);
    }
  });
});

it.each(['tag.create', 'view.update', 'entry_type.update', 'operational_model.publish'])(
  'refreshes open tag and schema controls after %s', async action => {
    const qc = new QueryClient();
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    await invalidateAfterChangeEvent(qc, { id:'metadata-event', ts:'2026-01-01T00:00:00Z',
      actor_kind:'user',actor_id:'user',action,resource_type:'Tag',resource_id:'resource',scope:'track:track'});
    expect(invalidate).toHaveBeenCalledWith({queryKey:['tags']});
    expect(invalidate).toHaveBeenCalledWith({queryKey:['views']});
    expect(invalidate).toHaveBeenCalledWith({queryKey:['entryTypes']});
  },
);
it.each(['create_tag', 'batch'])('refreshes metadata immediately after an approved %s', async kind => {
  const qc = new QueryClient();
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  await invalidateAfterAgentWrite(qc, {staged:staged({kind})});
  expect(invalidate).toHaveBeenCalledWith({queryKey:['tags']});
  expect(invalidate).toHaveBeenCalledWith({queryKey:['operationalModel']});
});
it('marks a deleted library detail stale without fetching it again', async () => {
  const qc = new QueryClient();
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  await invalidateAfterChangeEvent(qc, {id:'delete',ts:'2026-01-01T00:00:00Z',actor_kind:'user',actor_id:'user',action:'operational_model.delete',resource_type:'OperationalModel',resource_id:'model'});
  expect(invalidate).toHaveBeenCalledWith({queryKey:['operational-model'],refetchType:'none'});
  expect(invalidate).toHaveBeenCalledWith({queryKey:['operational-models']});
});


it('reconciles a hundred changes with one refetch and one detail notification', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const read = vi.fn().mockResolvedValue([]);
  await qc.fetchQuery({ queryKey: ['feed', 'entries'], queryFn: read });
  const observer = new QueryObserver(qc, { queryKey: ['feed', 'entries'], queryFn: read });
  const unsubscribe = observer.subscribe(() => {});
  read.mockClear();
  const detail = vi.fn();
  window.addEventListener(ENTRY_REFETCH_EVENT, detail);
  try {
    await invalidateAfterChangeEvents(qc, Array.from({ length: 100 }, (_, n) => ({ id: `event-${n}`, ts: '2026-01-01T00:00:00Z', actor_kind: 'agent', actor_id: 'agent', action: 'entry.update', resource_id: 'entry', scope: 'track:track' })));
    expect(read).toHaveBeenCalledTimes(1);
    expect(detail).toHaveBeenCalledTimes(1);
  } finally {
    unsubscribe(); window.removeEventListener(ENTRY_REFETCH_EVENT, detail); qc.clear();
  }
});

it('a deletion withholds deleted detail refetches in a mixed event burst', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const read = vi.fn().mockResolvedValue({});
  const queryKey = ['track', 'track'];
  await qc.fetchQuery({ queryKey, queryFn: read });
  const observer = new QueryObserver(qc, { queryKey, queryFn: read });
  const unsubscribe = observer.subscribe(() => {});
  read.mockClear();
  try {
    await invalidateAfterChangeEvents(qc, ['track.update', 'track.delete'].map((action, n) => ({ id: String(n), ts: '2026-01-01T00:00:00Z', actor_kind: 'agent', actor_id: 'agent', action, resource_id: 'track', scope: 'track:track' })));
    expect(read).not.toHaveBeenCalled();
    expect(qc.getQueryState(queryKey)?.isInvalidated).toBe(true);
  } finally { unsubscribe(); qc.clear(); }
});

it('finishes an older read then refreshes once after a mutation burst', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const queryKey = ['feed', 'entries'];
  const read = vi.fn().mockResolvedValue('initial');
  await qc.fetchQuery({ queryKey, queryFn: read });
  const observer = new QueryObserver(qc, { queryKey, queryFn: read });
  const unsubscribe = observer.subscribe(() => {});
  let finish!: (value: string) => void;
  read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue('latest');
  read.mockClear();
  const older = qc.refetchQueries({ queryKey });
  const applied = invalidateAfterChangeEvents(qc, [{ id: 'event', ts: '2026-01-01T00:00:00Z', actor_kind: 'agent', actor_id: 'agent', action: 'entry.update', resource_id: 'entry', scope: 'track:track' }]);
  await Promise.resolve();
  finish('older');
  await Promise.all([older, applied]);
  expect(read).toHaveBeenCalledTimes(2);
  expect(qc.getQueryData(queryKey)).toBe('latest');
  unsubscribe(); qc.clear();
});


const entryMutation = (action: string, scope = 'track:track', details?: Record<string, unknown>) =>
  parseChangeEventWireMessage({
    type: 'change_event',
    payload: {
      id: action,
      action,
      scope,
      resource_id: 'entry',
      actor: { kind: 'agent', id: 'worker' },
      details,
    },
  })!;

it('refreshes filtered and sorted rows and their count when an entry is added or removed', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const matching = [{ name: 'Zebra', active: true }, { name: 'Alder', active: true }];
  const active = () => matching.filter(row => row.active);
  const viewKey = 'active-only|-title|p2x1';
  const paged = ['track', 'track', 'entries', viewKey];
  const exactTotal = ['track', 'track', 'entries-total', 'active-only'];
  const other = ['track', 'unrelated', 'entries', viewKey];
  const page = vi.fn(() => {
    const sorted = active().sort((a, b) => b.name.localeCompare(a.name));
    return Promise.resolve({
      rows: sorted.slice(1, 2).map(row => row.name),
      ...(shouldCountTrackPage(qc, paged, 2, 2) ? { total: sorted.length } : {}),
    });
  });
  const total = vi.fn(async () => active().length);
  const unrelated = vi.fn(async () => ['unchanged']);
  const reads = [[paged, page], [exactTotal, total], [other, unrelated]] as const;
  const subscriptions = [];
  for (const [queryKey, read] of reads) {
    const queryFn = async (): Promise<unknown> => read();
    await qc.fetchQuery({ queryKey, queryFn });
    subscriptions.push(new QueryObserver(qc, { queryKey, queryFn }).subscribe(() => {}));
  }
  try {
    expect(qc.getQueryData(paged)).toEqual({ rows: ['Alder'] }); // no repeat count just to page
    const created = { name: 'Mango', active: true };
    matching.push(created, { name: 'Other sort position', active: false });
    await invalidateAfterChangeEvents(qc, [entryMutation('entry.create')]);
    expect(qc.getQueryData(paged)).toEqual({ rows: ['Mango'], total: 3 });
    expect(qc.getQueryData(exactTotal)).toBe(3);

    matching.splice(0, 1);
    await invalidateAfterChangeEvents(qc, [entryMutation('entry.delete')]);
    expect(qc.getQueryData(paged)).toEqual({ rows: ['Alder'], total: 2 });
    expect(qc.getQueryData(exactTotal)).toBe(2);
    expect(page).toHaveBeenCalledTimes(3);
    expect(total).toHaveBeenCalledTimes(3);
    expect(unrelated).toHaveBeenCalledTimes(1);
  } finally {
    subscriptions.forEach(unsubscribe => unsubscribe());
    qc.clear();
  }
});

it('recounts the active page after an older direct read finishes without a count', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const key = ['track', 'track', 'entries', 'filter|-name|p3x10'];
  let finish!: (value: { rows: string[] }) => void;
  const read = vi.fn()
    .mockResolvedValueOnce({ rows: ['initial'] })
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockImplementation(() => Promise.resolve({
      rows: [],
      ...(shouldCountTrackPage(qc, key, 3, 21) ? { total: 20 } : {}),
    }));
  await qc.fetchQuery({ queryKey: key, queryFn: read });
  const unsubscribe = new QueryObserver(qc, { queryKey: key, queryFn: read }).subscribe(() => {});
  try {
    const older = qc.refetchQueries({ queryKey: key });
    const committed = refreshCommittedTrackEntries(qc, 'track');
    await Promise.resolve();
    finish({ rows: ['older'] });
    await Promise.all([older, committed]);
    expect(read).toHaveBeenCalledTimes(3);
    expect(qc.getQueryData(key)).toEqual({ rows: [], total: 20 });
  } finally {
    unsubscribe();
    qc.clear();
  }
});

it('refreshes both sides of an authoritative bulk move and not unrelated tracks', async () => {
  const qc = new QueryClient();
  for (const id of ['from', 'to', 'unrelated']) {
    qc.setQueryData(['track', id, 'entries'], []);
    qc.setQueryData(['track', id, 'entries-total'], 0);
  }
  await invalidateAfterChangeEvents(qc, [
    entryMutation('entry.update', 'track:to', {
      operation: 'bulk_move',
      source_track_id: 'from',
      target_track_id: 'to',
    }),
  ]);
  for (const dataset of ['entries', 'entries-total']) {
    expect(qc.getQueryState(['track', 'from', dataset])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['track', 'to', dataset])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['track', 'unrelated', dataset])?.isInvalidated).toBe(false);
  }
  qc.clear();
});

it('reconciles a committed deletion receipt whose track is reported at the top level', async () => {
  const qc = new QueryClient();
  const key = ['track', 'actual-track', 'entries-total', 'saved-view'];
  qc.setQueryData(key, 2);
  await invalidateAfterAgentWrite(qc, {
    staged: staged({ kind: 'delete_entry', diff_machine: {} }),
    executeResult: { entry_id: 'record', track_id: 'actual-track', deleted: true },
  });
  expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
  qc.clear();
});
