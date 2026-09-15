/**
 * AgentsSection — tests for the M3c-2 "Connected agents" extension:
 *   - the MCP endpoint URL renders (contains `/api/mcp`)
 *   - clicking Copy writes the URL to the clipboard
 *   - connected-agent rows render from the mocked list()
 *   - Revoke → confirm → revoke(clientId) → list invalidated/refetched
 *   - empty state when list() returns []
 *
 * The resident-harness provider listing (header + provider rows) renders
 * statically — no agent-discovery fetch (retired, ADR-003).
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  render,
  screen,
  waitFor,
  fireEvent,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// ── Mocks ────────────────────────────────────────────────────────────
vi.mock('../../../api/connectedAgents', () => ({
  connectedAgentsApi: {
    list: vi.fn().mockResolvedValue([]),
    revoke: vi.fn().mockResolvedValue({ revoked: 1 }),
  },
}));

// Capability probe → enabled (so the section renders without the warning).
vi.mock('../hooks/useAgentiveCapability', () => ({
  useAgentiveCapability: () => ({ enabled: true, isLoading: false }),
}));

// Auto-confirm any destructive prompt.
const confirmMock = vi.fn().mockResolvedValue(true);
vi.mock('../../../context/ConfirmContext', () => ({
  useConfirm: () => confirmMock,
}));

// Toast — capture calls, otherwise no-op.
const showToastMock = vi.fn();
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    showToast: showToastMock,
    showPendingToast: vi.fn(),
    resolveToast: vi.fn(),
    dismissToast: vi.fn(),
  }),
}));

// Settings store — provide a minimal snapshot + no-op updater.
vi.mock('../store', () => ({
  useSettings: () => [
    { providers: { defaultProviderId: 'jvagent-embedded' } },
    vi.fn(),
  ],
}));

import { connectedAgentsApi } from '../../../api/connectedAgents';
import { AgentsSection } from './AgentsSection';

const mockedList = connectedAgentsApi.list as unknown as ReturnType<
  typeof vi.fn
>;
const mockedRevoke = connectedAgentsApi.revoke as unknown as ReturnType<
  typeof vi.fn
>;

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AgentsSection />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedList.mockResolvedValue([]);
  mockedRevoke.mockResolvedValue({ revoked: 1 });
  confirmMock.mockResolvedValue(true);
});

describe('AgentsSection — existing provider listing', () => {
  it('renders the section header', () => {
    renderPanel();
    expect(
      screen.getByRole('heading', { name: 'Agent', level: 2 }),
    ).toBeInTheDocument();
  });
});

describe('AgentsSection — MCP endpoint + connected agents', () => {
  it('renders the MCP endpoint URL containing /api/mcp', async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/\/api\/mcp/)).toBeInTheDocument();
    });
  });

  it('copies the MCP URL to the clipboard on Copy click', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderPanel();
    const copyBtn = await screen.findByRole('button', {
      name: /copy mcp endpoint url/i,
    });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    expect(writeText.mock.calls[0][0]).toContain('/api/mcp');
  });

  it('renders connected-agent rows from list()', async () => {
    mockedList.mockResolvedValue([
      {
        client_id: 'cid-1',
        client_name: 'Claude Desktop',
        scopes: ['mcp:read', 'mcp:write'],
        granted_at: '2026-06-01T00:00:00Z',
      },
    ]);
    renderPanel();
    expect(await screen.findByText('Claude Desktop')).toBeInTheDocument();
    expect(screen.getByText(/mcp:read/)).toBeInTheDocument();
    expect(screen.getByText(/mcp:write/)).toBeInTheDocument();
  });

  it('shows the empty state when list() returns []', async () => {
    mockedList.mockResolvedValue([]);
    renderPanel();
    expect(
      await screen.findByText(/no connected agents yet/i),
    ).toBeInTheDocument();
  });

  it('revokes a connected agent after confirmation and refetches', async () => {
    mockedList.mockResolvedValue([
      {
        client_id: 'cid-1',
        client_name: 'Claude Desktop',
        scopes: ['mcp:read'],
        granted_at: '2026-06-01T00:00:00Z',
      },
    ]);
    renderPanel();

    const revokeBtn = await screen.findByRole('button', {
      name: /revoke access for claude desktop/i,
    });
    fireEvent.click(revokeBtn);

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(mockedRevoke).toHaveBeenCalledWith('cid-1');
    });
    // list() refetched after invalidation (initial + post-revoke).
    await waitFor(() => {
      expect(mockedList.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });
});
