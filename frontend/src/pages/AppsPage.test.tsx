import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppsPage } from './AppsPage';

vi.mock('../components/apps/AppModal', () => ({
  AppModal: () => null,
}));

vi.mock('../components/apps/AppManagerDialog', () => ({
  AppManagerDialog: () => null,
}));

vi.mock('../api', () => ({
  appsApi: {
    list: vi.fn().mockResolvedValue([]),
  },
  workspacesApi: {
    list: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../context/CrumbsContext', () => ({
  useSetCrumbs: vi.fn(),
}));

const mockUseScope = vi.fn();
vi.mock('../context/ScopeContext', () => ({
  useScope: () => mockUseScope(),
}));

const mockUseWorkspaceCreationRights = vi.fn();
vi.mock('../hooks/useWorkspaceCreationRights', () => ({
  useWorkspaceCreationRights: () => mockUseWorkspaceCreationRights(),
}));

vi.mock('../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AppsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AppsPage creation rights gating', () => {
  beforeEach(() => {
    mockUseScope.mockReturnValue({
      activeWorkspace: {
        id: 'ws-org',
        kind: 'organization',
        name: 'Acme',
        your_role: 'member',
      },
    });
  });

  it('hides Manage apps when the caller cannot create apps', async () => {
    mockUseWorkspaceCreationRights.mockReturnValue({
      canCreateApps: false,
      canCreateTracks: false,
      lacksAppCreationInOrg: true,
      lacksTrackCreationInOrg: true,
    });

    renderPage();

    expect(screen.queryByRole('button', { name: /manage apps/i })).toBeNull();
    expect(
      await screen.findByText(/doesn't include permission to create apps/i),
    ).toBeInTheDocument();
  });

  it('shows Manage apps when the caller can create apps', async () => {
    mockUseWorkspaceCreationRights.mockReturnValue({
      canCreateApps: true,
      canCreateTracks: true,
      lacksAppCreationInOrg: false,
      lacksTrackCreationInOrg: false,
    });

    renderPage();

    expect(
      await screen.findByRole('button', { name: /manage apps/i }),
    ).toBeInTheDocument();
  });
});
