import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { listApprovals } from '../../api/approvals';
import { listPendingStagedChanges } from '../../api/agentive';
import { ownershipRowsForStatus, useOwnershipProposals } from '../access/useOwnershipProposals';
import { Surface, Text } from '../../ui';

/** Compact cross-workspace inbox preview; decisions stay on the Approvals page. */
export function MissionControlApprovals() {
  const policy = useQuery({
    queryKey: ['mission-control', 'pending-policy-approvals'],
    queryFn: () => listApprovals({ status: 'pending' }),
  });
  const staged = useQuery({
    queryKey: ['mission-control', 'pending-staged-approvals'],
    queryFn: listPendingStagedChanges,
  });
  const ownership = useOwnershipProposals();
  const ownershipCount = ownershipRowsForStatus(ownership.data ?? [], 'pending').length;
  const policyCount = policy.data?.approvals.length ?? 0;
  const stagedCount = (staged.data ?? []).filter(
    row => row.state === 'pending' && row.kind !== 'design_proposal',
  ).length;
  const count = policyCount + stagedCount + ownershipCount;

  if (count === 0) return null;

  return (
    <Surface tone="panel-2" border="default" radius="card" className="mb-10 flex flex-wrap items-center justify-between gap-3 p-4">
      <div>
        <Text as="h2" variant="body" weight="semibold">Approval inbox</Text>
        <Text as="p" variant="body-sm" tone="muted" className="mt-1">
          {policy.isError || staged.isError || ownership.isError
            ? 'Could not check the approval inbox.'
            : policy.isPending || staged.isPending || ownership.isPending
            ? 'Checking for pending approvals…'
            : `${count} pending ${count === 1 ? 'item' : 'items'} across policy, agent chat and ownership.`}
        </Text>
      </div>
      <Link to="/approvals" className="hover:underline">
        <Text variant="body" weight="medium">Review approvals →</Text>
      </Link>
    </Surface>
  );
}
