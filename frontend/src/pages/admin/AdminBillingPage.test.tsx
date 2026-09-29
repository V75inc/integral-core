import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { AdminBillingPage } from './AdminBillingPage';

const listSubscriptions = vi.fn();
const reconcile = vi.fn();
const setSubscription = vi.fn();
const listWorkspaces = vi.fn();

vi.mock('../../api/billing', () => ({
  billingApi: {
    listSubscriptions: (...args: unknown[]) => listSubscriptions(...args),
    reconcile: (...args: unknown[]) => reconcile(...args),
    setSubscription: (...args: unknown[]) => setSubscription(...args),
  },
}));

vi.mock('../../api/admin', () => ({
  adminApi: {
    listWorkspaces: (...args: unknown[]) => listWorkspaces(...args),
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
    listWorkspaces.mockReset();
    listWorkspaces.mockResolvedValue({
      workspaces: [
        {
          id: 'n.Workspace.1',
          kind: 'organization',
          name: 'Acme Corp',
          workspace_type: 'standard',
          member_count: 2,
          app_count: 1,
          track_count: 0,
        },
      ],
      total: 1,
      page: 1,
      per_page: 100,
      total_pages: 1,
      has_previous: false,
      has_next: false,
    });
  });

  it('renders friendly labels and workspace name', async () => {
    listSubscriptions.mockResolvedValue({
      total: 1,
      subscriptions: [
        {
          workspace_id: 'n.Workspace.1',
          workspace_name: 'Acme Corp',
          billing_account_id: 'ba:n.Workspace.1',
          status: 'trialing',
          plan_key: 'basic',
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
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    expect(screen.getAllByText('Free Trial').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Open')).toBeInTheDocument();
    expect(screen.getAllByText('Stripe').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Basic')).toBeInTheDocument();
    expect(screen.getByTestId('admin-billing-reconcile-help')).toHaveTextContent(
      'Refetch Stripe subscriptions',
    );
    expect(screen.getByTestId('admin-billing-grant')).toBeInTheDocument();
  });

  it('opens the grant plan form', async () => {
    const user = userEvent.setup();
    listSubscriptions.mockResolvedValue({ total: 0, subscriptions: [] });
    renderPage();

    expect(await screen.findByTestId('admin-billing-empty')).toBeInTheDocument();
    await user.click(screen.getByTestId('admin-billing-grant'));
    expect(
      await screen.findByTestId('admin-billing-grant-form'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('admin-billing-plan')).toBeInTheDocument();
    expect(screen.getByTestId('admin-billing-access-until')).toBeInTheDocument();
  });

  it('shows an empty state when nothing matches', async () => {
    listSubscriptions.mockResolvedValue({ total: 0, subscriptions: [] });
    renderPage();
    expect(await screen.findByTestId('admin-billing-empty')).toBeInTheDocument();
  });
});
