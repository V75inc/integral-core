import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const navigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      user_id: 'user-1',
      display_name: 'Ada',
      email: 'ada@example.com',
    },
    logout: vi.fn(),
  }),
}));

vi.mock('../../context/ScopeContext', () => ({
  useScope: () => ({
    scope: { workspaceId: 'ws_1' },
    setScope: vi.fn(),
    workspaces: [
      {
        id: 'ws_1',
        name: 'Acme',
        kind: 'organization',
        plan_key: 'free',
        plan_label: 'Free',
      },
    ],
    activeWorkspace: {
      id: 'ws_1',
      name: 'Acme',
      kind: 'organization',
      plan_key: 'free',
      plan_label: 'Free',
    },
    isPersonal: false,
  }),
}));

vi.mock('../../hooks/usePlatformAdmin', () => ({
  usePlatformAdmin: () => ({ isAdmin: false }),
}));

vi.mock('../../hooks/usePinned', () => ({
  usePinned: () => ({
    pinned: { tracks: [], apps: [] },
    isPinned: () => false,
    togglePin: vi.fn(),
  }),
}));

vi.mock('../../features/ai-chat', () => ({
  useWorkspacesWithRunningTurns: () => new Set(),
}));

vi.mock('./WorkspaceSwitcher', () => ({
  WorkspaceSwitcher: () => <div data-testid="workspace-switcher" />,
}));

vi.mock('../../api', () => ({
  appsApi: { list: vi.fn().mockResolvedValue([]) },
  tracksApi: { list: vi.fn().mockResolvedValue([]) },
}));

import { Sidebar } from './Sidebar';

describe('Sidebar account menu billing entry', () => {
  beforeEach(() => {
    navigate.mockReset();
  });

  it('offers Manage subscriptions above Sign out', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Sidebar
            collapsed={false}
            onToggleCollapsed={vi.fn()}
            mobileOpen={false}
            onMobileOpenChange={vi.fn()}
            isDesktop
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByLabelText(/Ada menu/i));
    expect(
      await screen.findByTestId('account-manage-subscriptions'),
    ).toHaveTextContent('Manage subscriptions');
    const signOut = screen.getByRole('menuitem', { name: /Sign out/i });
    const manage = screen.getByTestId('account-manage-subscriptions');
    expect(
      manage.compareDocumentPosition(signOut) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await userEvent.click(manage);
    expect(navigate).toHaveBeenCalledWith('/settings#billing');
  });
});
