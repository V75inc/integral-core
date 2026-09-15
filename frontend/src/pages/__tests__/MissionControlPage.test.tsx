import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockGetSnapshot = vi.fn();

vi.mock('../../api', async importOriginal => {
  const actual = await importOriginal<typeof import('../../api')>();
  return {
    ...actual,
    missionControlApi: {
      ...actual.missionControlApi,
      getSnapshot: (preview_limit?: number) => mockGetSnapshot(preview_limit),
    },
  };
});

vi.mock('../../context/ScopeContext', async importOriginal => {
  const actual =
    await importOriginal<typeof import('../../context/ScopeContext')>();
  return {
    ...actual,
    useScope: () => ({
      scope: { workspaceId: 'ws-1' },
      workspaces: [
        {
          id: 'ws-1',
          kind: 'organization',
          name: 'Workspace One',
          accent_color: '#2244ff',
          avatar_url: null,
        },
      ],
      setScope: vi.fn(),
    }),
  };
});

vi.mock('../../context/CrumbsContext', () => ({
  useSetCrumbs: () => vi.fn(),
}));

vi.mock('../../hooks/useNotifications', () => ({
  useNotifications: () => ({
    notifications: [],
    unreadCount: 9,
    loading: false,
    error: null,
    refetch: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
    isMarkingRead: false,
    isMarkingAllRead: false,
  }),
}));

vi.mock('../../components/system', () => ({
  notifyApiFailure: vi.fn(),
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

import { MissionControlPage } from '../MissionControlPage';

afterEach(() => {
  cleanup();
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MissionControlPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function metricValue(label: string): string {
  const section = screen.getByRole('region', { name: /at a glance/i });
  const labelNode = within(section).getByText(label);
  const card = labelNode.closest('div, a');
  const valueNode = card?.querySelector('p:nth-of-type(2)');
  return valueNode?.textContent?.trim() ?? '';
}

describe('<MissionControlPage /> counters', () => {
  beforeEach(() => {
    mockGetSnapshot.mockReset();
  });

  it('renders counters from the server-aggregated mission-control snapshot', async () => {
    const today = new Date().toISOString();
    mockGetSnapshot.mockResolvedValue({
      workspaces: [],
      apps: [],
      tracks: [
        {
          id: 't-1',
          title: 'Track One',
          workspace_id: 'ws-1',
          updated_at: '2026-05-22T12:00:00Z',
          created_at: '2026-05-20T12:00:00Z',
          entry_count: 5,
        },
      ],
      preview_entries: [
        { id: 'e-p1', track_id: 't-1', created_at: today, title: 'Preview only' },
      ],
      entries_today: 2,
      active_tracks: 2,
    });

    renderPage();

    await waitFor(() => {
      expect(metricValue('Entries today')).toBe('2');
    });
    expect(metricValue('Active tracks')).toBe('2');
    expect(metricValue('Unread')).toBe('9');
    expect(mockGetSnapshot).toHaveBeenCalledWith(50);
  });
});
