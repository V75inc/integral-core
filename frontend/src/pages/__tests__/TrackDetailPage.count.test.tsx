import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const track = { id: 't1', title: 'Work', collaborators: [], visibility: 'private' };
const entry = (n: number, type: string, tags: { id: string }[]) => ({
  id: `e${n}`, track_id: 't1', title: `Entry ${n}`, type, tags, custom_fields: {},
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
});
// The first page of a twelve-entry Track: two tasks and one tagged note.
const page = {
  entries: [entry(1, 'task', []), entry(2, 'task', [{ id: 'urgent' }]), entry(3, 'note', [{ id: 'urgent' }]), entry(4, 'note', [])],
  total: undefined, hasMore: true, nextCursor: 'more',
};

vi.mock('../../api', async original => ({
  ...(await original<typeof import('../../api')>()),
  tracksApi: {
    getDetail: async () => ({ track, collaborators: [] }),
    get: async () => track,
    getCollaboratorsDetailed: async () => ({ collaborators: [] }),
    getWatchers: async () => ({ watchers: [] }),
    getEntriesPage: async (_id: string, params: { cursor?: string }) =>
      params.cursor ? new Promise(() => {}) : page,
    getEntriesTotal: async () => 12,
    list: async () => [],
  },
  entryTypesApi: { list: async () => [] },
  trackViewsApi: { list: async () => [] },
}));
vi.mock('../../api/sharing', () => ({ sharingApi: { getPublicTrackSettings: async () => ({ enabled: false }) } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { id: 'u1' } }) }));
vi.mock('../../context/ScopeContext', () => ({
  useScope: () => ({ scope: { workspaceId: 'w1' }, setScope: vi.fn(), workspaces: [] }),
}));
vi.mock('../../context/ConfirmContext', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('../../context/ChatPageFocusContext', () => ({
  useChatPageFocus: () => ({ setPageContext: vi.fn(), clearPageContext: vi.fn(), setDialogContext: vi.fn() }),
}));
vi.mock('../../context/CrumbsContext', () => ({ useSetCrumbs: () => {} }));
vi.mock('../../hooks/useDeployment', () => ({ useDeployment: () => ({ collaboration: true }) }));
vi.mock('../../hooks/useRecents', () => ({ useRecents: () => ({ visit: vi.fn() }) }));
vi.mock('../../hooks/useWorkspaceCrumbPrefix', () => ({ useWorkspaceCrumbPrefix: () => [] }));
vi.mock('../../hooks/useRetrieve', () => ({
  useRetrieve: () => ({ results: [], loading: false, error: null, degraded: false, run: vi.fn(), reset: vi.fn() }),
}));
vi.mock('../../utils/entryEditRights', async original => ({
  ...(await original<typeof import('../../utils/entryEditRights')>()),
  useTrackPermissions: () => ({}),
}));
vi.mock('../../components/sidebar/PinButton', () => ({ PinButton: () => null }));
vi.mock('../../components/tracks/detail', async original => ({
  ...(await original<typeof import('../../components/tracks/detail')>()),
  TrackDetailViewChrome: () => null,
  TrackDetailRightRail: () => null,
  TrackCollaboratorsModal: () => null,
  // Stands in for the filter controls: the page owns the selections.
  TrackDetailMainColumn: (props: {
    filteredEntries: unknown[]; setFilterType: (v: string) => void; setFilterTags: (v: string[]) => void;
  }) => (
    <div>
      <span data-testid="rows">{props.filteredEntries.length}</span>
      <button type="button" onClick={() => props.setFilterType('task')}>Pick type</button>
      <button type="button" onClick={() => props.setFilterTags(['urgent'])}>Pick tag</button>
      <button type="button" onClick={() => { props.setFilterType(''); props.setFilterTags([]); }}>Clear</button>
    </div>
  ),
}));

import { TrackDetailPage } from '../TrackDetailPage';

class Observer { observe() {} unobserve() {} disconnect() {} }
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', Observer);
  vi.stubGlobal('IntersectionObserver', Observer);
});
afterAll(() => vi.unstubAllGlobals());
afterEach(cleanup);

describe('TrackDetailPage entry count on a paginated Track', () => {
  it('follows type and tag choices and returns to the whole count when cleared', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/tracks/t1']}>
          <Routes><Route path="/tracks/:id" element={<TrackDetailPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/12 entries/)).toBeInTheDocument());
    expect(screen.getByTestId('rows')).toHaveTextContent('4');

    fireEvent.click(screen.getByRole('button', { name: 'Pick type' }));
    expect(screen.getByText(/2 shown · 12 entries/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pick tag' }));
    expect(screen.getByText(/1 shown · 12 entries/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByText(/12 entries/)).not.toHaveTextContent('shown');
  });
});
