import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';

import { StagedChatApprovals } from '../StagedChatApprovals';
import { ConfirmProvider } from '../../../context/ConfirmContext';
import { listPendingStagedChanges } from '../../../api/agentive';

const staged = {
  _kind: 'staged_change' as const,
  token: 'staged-1',
  kind: 'entry.create',
  summary: 'Create vehicle inspection for car 14',
  diff_human: 'Create an inspection',
  diff_machine: {},
  state: 'pending' as const,
  created_at: '2026-09-22T00:00:00Z',
  expires_at: '2026-09-23T00:00:00Z',
  autonomy_grant_used: false,
};

let state: 'pending' | 'blessed' = 'pending';

vi.mock('../../../api/agentive', () => ({
  listPendingStagedChanges: vi.fn(async () => [staged]),
}));

vi.mock('../../../features/ai-chat/staging/useStagedChange', () => ({
  useStagedChange: () => ({
    busy: false,
    error: null,
    state,
    status: { kind: 'idle', state },
    isBlessed: state === 'blessed',
    isTerminal: false,
    bless: vi.fn(),
    revoke: vi.fn(),
  }),
}));

afterEach(() => {
  state = 'pending';
  cleanup();
});

describe('StagedChatApprovals status language', () => {
  it('makes authorization distinct from a completed write', async () => {
    state = 'blessed';
    vi.mocked(listPendingStagedChanges).mockResolvedValueOnce([{ ...staged, state: 'blessed' }]);
    render(
      <ConfirmProvider><MemoryRouter>
        <StagedChatApprovals />
      </MemoryRouter></ConfirmProvider>,
    );

    expect(
      await screen.findByText(
        (_content, element) =>
          element?.tagName === 'P' &&
          element.textContent?.includes(
            'Authorized · waiting for the agent to apply it',
          ) === true,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Authorize staged change' }),
    ).not.toBeInTheDocument();
  });

  it('labels pending work as awaiting authorization', async () => {
    render(
      <ConfirmProvider><MemoryRouter>
        <StagedChatApprovals />
      </MemoryRouter></ConfirmProvider>,
    );

    expect(
      await screen.findByText(/Awaiting authorization · staged/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Review staged change' }),
    ).toBeInTheDocument();
  });

  it('opens the exact change locally before authorization, without a chat session', async () => {
    render(<ConfirmProvider><MemoryRouter><StagedChatApprovals /></MemoryRouter></ConfirmProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Review staged change' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Create an inspection')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Approve$/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Review in chat' })).not.toBeInTheDocument();
  });

  it('refreshes an already-open page when a proposal appears', async () => {
    vi.mocked(listPendingStagedChanges).mockResolvedValueOnce([]);
    render(<ConfirmProvider><MemoryRouter><StagedChatApprovals /></MemoryRouter></ConfirmProvider>);
    await new Promise(resolve => setTimeout(resolve, 0));
    fireEvent(window, new Event('integral:staging-created'));
    expect(await screen.findByRole('button', { name: 'Review staged change' })).toBeInTheDocument();
  });

  it('keeps known proposals visible when a refresh fails', async () => {
    render(<ConfirmProvider><MemoryRouter><StagedChatApprovals /></MemoryRouter></ConfirmProvider>);
    expect(await screen.findByRole('button', { name: 'Review staged change' })).toBeInTheDocument();
    vi.mocked(listPendingStagedChanges).mockRejectedValueOnce(new Error('unavailable'));
    fireEvent(window, new Event('integral:staging-state-changed'));
    expect(await screen.findByRole('status')).toHaveTextContent('could not be refreshed');
    expect(screen.getByRole('button', { name: 'Review staged change' })).toBeInTheDocument();
  });
});
