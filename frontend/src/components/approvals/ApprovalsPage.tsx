import { useCallback, useEffect, useState } from 'react';
import { PageHeading, PageShell, PageSection } from '../ui';
import { Select, Text } from '../../ui';
import { useSetCrumbs } from '../../context/CrumbsContext';
import {
  listApprovals,
  type ApprovalResponse,
} from '../../api/approvals';
import { ApprovalsListBody } from './ApprovalsListBody';
import { StagedChatApprovals } from './StagedChatApprovals';

type StatusFilter = 'pending' | 'approved' | 'rejected' | 'expired';

export function ApprovalsPage() {
  useSetCrumbs([{ label: 'Approvals' }]);

  const [rows, setRows] = useState<ApprovalResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stagedCount, setStagedCount] = useState(0);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listApprovals({ status: statusFilter });
      setRows(data.approvals);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : 'Failed to load approvals';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const pendingCount = rows.filter(r => r.status === 'pending').length;
  // Staged chat changes have no status axis — they are pending or they are
  // gone — so they only belong under the pending filter.
  const showStaged = statusFilter === 'pending';

  return (
    <PageShell>
      <PageSection>
        <header className="mb-8 md:mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <PageHeading>Approvals</PageHeading>
            <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
              <span>
                {rows.length + (statusFilter === 'pending' ? stagedCount : 0)}{' '}
                {rows.length +
                  (statusFilter === 'pending' ? stagedCount : 0) ===
                1
                  ? 'item'
                  : 'items'}
              </span>
              <span aria-hidden>·</span>
              <span>
                {statusFilter === 'pending' &&
                pendingCount === 0 &&
                stagedCount === 0
                  ? 'All clear'
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
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="expired">Expired</option>
            </Select>
          </label>
        </header>
      </PageSection>

      <PageSection.Separator />

      <PageSection className="mt-8">
        {/* Two independent queues live here: policy-gated agent writes, and
            changes staged inside a conversation. Label the first only when
            both are on screen — a lone list needs no heading, but an
            unlabelled one above "Agent chat approvals" reads as its parent. */}
        {showStaged && rows.length > 0 ? (
          <Text
            variant="meta"
            weight="semibold"
            tone="subtle"
            as="h2"
            className="mb-2 block uppercase tracking-[0.08em]"
          >
            Policy approvals
          </Text>
        ) : null}
        <ApprovalsListBody
          rows={rows}
          loading={loading}
          error={error}
          onRetry={() => void refetch()}
          onDecide={() => void refetch()}
          // Its empty state is worded for the whole page; let it speak only
          // when there is genuinely nothing pending anywhere.
          hideEmptyState={showStaged && stagedCount > 0}
        />
        {showStaged && <StagedChatApprovals onCountChange={setStagedCount} />}
      </PageSection>
    </PageShell>
  );
}
