import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/workspaces', () => ({
  workspacesApi: {
    list: vi.fn().mockResolvedValue([
      {
        id: 'ws_1',
        name: 'Acme Co',
        kind: 'organization',
        your_role: 'owner',
        workspace_type: 'collaborative',
        plan_key: 'basic',
        plan_label: 'Basic',
      },
    ]),
  },
}));

vi.mock('../components/workspace/CreateWorkspaceModal', () => ({
  CreateWorkspaceModal: () => null,
}));

vi.mock('../context/CrumbsContext', () => ({
  useSetCrumbs: () => undefined,
}));

vi.mock('../hooks/usePublishPageContext', () => ({
  usePublishPageContext: () => undefined,
}));

import { WorkspacesPage } from './WorkspacesPage';

describe('WorkspacesPage plan badge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders plan badge from API plan_label', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <WorkspacesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText('Acme Co')).toBeInTheDocument();
    });
    expect(screen.getByText('Basic')).toBeInTheDocument();
  });
});
