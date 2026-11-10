import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { entriesApi, type ResidentAssignmentState } from '../../api/entries';
import { workspacesApi } from '../../api/workspaces';
import { errorMessageFromAxios } from '../../api/helpers';
import { Button, Pill } from '../ui';
import { Text } from '../../ui';

const readinessLabels = {
  paused: 'Assignment intake is disabled',
  needs_reassignment: 'Needs reassignment',
  needs_task_setup: 'Needs task setup',
  awaiting_execution_policy: 'Awaiting execution policy',
};

export function useResidentAssignmentEnabled(workspaceId?: string | null) {
  return useQuery({
    queryKey: ['workspace-resident-assignment-settings', workspaceId],
    queryFn: () => workspacesApi.getResidentAssignmentSettings(workspaceId!),
    enabled: Boolean(workspaceId),
    retry: false,
  }).data?.enabled === true;
}

export function ResidentAssignmentControl({
  entryId,
  workspaceId,
  compact = false,
}: {
  entryId: string;
  workspaceId: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const queryKey = ['entry-resident-assignment', workspaceId, entryId];
  const assignment = useQuery({
    queryKey,
    queryFn: () => entriesApi.getResidentAssignment(entryId, workspaceId),
    enabled: !compact || open,
    retry: false,
  });
  const change = useMutation({
    mutationFn: ({ state, revision }: { state: ResidentAssignmentState; revision: number }) =>
      entriesApi.setResidentAssignment(entryId, workspaceId, state, revision),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  });
  const current = assignment.data;
  const assigned = current?.state === 'assigned';

  if (!compact && !assigned && !current?.can_assign) return null;

  return (
    <div
      className={compact ? "flex flex-wrap items-center gap-1.5 px-2 pb-2" : "flex flex-wrap items-center gap-1.5"}
      onClick={event => event.stopPropagation()}
      onMouseDown={event => event.stopPropagation()}
      onTouchStart={event => event.stopPropagation()}
      onKeyDown={event => event.stopPropagation()}
    >
      {compact ? (
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          onClick={() => {
            change.reset();
            setOpen(value => !value);
          }}
        >
          {assigned ? 'Assigned to Integral' : 'Resident assignment'}
        </Button>
      ) : assigned ? <Pill tone="descriptive">Assigned to Integral</Pill> : null}
      {(!compact || open) && (
        <>
          {current?.readiness ? (
            <Text variant="meta">{readinessLabels[current.readiness]}</Text>
          ) : null}
          {assignment.isPending ? <Text variant="meta">Loading assignment…</Text> : null}
          {assignment.isError ? <Text variant="meta">Assignment unavailable.</Text> : null}
          {compact && current && !assigned && !current.can_assign ? (
            <Text variant="meta">Assignment is unavailable for this Entry.</Text>
          ) : null}
          {current && (current.can_assign || current.can_withdraw) ? (
            <Button
              variant="outline"
              size="sm"
              disabled={change.isPending || assignment.isFetching}
              onClick={() => change.mutate({
                state: assigned ? 'withdrawn' : 'assigned',
                revision: current.revision,
              })}
            >
              {assigned ? 'Withdraw assignment' : 'Assign to Integral'}
            </Button>
          ) : null}
          {change.isError ? (
            <div role="alert">
              <Text variant="meta">
                {errorMessageFromAxios(change.error, 'Could not update resident assignment.')}
              </Text>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
