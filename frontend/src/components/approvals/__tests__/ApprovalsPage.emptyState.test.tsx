/**
 * Page-level wiring for the two approval queues.
 *
 * `ApprovalsListBody` learned to stay quiet via `hideEmptyState`, but the
 * bug was only fixed once the page passed it. Observed live: "0 items" was
 * wrong too — the header read "1 item", the body read "No pending
 * approvals", and a pending staged change sat right below. This pins the
 * page's own logic, which the component-level test cannot reach.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom/vitest';

vi.mock('../../../api/approvals', () => ({
  listApprovals: vi.fn(async () => ({ approvals: [] })),
  approveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}));

vi.mock('../../../api/agentive', () => ({
  listPendingStagedChanges: vi.fn(async () => []),
  blessStagingToken: vi.fn(),
  revokeStagingToken: vi.fn(),
  rollbackStagingToken: vi.fn(),
  // The row reconciles on mount; `pending` keeps it in the state this test is
  // about. Returning null would resolve it to `expired`, which is a different
  // scenario entirely.
  getStagingTokenState: vi.fn(async () => ({ state: 'pending' })),
  getStagingRollbackStatus: vi.fn(async () => ({ ok: true, available: false })),
}));

vi.mock('../../../context/CrumbsContext', () => ({
  useSetCrumbs: () => {},
}));

import * as agentiveApi from '../../../api/agentive';
import { ApprovalsPage } from '../ApprovalsPage';

const stagedChange = {
  token: 'tok-1',
  kind: 'batch',
  summary: 'Client Work tracker: Clients track + Tasks track',
  diff_human: '',
  state: 'pending',
  created_at: new Date(0).toISOString(),
  expires_at: new Date(0).toISOString(),
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ApprovalsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(agentiveApi.listPendingStagedChanges).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ApprovalsPage empty state', () => {
  it('says nothing is pending only when both queues are empty', async () => {
    renderPage();
    expect(await screen.findByText('No pending approvals')).toBeInTheDocument();
    expect(screen.getByText('All clear')).toBeInTheDocument();
  });

  it('suppresses the empty state when a staged change is pending', async () => {
    vi.mocked(agentiveApi.listPendingStagedChanges).mockResolvedValue([
      stagedChange,
    ] as never);

    renderPage();

    // The staged row arrives...
    expect(
      await screen.findByText(/Client Work tracker/),
    ).toBeInTheDocument();
    // ...and the policy list must not contradict it.
    await waitFor(() =>
      expect(screen.queryByText('No pending approvals')).not.toBeInTheDocument(),
    );
    expect(screen.queryByText('All clear')).not.toBeInTheDocument();
    expect(screen.getByText('1 item')).toBeInTheDocument();
  });
});
