import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OntologyPage } from '../OntologyPage';
import { ontologyApi, type OntologyGraph } from '../../api/ontology';

const scopeState = vi.hoisted(() => ({ workspaceId: 'ws-a', userId: 'ordinary-user' }));
vi.mock('../../context/ScopeContext', () => ({ useScope: () => ({ scope: { workspaceId: scopeState.workspaceId }, activeWorkspace: { name: 'Research' } }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { id: scopeState.userId, roles: [] } }) }));
vi.mock('../../context/CrumbsContext', () => ({ useSetCrumbs: vi.fn() }));
vi.mock('../../api/ontology', () => ({ ontologyApi: { capture: vi.fn(), roots: vi.fn() } }));
vi.mock('../../features/ontology/OntologyCanvas', () => ({
  NODE_NAMES: { Workspace: 'Workspace', Track: 'Track', EntryType: 'Entry type' },
  OntologyCanvas: () => <div data-testid="ontology-canvas" />,
}));

function graph(workspace = 'ws-a', label = 'Decisions'): OntologyGraph {
  return {
    nodes: [
      { id: workspace, entity: 'Workspace', label: 'Research', context: {} },
      { id: `${workspace}-track`, entity: 'Track', label, context: {} },
      { id: `${workspace}-type`, entity: 'EntryType', label: 'Decision', context: {}, schema_fields: [{ key: 'summary', label: 'Summary', type: 'text', required: true }] },
    ],
    edges: [],
    meta: { workspace_id: workspace, root_id: workspace, max_depth: 3, complete: true, captured_at: '' },
  };
}

function renderPage() { return render(<MemoryRouter><OntologyPage /></MemoryRouter>); }

describe('workspace ontology', () => {
  beforeEach(() => {
    vi.clearAllMocks(); scopeState.workspaceId = 'ws-a'; scopeState.userId = 'ordinary-user';
    vi.mocked(ontologyApi.capture).mockResolvedValue(graph());
    vi.mocked(ontologyApi.roots).mockResolvedValue([]);
  });

  it('shows safe schema inspection for an ordinary user and supports keyboard selection', async () => {
    renderPage();
    await screen.findByTestId('ontology-canvas');
    fireEvent.click(screen.getByRole('button', { name: 'Decision · Entry type' }));
    expect(screen.getByText('Summary *')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter visible nodes' }), { target: { value: 'Decisions' } });
    expect(screen.queryByRole('button', { name: 'Decision · Entry type' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decisions · Track' })).toBeInTheDocument();
  });

  it('clears the old graph when a live expansion is denied', async () => {
    renderPage(); await screen.findByTestId('ontology-canvas');
    fireEvent.click(screen.getByRole('button', { name: 'Decisions · Track' }));
    vi.mocked(ontologyApi.capture).mockRejectedValueOnce(new Error('Access revoked'));
    fireEvent.click(screen.getByRole('button', { name: 'Browse from here' }));
    await screen.findByRole('alert');
    expect(screen.queryByTestId('ontology-canvas')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Decisions · Track' })).not.toBeInTheDocument();
    expect(ontologyApi.capture).toHaveBeenLastCalledWith('ws-a', 'ws-a-track', 3, expect.any(AbortSignal));
  });

  it('discards delayed old-workspace responses after switching', async () => {
    let resolveOld!: (value: OntologyGraph) => void;
    vi.mocked(ontologyApi.capture).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const page = renderPage();
    const oldSignal = vi.mocked(ontologyApi.capture).mock.calls[0][3];
    scopeState.workspaceId = 'ws-b';
    vi.mocked(ontologyApi.capture).mockResolvedValue(graph('ws-b', 'New workspace track'));
    page.rerender(<MemoryRouter><OntologyPage /></MemoryRouter>);
    await screen.findByRole('button', { name: 'New workspace track · Track' });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld(graph('ws-a', 'Stale private track')));
    expect(screen.queryByText('Stale private track')).not.toBeInTheDocument();
  });

  it('uses fresh authorized starting points and clears state on a principal change', async () => {
    vi.mocked(ontologyApi.roots).mockResolvedValue([{ id: 'shared-track', entity: 'Track', label: 'Directly shared' }]);
    const page = renderPage(); await screen.findByTestId('ontology-canvas');
    fireEvent.change(screen.getByRole('textbox', { name: 'Find a starting point' }), { target: { value: 'shared' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    await screen.findByRole('button', { name: 'Directly shared · Track' });
    expect(ontologyApi.roots).toHaveBeenCalledWith('ws-a', 'shared', expect.any(AbortSignal));
    scopeState.userId = 'second-user';
    page.rerender(<MemoryRouter><OntologyPage /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Directly shared · Track' })).not.toBeInTheDocument());
  });
});
