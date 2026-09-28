import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { AdminBillingPage } from './AdminBillingPage';

const listSubscriptions = vi.fn();
const reconcile = vi.fn();
const setSubscription = vi.fn();

vi.mock('../../api/billing', () => ({
  billingApi: {
    listSubscriptions: (...args: unknown[]) => listSubscriptions(...args),
    reconcile: (...args: unknown[]) => reconcile(...args),
    setSubscription: (...args: unknown[]) => setSubscription(...args),
  },
}));

vi.mock('../../context/CrumbsContext', () => ({
  useSetCrumbs: () => undefined,
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AdminBillingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AdminBillingPage', () => {
  beforeEach(() => {
    listSubscriptions.mockReset();
    reconcile.mockReset();
    setSubscription.mockReset();
  });

  it('renders subscription rows from the list API', async () => {
    listSubscriptions.mockResolvedValue({
      total: 1,
      subscriptions: [
        {
          workspace_id: 'n.Workspace.1',
          billing_account_id: 'ba:n.Workspace.1',
          status: 'active',
          plan_key: 'base',
          source: 'stripe',
          external_customer_id: 'cus_1',
          external_subscription_id: 'sub_1',
          access: 'open',
          updated_at: '2026-09-28T12:00:00+00:00',
        },
      ],
    });

    renderPage();

    expect(await screen.findByTestId('admin-billing-table')).toBeInTheDocument();
    expect(
      screen.getByTestId('admin-billing-row-n.Workspace.1'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('admin-billing-reconcile')).toBeInTheDocument();
    expect(
      screen.getByTestId('admin-billing-override-n.Workspace.1'),
    ).toBeInTheDocument();
  });

  it('shows an empty state when nothing matches', async () => {
    listSubscriptions.mockResolvedValue({ total: 0, subscriptions: [] });
    renderPage();
    expect(await screen.findByTestId('admin-billing-empty')).toBeInTheDocument();
  });
});
