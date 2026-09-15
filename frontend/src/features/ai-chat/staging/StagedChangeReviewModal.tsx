import { CheckIcon, Sparkles, X } from 'lucide-react';
import { Modal } from '../../../components/ui/Modal';
import JsonViewer from '../../../components/ui/JsonViewer';
import { MarkdownContent } from '../../../components/ui/MarkdownContent';
import { Text } from '../../../ui';
import type { StagedChange } from './types';
import type { UseStagedChangeResult } from './useStagedChange';
import './staging.css';

/**
 * Room to actually read a staged change before approving it.
 *
 * The inline card is sized for the dock's narrow column, which is fine for
 * "Create entry X in Marketing" and useless for a batch that provisions an App
 * with four tracks and a taxonomy. Approving what you cannot read is the
 * failure this exists to prevent — the card stays the default, and anything
 * long enough to be unreadable there offers this instead.
 */
export interface StagedChangeReviewModalProps {
  staged: StagedChange;
  open: boolean;
  onClose: () => void;
  /**
   * The caller's LIVE hook result — deliberately passed in rather than the
   * modal calling `useStagedChange` itself.
   *
   * Two hook instances for one token each keep their own status and error, and
   * neither learns what the other saw: a bless refused with 403 sets the error
   * only where it was clicked. That is exactly how the chat card ended up
   * claiming a refused write was "awaiting agent execution". One instance,
   * two renderings.
   */
  controls: UseStagedChangeResult;
}

export function StagedChangeReviewModal({
  staged,
  open,
  onClose,
  controls,
}: StagedChangeReviewModalProps) {
  const { status, error, isBlessed, isTerminal, bless, revoke } = controls;
  const busy = status.kind === 'loading';

  // Close once the change is settled — leaving a modal open over a change that
  // no longer exists invites a second click on a dead button.
  const handleAction = async (run: () => Promise<void>) => {
    await run();
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      titleIcon={<Sparkles size={14} aria-hidden />}
      title={
        <span className="min-w-0 break-words">{staged.summary}</span>
      }
    >
      <Modal.Body>
        <div className="flex flex-col gap-4">
          <div>
            <Text variant="meta" tone="subtle" as="p" className="mb-1 block uppercase tracking-[0.08em]">
              Change
            </Text>
            <div className="staged-review-diff text-sm">
              <MarkdownContent mutedBody={false}>
                {staged.diff_human}
              </MarkdownContent>
            </div>
          </div>

          <div>
            <Text variant="meta" tone="subtle" as="p" className="mb-1 block uppercase tracking-[0.08em]">
              Raw
            </Text>
            {/* A viewer rather than a <pre> dump: the whole point of opening
                this is that the payload was too big to scan inline. */}
            <JsonViewer data={staged.diff_machine} defaultExpandDepth={2} maxHeight="40vh" />
          </div>

          <Text variant="meta" tone="muted" as="p" className="block">
            {staged.kind} · token {staged.token.slice(0, 8)}…
          </Text>

          {error ? (
            <div className="rounded-[var(--radius-input)] bg-[var(--danger-bg)] px-2 py-1">
              <Text variant="meta" tone="danger">{error}</Text>
            </div>
          ) : null}
        </div>
      </Modal.Body>

      <Modal.Footer>
        {status.state === 'pending' ? (
          <div className="flex w-full flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleAction(() => bless('single'))}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-[var(--brand-accent)] px-3 py-1.5 text-xs font-medium text-[var(--brand-accent-fg)] hover:opacity-90 disabled:opacity-50"
            >
              <CheckIcon size={12} /> Approve
            </button>
            <button
              type="button"
              onClick={() => void handleAction(() => bless('session'))}
              disabled={busy}
              title="Approve and auto-allow this kind for the rest of this session"
              className="staged-review-secondary inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-[var(--border-subtle)] px-3 py-1.5 text-xs disabled:opacity-50"
            >
              <Sparkles size={12} /> Approve &amp; auto-allow
            </button>
            <button
              type="button"
              onClick={() => void handleAction(() => revoke())}
              disabled={busy}
              className="staged-review-reject ml-auto inline-flex items-center gap-1 rounded-[var(--radius-pill)] px-3 py-1.5 text-xs disabled:opacity-50"
            >
              <X size={12} /> Reject
            </button>
          </div>
        ) : (
          <Text variant="meta" tone="muted">
            {/* Same wording as the card and the inbox row: `blessed` means
                approved, not applied, and this surface cannot tell which. */}
            {isBlessed
              ? 'Approved — not yet applied.'
              : isTerminal
                ? `No longer actionable (${status.state}).`
                : status.state}
          </Text>
        )}
      </Modal.Footer>
    </Modal>
  );
}

/**
 * Is this change too big to read inside the card?
 *
 * Deliberately generous — the cost of offering review on a change that would
 * have fit is a button nobody needs; the cost of not offering it is someone
 * approving a subgraph they could not see.
 */
export function isLargeStagedDiff(staged: StagedChange): boolean {
  if ((staged.diff_human || '').length > 400) return true;
  const machine = staged.diff_machine || {};
  const steps = (machine as { steps?: unknown[] }).steps;
  if (Array.isArray(steps) && steps.length > 2) return true;
  return JSON.stringify(machine).length > 800;
}
