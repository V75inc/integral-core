import { useCallback, useEffect, useState } from 'react';
import { PageHeading, PageShell, PageSection } from '../ui';
import { Select } from '../../ui';
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
            <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
              <span>
                {rows.length} {rows.length === 1 ? 'task' : 'tasks'}
              </span>
              <span aria-hidden>·</span>
              <span>
                {statusFilter === 'all'
                  ? activeCount === 0
                    ? 'No active routines'
                    : `${activeCount} active`
                  : `Showing ${statusFilter}`}
              </span>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm shrink-0">
            <span className="text-[var(--text-muted)]">Status</span>
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

      <PageSection.Separator />

      <PageSection className="mt-8">
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
