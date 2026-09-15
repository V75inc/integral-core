import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Mail } from 'lucide-react';
import { sharingApi } from '../../api';
import type { MyInvitation } from '../../api/sharing';
import { Button, LINE_ICON_STROKE, Skeleton } from '../ui';
import { useToast } from '../../context/ToastContext';

function invitationLabel(inv: MyInvitation): string {
  if (inv.target_resource_type && inv.target_resource_id) {
    const kind = inv.target_resource_type.charAt(0).toUpperCase()
      + inv.target_resource_type.slice(1);
    return `${kind} access`;
  }
  return inv.workspace_name?.trim() || 'Workspace';
}

function invitationRole(inv: MyInvitation): string {
  return inv.target_resource_role || inv.role || 'member';
}

interface PendingInvitationsPanelProps {
  /** When true, omit the outer section chrome (embedded in another page). */
  compact?: boolean;
  /** Called after accept/decline so parents can refresh. */
  onChanged?(): void;
}

export function PendingInvitationsPanel({
  compact = false,
  onChanged,
}: PendingInvitationsPanelProps) {
  const { showToast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [invitations, setInvitations] = useState<MyInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await sharingApi.listMyInvitations();
      setInvitations(rows.filter(i => i.status === 'pending'));
    } catch {
      setInvitations([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      load(),
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['notifications'] }),
    ]);
    onChanged?.();
  }, [load, onChanged, queryClient]);

  const accept = async (inv: MyInvitation) => {
    setWorkingId(inv.id);
    try {
      const res = await sharingApi.acceptMyInvitation(inv.id);
      showToast(`Joined as ${res.role}`, 'success');
      await refreshAll();
      if (res.workspace?.id) {
        navigate(`/workspaces/${res.workspace.id}`);
      }
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { message?: string; detail?: string } } })
          ?.response?.data?.message
        || (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail
        || 'Could not accept invitation';
      showToast(String(detail), 'error');
    } finally {
      setWorkingId(null);
    }
  };

  const decline = async (inv: MyInvitation) => {
    setWorkingId(inv.id);
    try {
      await sharingApi.declineMyInvitation(inv.id);
      showToast('Invitation declined', 'success');
      await refreshAll();
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { message?: string; detail?: string } } })
          ?.response?.data?.message
        || (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail
        || 'Could not decline invitation';
      showToast(String(detail), 'error');
    } finally {
      setWorkingId(null);
    }
  };

  if (loading) {
    return compact ? (
      <Skeleton className="h-24 rounded-lg" />
    ) : (
      <section aria-label="Pending invitations" className="mb-12">
        <Skeleton className="h-32 rounded-lg" />
      </section>
    );
  }

  if (invitations.length === 0) {
    return null;
  }

  const body = (
    <ul className="divide-y divide-[var(--panel-border)] rounded-lg border border-[var(--panel-border)] bg-[var(--panel)]">
      {invitations.map(inv => {
        const busy = workingId === inv.id;
        const label = invitationLabel(inv);
        const role = invitationRole(inv);
        return (
          <li
            key={inv.id}
            className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0 flex items-start gap-3">
              <Mail
                size={16}
                strokeWidth={LINE_ICON_STROKE}
                className="mt-0.5 shrink-0 text-[var(--text-muted)]"
                aria-hidden
              />
              <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--text)] truncate">
                  Join {label}
                </p>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">
                  Role: {role}
                  {inv.expires_at ? ` · Expires ${inv.expires_at.slice(0, 10)}` : ''}
                </p>
                {inv.message ? (
                  <p className="text-xs text-[var(--text-subtle)] mt-1 italic truncate">
                    "{inv.message}"
                  </p>
                ) : null}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0 sm:ml-4">
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => decline(inv)}
              >
                Decline
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={busy}
                onClick={() => accept(inv)}
              >
                Accept
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );

  if (compact) {
    return body;
  }

  return (
    <section aria-labelledby="pending-invitations-heading" className="mb-12">
      <div className="flex items-baseline justify-between mb-4">
        <h2
          id="pending-invitations-heading"
          className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
        >
          Pending invitations
        </h2>
        <span className="text-xs text-[var(--text-subtle)]">
          {invitations.length} awaiting your response
        </span>
      </div>
      {body}
    </section>
  );
}
