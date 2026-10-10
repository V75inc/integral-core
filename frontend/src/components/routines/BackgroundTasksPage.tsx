import { useCallback, useEffect, useState } from 'react';
import { PageHeading, PageShell, PageSection } from '../ui';
import { Select, Text } from '../../ui';
import { useScopeOptional } from '../../context/ScopeContext';
import { WorkItemsSection } from './WorkItemsSection';
import { useSetCrumbs } from '../../context/CrumbsContext';
import {
  listRoutines,
  type RoutineResponse,
  type RoutineStatus,
} from '../../api/routines';
import { RoutinesListBody } from './RoutinesListBody';

type StatusFilter = RoutineStatus | 'all';

export function BackgroundTasksPage() {
  useSetCrumbs([{ label: 'Background Tasks' }]);
  const workspaceId = useScopeOptional()?.scope?.workspaceId;

  const [rows, setRows] = useState<RoutineResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listRoutines(
        statusFilter === 'all' ? {} : { status: statusFilter },
      );
      setRows(data.routines);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to load background tasks';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const activeCount = rows.filter(r => r.status === 'active').length;

  return (
    <PageShell>
      <PageSection>
        <header className="mb-8 md:mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <PageHeading>Background Tasks</PageHeading>
            <Text as="div" variant="meta" tone="subtle" className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2">
              <span>
                {rows.length} {rows.length === 1 ? 'scheduled routine' : 'scheduled routines'}
              </span>
              <span aria-hidden>·</span>
              <span>
                {statusFilter === 'all'
                  ? activeCount === 0
                    ? 'No active routines'
                    : `${activeCount} active`
                  : `Showing ${statusFilter}`}
              </span>
            </Text>
          </div>
          <label className="flex items-center gap-2 text-sm shrink-0">
            <Text as="span" tone="subtle" variant="meta">Status</Text>
            <Select
              size="sm"
              value={statusFilter}
              onChange={e =>
                setStatusFilter(e.target.value as StatusFilter)
              }
            >
              <option value="all">All (excl. cancelled)</option>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
            </Select>
          </label>
        </header>
      </PageSection>

      {workspaceId && <PageSection className="mb-8">
        <WorkItemsSection key={workspaceId} workspaceId={workspaceId} />
      </PageSection>}
      <PageSection.Separator />

      <PageSection className="mt-8">
        <Text as="h2" weight="medium" className="mb-3">Scheduled routines</Text>
        <RoutinesListBody
          rows={rows}
          loading={loading}
          error={error}
          onRetry={() => void refetch()}
          onChanged={() => void refetch()}
        />
      </PageSection>
    </PageShell>
  );
}
