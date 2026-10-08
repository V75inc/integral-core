import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppHomePanel } from '../AppHomePanel';
import { dashboardsApi } from '../../../api/dashboards';
import { requestOpenCompanionChat } from '../../ai-chat/chatHandoff';

vi.mock('../../../context/ScopeContext', () => ({ useScope: () => ({ scope: { workspaceId: 'ws1' } }) }));
vi.mock('../../../api/dashboards', () => ({ dashboardsApi: { home: vi.fn() } }));
vi.mock('../../ai-chat/chatHandoff', () => ({ requestOpenCompanionChat: vi.fn() }));

const home = {
  title: 'Current work', description: 'One step at a time.',
  actions: [
    { label: 'Start', draft: 'Help me get started.', when: { widget: 'current', state: 'empty' } },
    { label: 'Continue', draft: 'Help with my next step.', when: { widget: 'current', state: 'has_records' } },
  ],
  widgets: [{ id: 'current', type: 'record_summary', title: 'Next step', grid: { x: 0, y: 0, w: 12, h: 5 }, config: { fields: [{ field: 'step', label: 'Step' }] }, data_source: {}, data: { records: [], total_matched: 0 } }],
};
function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AppHomePanel appId="a1" workspaceId="ws1" /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => vi.clearAllMocks());
describe('AppHomePanel', () => {
  it('shows the empty-state action and prepares a draft without sending it', async () => {
    vi.mocked(dashboardsApi.home).mockResolvedValue({ home } as never);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Start' }));
    expect(requestOpenCompanionChat).toHaveBeenCalledWith({ draftText: 'Help me get started.' });
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument();
    expect(screen.getByText('Next step').closest('.app-home-widget')).toHaveStyle({ '--home-widget-column': '1 / span 12', '--home-widget-row': '1 / span 5' });
  });
  it('shows the next-step action for existing records', async () => {
    vi.mocked(dashboardsApi.home).mockResolvedValue({ home: { ...home, widgets: [{ ...home.widgets[0], data: { records: [{ id: 'n.Entry.1', title: 'Saved work', fields: { step: 'Review' } }], total_matched: 1 } }] } } as never);
    mount();
    expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
    expect(screen.getByText('Review')).toBeInTheDocument();
  });
  it('does not treat a failed query as an empty start', async () => {
    vi.mocked(dashboardsApi.home).mockResolvedValue({ home: { ...home, widgets: [{ ...home.widgets[0], data: { error: 'home_query_unavailable' } }] } } as never);
    mount();
    expect(await screen.findByText("We couldn’t read this information right now.")).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument();
  });
});
