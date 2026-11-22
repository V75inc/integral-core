import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { Button, LINE_ICON_STROKE, PageHeading, PageMeta, PageShell, PageSection } from '../ui';
import { Select, Surface, Text } from '../../ui';
import { useScopeOptional } from '../../context/ScopeContext';
import { WorkItemsSection } from './WorkItemsSection';
import { useSetCrumbs } from '../../context/CrumbsContext';
import {
  getRoutine,
  listRoutines,
  type RoutineResponse,
  type RoutineStatus,
} from '../../api/routines';
import { RoutinesListBody } from './RoutinesListBody';

type StatusFilter = RoutineStatus | 'all';

export function BackgroundTasksPage() {
  useSetCrumbs([{ label: 'Background Tasks' }]);
  const { hash, search } = useLocation();
  const selectedRoutineId = new URLSearchParams(search).get('routine') || '';
  const workspaceId = useScopeOptional()?.scope?.workspaceId;

  useEffect(() => {
    if (hash === '#scheduled-routines') {
      document.getElementById('scheduled-routines')?.scrollIntoView?.({ block: 'start' });
    }
  }, [hash]);

  const [selectedRoutine, setSelectedRoutine] = useState<RoutineResponse | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [rows, setRows] = useState<RoutineResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const requestGeneration = useRef(0);
  const refetch = useCallback(async () => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const data = await listRoutines(
        statusFilter === 'all' ? {} : { status: statusFilter },
      );
      if (generation !== requestGeneration.current) return;
      setRows(data.routines);
      setSelectedRoutine(null);
      setSelectionError(null);
      if (selectedRoutineId && !data.routines.some(row => row.id === selectedRoutineId)) {
        try {
          const selected = await getRoutine(selectedRoutineId);
          if (generation === requestGeneration.current) setSelectedRoutine(selected);
        } catch {
          if (generation === requestGeneration.current) setSelectionError('This routine is unavailable or you no longer have access to it.');
        }
      }
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to load background tasks';
      if (generation === requestGeneration.current) setError(msg);
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [statusFilter, selectedRoutineId, workspaceId]);

  useEffect(() => {
    void refetch();
    return () => { requestGeneration.current += 1; };
  }, [refetch]);

  const visibleRows = selectedRoutine ? [selectedRoutine, ...rows] : rows;
  const activeCount = visibleRows.filter(r => r.status === 'active').length;

  return (
    <PageShell>
      <PageSection>
        <PageHeading className="mb-8 md:mb-10 [&>span:last-child]:whitespace-normal [&>span:last-child]:text-clip">Background Tasks</PageHeading>
      </PageSection>

      <PageSection.Separator />

      <PageSection className="mt-8" innerClassName="grid gap-8">
        {workspaceId && <WorkItemsSection key={workspaceId} workspaceId={workspaceId} />}
        <Surface id="scheduled-routines" as="section" aria-label="Scheduled routines" tone="panel" border="subtle" radius="card" padding="lg">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1 basis-64">
              <Text as="h2" variant="heading-md">Scheduled routines</Text>
              <Text as="p" tone="muted" variant="subtitle" className="mt-1">
                Tasks scheduled in chat, including reminders and recurring work.
              </Text>
            </div>
            <div className="flex min-w-0 max-w-full flex-wrap items-end gap-2">
              <label className="flex min-w-0 max-w-full flex-col gap-1">
                <Text as="span" tone="muted" variant="label">Routine status</Text>
                <Select size="sm" value={statusFilter}
                  onChange={e => setStatusFilter(e.target.value as StatusFilter)}>
                  <option value="all">All (excl. cancelled)</option>
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                  <option value="completed">Completed</option>
                  <option value="cancelled">Cancelled</option>
                </Select>
              </label>
              <Button type="button" size="sm" variant="outline" loading={loading}
                aria-label="Refresh scheduled routines" aria-busy={loading}
                icon={<RefreshCw size={14} strokeWidth={LINE_ICON_STROKE} aria-hidden />}
                onClick={() => void refetch()}>
                {loading ? 'Refreshing…' : 'Refresh'}
              </Button>
            </div>
          </header>
          <div className="mt-5">
            {loading ? <Text as="p" variant="meta" tone="muted" role="status" className="mb-3">Loading scheduled routines…</Text>
              : !error && visibleRows.length > 0 && (
                <PageMeta className="mb-3">
                  <span>{visibleRows.length} {visibleRows.length === 1 ? 'scheduled routine' : 'scheduled routines'}</span>
                  {statusFilter === 'all' && <>
                    <span aria-hidden>·</span>
                    <span>{activeCount === 0 ? 'No active routines' : `${activeCount} active`}</span>
                  </>}
                </PageMeta>
              )}
            {selectedRoutine && <Text as="p" variant="meta" tone="muted" className="mb-3">The linked routine is shown even though it is outside this filter.</Text>}
            {selectionError && <div role="status" className="mb-3"><Text as="p" tone="muted">{selectionError}</Text></div>}
            <RoutinesListBody
              rows={visibleRows}
              selectedRoutineId={selectedRoutineId}
              loading={loading}
              error={error}
              emptyMessage={statusFilter === 'all' ? undefined : 'No routines match this status.'}
              onRetry={() => void refetch()}
              onChanged={() => void refetch()}
            />
          </div>
        </Surface>
      </PageSection>
    </PageShell>
  );
}
