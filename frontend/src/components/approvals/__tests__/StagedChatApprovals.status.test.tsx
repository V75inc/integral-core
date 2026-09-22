import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';

import { StagedChatApprovals } from '../StagedChatApprovals';

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
    render(
      <MemoryRouter>
        <StagedChatApprovals />
      </MemoryRouter>,
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
      <MemoryRouter>
        <StagedChatApprovals />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/Awaiting authorization · staged/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Authorize staged change' }),
    ).toBeInTheDocument();
  });
});
