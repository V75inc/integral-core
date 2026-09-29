import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const setScope = vi.fn();
const navigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => ({ workspaceId: 'ws_1' }),
  };
});

vi.mock('../api', () => ({
  workspacesApi: {
    get: vi.fn().mockResolvedValue({
      id: 'ws_1',
      name: 'Acme',
      kind: 'organization',
      your_role: 'owner',
      owner_user_id: 'user-1',
      plan_key: 'premium',
      plan_label: 'Premium',
    }),
    delete: vi.fn(),
    update: vi.fn(),
  },
  appsApi: {
    list: vi.fn().mockResolvedValue([]),
  },
  tracksApi: {
    list: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', user_id: 'user-1' },
  }),
}));

vi.mock('../context/ScopeContext', () => ({
  useScope: () => ({
    scope: { workspaceId: 'ws_other' },
    setScope,
    workspaces: [
      {
        id: 'ws_1',
        name: 'Acme',
        kind: 'organization',
        your_role: 'owner',
        plan_key: 'premium',
        plan_label: 'Premium',
      },
    ],
  }),
}));

vi.mock('../context/CrumbsContext', () => ({
  useSetCrumbs: () => undefined,
}));

vi.mock('../hooks/usePublishPageContext', () => ({
  usePublishPageContext: () => undefined,
}));

import { WorkspaceDetailPage } from './WorkspaceDetailPage';

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/workspaces/ws_1']}>
        <Routes>
          <Route path="/workspaces/:workspaceId" element={<WorkspaceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('WorkspaceDetailPage plan UX', () => {
  beforeEach(() => {
    setScope.mockReset();
    navigate.mockReset();
  });

  it('shows plan badge and Manage plan for admins', async () => {
    renderPage();
    expect(await screen.findByText('Premium')).toBeInTheDocument();
    const manage = await screen.findByTestId('workspace-manage-plan');
    await userEvent.click(manage);
    expect(setScope).toHaveBeenCalledWith({ workspaceId: 'ws_1' });
    expect(navigate).toHaveBeenCalledWith('/settings#billing');
  });
});
