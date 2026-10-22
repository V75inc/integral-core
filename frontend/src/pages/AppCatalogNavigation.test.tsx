import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OperationalModelsPage } from './OperationalModelsPage';
import OperationalModelDetailPage from './OperationalModelDetailPage';

const profiles = [
  { id: 'app-model', name: 'Installable App', manifest: { scope: 'app', app: { tracks: [] } } },
  { id: 'track-model', name: 'Reusable Track', manifest: { scope: 'track', track: { entry_types: [] } } },
];
vi.mock('../api', () => ({
  operationalModelsApi: { list: vi.fn(async () => profiles), get: vi.fn(async () => profiles[0]) },
  tracksApi: { list: vi.fn(async () => []) }, appsApi: { list: vi.fn(async () => []) },
}));
vi.mock('../context/CrumbsContext', () => ({ useSetCrumbs: vi.fn() }));
vi.mock('../context/ScopeContext', () => ({ useScope: () => ({ scope: { workspaceId: 'ws-1' } }) }));
vi.mock('../context/ConfirmContext', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('../hooks/usePlatformAdmin', () => ({ usePlatformAdmin: () => false }));
vi.mock('../hooks/usePublishPageContext', () => ({ usePublishPageContext: vi.fn() }));
vi.mock('../components/library/ImportPackageModal', () => ({ ImportPackageModal: () => null }));

function show(path: string) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={[path]}><Routes>
      <Route path="/models" element={<OperationalModelsPage />} />
      <Route path="/models/:id" element={<OperationalModelDetailPage />} />
      <Route path="/apps" element={<div>Canonical App catalog</div>} />
    </Routes></MemoryRouter>
  </QueryClientProvider>);
}

describe('single App catalog', () => {
  it('keeps only reusable Track models in model tools', async () => {
    show('/models');
    expect(await screen.findByText('Reusable Track')).toBeInTheDocument();
    expect(screen.queryByText('Installable App')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse Apps' })).toHaveAttribute('href', '/apps?manage=1');
  });
  it('redirects old App details to the canonical App catalog', async () => {
    show('/models/app-model');
    expect(await screen.findByText('Canonical App catalog')).toBeInTheDocument();
  });
});
