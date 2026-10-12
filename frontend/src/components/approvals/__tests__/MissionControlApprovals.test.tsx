import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  inbox: vi.fn(),
  listApprovals: vi.fn(),
  listPendingStagedChanges: vi.fn(),
}));

vi.mock('../../../api/accessControl', () => ({ ownershipConsentApi: { inbox: mocks.inbox } }));

vi.mock('../../../api/approvals', () => ({ listApprovals: mocks.listApprovals }));
vi.mock('../../../api/agentive', () => ({
  listPendingStagedChanges: mocks.listPendingStagedChanges,
}));

import { MissionControlApprovals } from '../MissionControlApprovals';

beforeEach(() => {
  mocks.inbox.mockResolvedValue({ complete: true, proposals: [] });
  mocks.listApprovals.mockResolvedValue({ approvals: [] });
  mocks.listPendingStagedChanges.mockResolvedValue([]);
});
afterEach(() => cleanup());

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter><MissionControlApprovals /></MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

describe('MissionControlApprovals', () => {
  it('combines policy and agent-chat pending items and links to review', async () => {
    mocks.listApprovals.mockResolvedValue({ approvals: [{ id: 'a1' }, { id: 'a2' }] });
    mocks.listPendingStagedChanges.mockResolvedValue([
      { token: 's1', state: 'pending', kind: 'update_entry' },
      { token: 'design', state: 'pending', kind: 'design_proposal' },
    ]);

    renderPanel();

    expect(await screen.findByText('3 pending items across policy, agent chat and ownership.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review approvals →' })).toHaveAttribute('href', '/approvals');
  });

  it('does not report an empty inbox when a source fails', async () => {
    mocks.listApprovals.mockRejectedValue(new Error('unavailable'));
    mocks.listPendingStagedChanges.mockResolvedValue([]);

    renderPanel();

    await waitFor(() => expect(mocks.listApprovals).toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: 'Approval inbox' })).not.toBeInTheDocument();
  });
  it('includes ownership in the one summary without inline acceptance controls', async () => {
    mocks.inbox.mockResolvedValue({ complete: true, proposals: [{ status: 'pending', expired: false }] });
    renderPanel();
    expect(await screen.findByText('1 pending item across policy, agent chat and ownership.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Accept ownership' })).not.toBeInTheDocument();
  });

  it('does not report no pending approvals when the ownership read is incomplete', async () => {
    mocks.inbox.mockResolvedValue({ complete: false, proposals: [] });
    renderPanel();
    await waitFor(() => expect(mocks.inbox).toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: 'Approval inbox' })).not.toBeInTheDocument();
    expect(screen.queryByText('No pending approvals.')).not.toBeInTheDocument();
  });
  it('hides the home section while loading and when all queues are empty', async () => {
    const { client } = renderPanel();
    expect(screen.queryByRole('heading', { name: 'Approval inbox' })).not.toBeInTheDocument();
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(screen.queryByRole('link', { name: 'Review approvals →' })).not.toBeInTheDocument();
    act(() => client.setQueryData(['mission-control', 'pending-policy-approvals'], { approvals: [{ id: 'new-request' }] }));
    expect(await screen.findByText('1 pending item across policy, agent chat and ownership.')).toBeVisible();
    act(() => client.setQueryData(['mission-control', 'pending-policy-approvals'], { approvals: [] }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Approval inbox' })).not.toBeInTheDocument());
  });

});
