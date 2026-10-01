import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  listApprovals: vi.fn(),
  listPendingStagedChanges: vi.fn(),
}));

vi.mock('../../../api/approvals', () => ({ listApprovals: mocks.listApprovals }));
vi.mock('../../../api/agentive', () => ({
  listPendingStagedChanges: mocks.listPendingStagedChanges,
}));

import { MissionControlApprovals } from '../MissionControlApprovals';

afterEach(() => cleanup());

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><MissionControlApprovals /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('MissionControlApprovals', () => {
  it('combines policy and agent-chat pending items and links to review', async () => {
    mocks.listApprovals.mockResolvedValue({ approvals: [{ id: 'a1' }, { id: 'a2' }] });
    mocks.listPendingStagedChanges.mockResolvedValue([
      { token: 's1', state: 'pending', kind: 'update_entry' },
      { token: 'design', state: 'pending', kind: 'design_proposal' },
    ]);

    renderPanel();

    expect(await screen.findByText('3 pending items across policy and agent chat.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review approvals →' })).toHaveAttribute('href', '/approvals');
  });

  it('does not report an empty inbox when a source fails', async () => {
    mocks.listApprovals.mockRejectedValue(new Error('unavailable'));
    mocks.listPendingStagedChanges.mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByText('Could not check the approval inbox.')).toBeInTheDocument();
  });
});
