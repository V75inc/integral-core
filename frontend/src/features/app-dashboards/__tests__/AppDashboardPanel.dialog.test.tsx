import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

const { listMock, substrateMock, dataMock, drillThroughMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  substrateMock: vi.fn(),
  dataMock: vi.fn(),
  drillThroughMock: vi.fn(),
}));

vi.mock('../../../api/dashboards', () => ({
  dashboardsApi: {
    list: listMock,
    getSubstrate: substrateMock,
    getData: dataMock,
    drillThrough: drillThroughMock,
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    suggest: vi.fn(),
  },
}));

vi.mock('react-grid-layout/legacy', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../DashboardWidgetRegistry', () => ({
  DashboardWidgetRenderer: ({ onDrillThrough }: { onDrillThrough?: (key?: string) => void }) => (
    <button type="button" onClick={() => onDrillThrough?.('active')}>
      Open chart records
    </button>
  ),
  groupWidgetTypes: () => ({}),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

vi.mock('../../../context/ChatPageFocusContext', () => ({
  useChatPageFocus: () => ({ setPageContext: vi.fn() }),
}));

import { AppDashboardPanel } from '../AppDashboardPanel';

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

describe('AppDashboardPanel drill-through dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
      unobserve() {}
    });
    listMock.mockResolvedValue([
      {
        id: 'dashboard-1',
        name: 'Revenue',
        app_id: 'app-1',
        layout: { columns: 12, row_height: 80 },
        widgets: [
          {
            id: 'chart-1',
            type: 'chart_bar',
            title: 'Revenue chart',
            grid: { x: 0, y: 0, w: 6, h: 4 },
            config: {},
            data_source: { kind: 'aggregate', op: 'count' },
          },
        ],
        is_default: true,
      },
    ]);
    substrateMock.mockResolvedValue({ widget_types: [] });
    dataMock.mockResolvedValue({ 'chart-1': {} });
    drillThroughMock.mockResolvedValue({
      items: [],
      result_set_id: 'result-1',
      graph_revision: 'revision-1',
      membership_limit: 100,
      membership_scope: {},
      calculation: { op: 'count' },
      refreshed_at: '2026-09-28T00:00:00Z',
      current_widget_value: 0,
    });
  });

  it('uses the standard padded Modal.Body for chart record results', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <AppDashboardPanel appId="app-1" canEdit={false} />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Open chart records' }));
    const calculation = await screen.findByText(/Calculation: count/);
    const modalBody = calculation.parentElement;
    await waitFor(() => expect(drillThroughMock).toHaveBeenCalled());

    expect(modalBody).toHaveClass('px-5', 'sm:px-6', 'py-5', 'space-y-4');
  });

  it('shows the API reason when governed drill-through is refused', async () => {
    drillThroughMock.mockRejectedValueOnce({
      response: {
        data: {
          error_code: 'query_boundary.denied',
          message: 'This App requires a declared query capability',
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <AppDashboardPanel appId="app-1" canEdit={false} />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Open chart records' }));

    expect(
      await screen.findByText(
        'Could not open this result set: This App requires a declared query capability',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/AxiosError/)).not.toBeInTheDocument();
  });

  it('continues with the governed cursor and appends the next page', async () => {
    drillThroughMock
      .mockResolvedValueOnce({
        items: [{ id: 'entry-1', title: 'Invoice 1', track_id: 'track-1' }],
        result_set_id: 'result-1',
        graph_revision: 'revision-1',
        membership_limit: 1,
        membership_scope: {},
        calculation: { op: 'count' },
        refreshed_at: '2026-09-28T00:00:00Z',
        current_widget_value: 2,
        total_estimate: 2,
        next_cursor: 'cursor-2',
      })
      .mockResolvedValueOnce({
        items: [{ id: 'entry-2', title: 'Invoice 2', track_id: 'track-1' }],
        result_set_id: 'result-2',
        graph_revision: 'revision-1',
        membership_limit: 1,
        membership_scope: {},
        calculation: { op: 'count' },
        refreshed_at: '2026-09-28T00:00:00Z',
        current_widget_value: 2,
        total_estimate: 2,
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <AppDashboardPanel appId="app-1" canEdit={false} />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Open chart records' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Load next page' }));

    expect(await screen.findByText('Invoice 2')).toBeInTheDocument();
    expect(await screen.findByText(/All matching records have been loaded/)).toBeInTheDocument();
    expect(drillThroughMock).toHaveBeenNthCalledWith(2, 'app-1', 'dashboard-1', {
      widget_id: 'chart-1',
      group_key: 'active',
      cursor: 'cursor-2',
    });
  });

  it('opens the source Track with the dashboard group filter preserved', async () => {
    drillThroughMock.mockResolvedValueOnce({
      items: [{ id: 'entry-1', title: 'Invoice A', track_id: 'track-1' }],
      result_set_id: 'result-1',
      graph_revision: 'revision-1',
      membership_limit: 100,
      membership_scope: {},
      calculation: { op: 'count' },
      refreshed_at: '2026-09-28T00:00:00Z',
      current_widget_value: 1,
      total_estimate: 1,
      loaded_count: 1,
      track_navigation: {
        track_id: 'track-1',
        filters: [{ field: 'status', op: 'eq', value: 'active' }],
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <MemoryRouter>
        <LocationProbe />
        <QueryClientProvider client={queryClient}>
          <AppDashboardPanel appId="app-1" canEdit={false} />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Open chart records' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open filtered Track' }));

    const location = await screen.findByTestId('location');
    expect(location.textContent).toContain('/tracks/track-1?');
    const query = location.textContent?.split('?')[1] ?? '';
    expect(new URLSearchParams(query).get('dashboard_filters')).toBe(
      JSON.stringify([{ field: 'status', op: 'eq', value: 'active' }]),
    );
  });
});
