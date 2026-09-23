import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Check, MessageSquare, X } from 'lucide-react';
import { LINE_ICON_STROKE } from '../ui';
import { Text } from '../../ui';
import { formatRelativeTime } from '../../utils';
import { listPendingStagedChanges } from '../../api/agentive';
import { useStagedChange } from '../../features/ai-chat/staging/useStagedChange';

import type { StagedChange } from '../../features/ai-chat/staging/types';

/**
 * Pending agent-chat staged changes, surfaced on the Approvals page.
 *
 * A staged change awaiting approval used to be visible ONLY as the inline
 * card inside the chat conversation that minted it — this page said
 * "All clear" at the same moment a card sat AWAITING APPROVAL in chat
 * (June 30 review R3). Approve/Reject here reuse the exact bless/revoke
 * endpoints the chat card calls, so the inline card reconciles to the
 * terminal state on its next mount.
 */
export function StagedChatApprovals({
  onCountChange,
}: {
  onCountChange?: (count: number) => void;
}) {
  const [rows, setRows] = useState<StagedChange[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refetch = useCallback(async () => {
    try {
      const pending = await listPendingStagedChanges();
      // design_proposal is confirmed in chat, not via Approvals Approve —
      // showing it here collapsed design confirm into a bless dialog.
      setRows(
        pending.filter(
          (sc) => sc.state === 'pending' && sc.kind !== 'design_proposal',
        ),
      );
    } catch {
      // Silent: this section is supplementary — the policy-approvals list
      // above owns the page-level error surface.
      setRows([]);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  useEffect(() => {
    if (loaded) onCountChange?.(rows.length);
  }, [loaded, rows.length, onCountChange]);

  if (!loaded || rows.length === 0) return null;

  return (
    <section aria-label="Agent chat approvals" className="mt-8">
      <Text as="h2" variant="body-sm" tone="muted" weight="medium" className="mb-3 block">
        Agent chat approvals
        <Text as="span" variant="body-sm" tone="subtle" className="ml-2">
          staged in conversation — authorizing allows the agent to apply the change
        </Text>
      </Text>
      <div className="space-y-2">
        {rows.map(sc => (
          <StagedChangeRow key={sc.token} staged={sc} onDecide={refetch} />
        ))}
      </div>
    </section>
  );
}

function StagedChangeRow({
  staged,
  onDecide,
}: {
  staged: StagedChange;
  onDecide(): void;
}) {
  // Shares the chat card's state machine rather than reimplementing it.
  //
  // The reimplementation this replaces called `blessStagingToken` and
  // stopped, which was wrong in two ways: approving a kind the backend has
  // no executor for left the token blessed with the write never performed,
  // and a write that DID land was invisible until a manual refresh because
  // nothing invalidated the caches.
  //
  // No `onNeedsAgentNudge`: this surface has no conversation to nudge. When
  // a bless lands without a write, the agent picks it up on its next turn.
  const { busy, error, state, bless, revoke } = useStagedChange(staged, {
    onTerminal: () => onDecide(),
  });
  const awaitingExecution = state === 'blessed';

  return (
    <div className="flex items-start gap-3 rounded-lg border border-[var(--panel-border)] bg-[var(--nav-active-bg)] p-4">
      <div
        className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-[var(--warn-fg)]"
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <Text as="p" variant="body-sm" weight="medium" className="block">
          {staged.summary}
        </Text>
        <Text as="p" variant="meta" tone="muted" className="mt-0.5 block">
          {awaitingExecution
            ? 'Authorized · waiting for the agent to apply it'
            : `Awaiting authorization · staged ${formatRelativeTime(staged.created_at)}`}
          <span className="mx-1.5" aria-hidden>
            ·
          </span>
          expires {formatRelativeTime(staged.expires_at)}
          <span className="mx-1.5" aria-hidden>
            ·
          </span>
          <Link
            to="/agent"
            className="inline-flex items-center gap-1 text-[var(--link)] hover:text-[var(--link-hover)] hover:underline"
          >
            <MessageSquare size={11} strokeWidth={LINE_ICON_STROKE} />
            Review in chat
          </Link>
        </Text>
        {error && (
          <div className="mt-2 rounded-[var(--radius-input)] border border-[color:var(--danger-fg)]/20 bg-[var(--danger-bg)] px-2 py-1 text-[11px] text-[var(--danger-fg)]">
            {error}
          </div>
        )}
      </div>
      {!awaitingExecution && (
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            disabled={busy}
            onClick={() => void bless()}
            aria-label="Authorize staged change"
            className="rounded-lg p-1.5 text-[var(--success-fg)] transition-colors hover:bg-[var(--nav-active-bg)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check size={14} strokeWidth={LINE_ICON_STROKE} />
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void revoke()}
            aria-label="Reject staged change"
            className="rounded-lg p-1.5 text-[var(--danger-fg)] transition-colors hover:bg-[var(--nav-active-bg)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X size={14} strokeWidth={LINE_ICON_STROKE} />
          </button>
        </div>
      )}
    </div>
  );
}
