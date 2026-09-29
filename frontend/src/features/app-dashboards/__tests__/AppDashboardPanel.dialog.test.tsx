import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

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
});
