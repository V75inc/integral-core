import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { entriesApi, type ResidentAssignmentView } from '../../../api/entries';
import { ResidentAssignmentControl } from '../ResidentAssignmentControl';

vi.mock('../../../api/entries', () => ({
  entriesApi: { getResidentAssignment: vi.fn(), setResidentAssignment: vi.fn() },
}));

describe('resident assignment control', () => {
  it('loads a Kanban assignment on demand and refreshes the shared Entry state after writes', async () => {
    const client = new QueryClient();
    let current: ResidentAssignmentView = {
      state: 'withdrawn', revision: 0, can_assign: true, can_withdraw: false,
    };
    vi.mocked(entriesApi.getResidentAssignment).mockImplementation(async () => current);
    vi.mocked(entriesApi.setResidentAssignment).mockImplementation(async (_entry, _workspace, state) => {
      current = {
        state,
        revision: current.revision + 1,
        can_assign: state === 'withdrawn',
        can_withdraw: state === 'assigned',
      };
      return current;
    });
    const props = { entryId: 'entry-1', workspaceId: 'workspace-1' };
    render(
      <QueryClientProvider client={client}>
        <ResidentAssignmentControl {...props} compact />
      </QueryClientProvider>,
    );
    expect(entriesApi.getResidentAssignment).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Resident assignment' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Assign to Integral' }));
    await waitFor(() => expect(entriesApi.setResidentAssignment).toHaveBeenCalledWith(
      'entry-1', 'workspace-1', 'assigned', 0,
    ));
    expect(await screen.findByRole('button', { name: 'Assigned to Integral' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw assignment' }));
    await waitFor(() => expect(entriesApi.setResidentAssignment).toHaveBeenLastCalledWith(
      'entry-1', 'workspace-1', 'withdrawn', 1,
    ));
    expect(await screen.findByRole('button', { name: 'Assign to Integral' })).toBeVisible();
  });
});
