import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import '@testing-library/jest-dom/vitest';
import { ConfirmProvider } from '../../../../context/ConfirmContext';
import { InboxView } from '../InboxView';

const { bless, revoke, inboxState } = vi.hoisted(() => ({ bless: vi.fn(), revoke: vi.fn(), inboxState: { failed: false } }));
vi.mock('../useAgentInbox', () => ({
  useAgentInbox: () => ({
    staged: inboxState.failed ? [] : [{
      _kind: 'staged_change', token: 'mcp-proposal', session_id: null,
      kind: 'update_entry', state: 'pending', summary: 'Update draft note',
      diff_human: 'Replace the draft with the reviewed note.', diff_machine: {},
      created_at: '2026-10-11T00:00:00Z', expires_at: '2026-10-11T00:10:00Z',
    }],
    approvals: [], routines: [], loading: false, degraded: inboxState.failed, refetch: vi.fn(),
  }),
}));
vi.mock('../../staging/useStagedChange', () => ({
  useStagedChange: () => ({
    status: { kind: 'idle', state: 'pending' }, state: 'pending',
    busy: false, error: null, isTerminal: false, isBlessed: false, bless, revoke,
  }),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); inboxState.failed = false; });

describe('Inbox review', () => {
  it('does not claim nothing is waiting when an empty queue could not be read', () => {
    inboxState.failed = true;
    render(<ConfirmProvider><MemoryRouter><InboxView /></MemoryRouter></ConfirmProvider>);
    expect(screen.getByText(/Some items could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByText('Nothing waiting on you')).not.toBeInTheDocument();
  });
  it('reviews a sessionless proposal in place before approving through the shared controls', async () => {
    render(<ConfirmProvider><MemoryRouter><InboxView /></MemoryRouter></ConfirmProvider>);
    expect(screen.queryByRole('button', { name: /^Approve$/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review staged change' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Replace the draft with the reviewed note.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Approve$/ }));
    expect(bless).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
  });
});
