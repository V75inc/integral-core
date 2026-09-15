/**
 * What the card is allowed to claim in the `blessed` state.
 *
 * `blessed` means approved, not applied. The card frequently cannot tell which
 * — the error from a failed write lives only in the hook instance that ran the
 * bless, so a card that learned the state from the WS push or from
 * reconcile-on-mount (approved on the Approvals page, in the agent inbox, or
 * before a reload) holds the state and nothing else.
 *
 * It used to say "Approved. Awaiting agent execution…", which asserts work is
 * in flight. Observed live: a `create_track` the backend refused with
 * `403 insufficient_permissions` rendered exactly that, with a green check,
 * while nothing was executing and nothing ever would.
 */

import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom/vitest';

vi.mock('../../../../api/agentive', () => ({
  blessStagingToken: vi.fn(),
  revokeStagingToken: vi.fn(),
  rollbackStagingToken: vi.fn(),
  getStagingTokenState: vi.fn(async () => ({ state: 'blessed' })),
  getStagingRollbackStatus: vi.fn(async () => ({ ok: true, available: false })),
}));

vi.mock('../../../../services/graphMutationInvalidation', () => ({
  invalidateAfterAgentWrite: vi.fn(async () => undefined),
}));

// The card lives inside a chat thread so it can nudge the agent when a bless
// lands with no write. That runtime is irrelevant to what the card *says*.
vi.mock('@assistant-ui/react', () => ({
  useThreadRuntime: () => ({ append: vi.fn() }),
}));

import { ConfirmProvider } from '../../../../context/ConfirmContext';
import { StagedChangeCard } from '../StagedChangeCard';
import type { StagedChange } from '../types';

const staged = {
  _kind: 'staged_change',
  token: 'tok-blessed',
  kind: 'create_track',
  state: 'pending',
  summary: 'Create track “Field Marketing”',
  diff_human: '',
  diff_machine: {},
  created_at: new Date(0).toISOString(),
  expires_at: new Date(Date.now() + 600_000).toISOString(),
  autonomy_grant_used: false,
} as unknown as StagedChange;

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConfirmProvider>
        <StagedChangeCard staged={staged} />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => cleanup());
afterEach(() => vi.clearAllMocks());

describe('StagedChangeCard — blessed state', () => {
  it('says the change is not yet applied rather than claiming execution is under way', async () => {
    // The card reconciles to `blessed` from the backend and knows nothing
    // about whether the write succeeded, failed, or has no executor at all.
    renderCard();

    await waitFor(() =>
      expect(screen.getByText(/not yet applied/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/awaiting agent execution/i)).toBeNull();
  });

  it('still offers Undo, since an approved-but-unapplied change is revocable', async () => {
    renderCard();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /undo/i })).toBeInTheDocument(),
    );
  });
});
