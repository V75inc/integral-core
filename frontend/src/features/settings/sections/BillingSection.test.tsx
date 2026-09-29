import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { BillingSection } from './BillingSection';

const checkout = vi.fn();
const changePlan = vi.fn();
const portal = vi.fn();
const getStatus = vi.fn();
const getCatalog = vi.fn();

vi.mock('../../../api/billing', () => ({
  billingApi: {
    getStatus: (...args: unknown[]) => getStatus(...args),
    getCatalog: (...args: unknown[]) => getCatalog(...args),
    checkout: (...args: unknown[]) => checkout(...args),
    changePlan: (...args: unknown[]) => changePlan(...args),
    portal: (...args: unknown[]) => portal(...args),
  },
}));

vi.mock('../../../context/ScopeContext', () => ({
  useScope: () => ({ scope: { workspaceId: 'n.Workspace.demo' } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

function catalogFixture(overrides: Record<string, unknown> = {}) {
  return {
    any_plan_configured: true,
    portal_available: false,
    has_subscription: false,
    current_plan_key: null,
    plans: [
      {
        key: 'basic',
        title: 'Basic',
        description: 'CRM and payroll for growing teams.',
        rank: 10,
        price_configured: true,
        apps: ['crm', 'guyana-payroll'],
      },
      {
        key: 'premium',
        title: 'Premium',
        description: 'Everything in Basic, plus Sales.',
        rank: 20,
        price_configured: true,
        apps: ['crm', 'guyana-payroll', 'sales'],
      },
    ],
    apps: [
      {
        slug: 'crm',
        entitlement_key: 'crm',
        title: 'CRM',
        min_plan: 'basic',
        entitled: false,
      },
      {
        slug: 'sales',
        entitlement_key: 'sales',
        title: 'Sales',
        min_plan: 'premium',
        entitled: false,
      },
      {
        slug: 'guyana-payroll',
        entitlement_key: 'guyana-payroll',
        title: 'Guyana Payroll',
        min_plan: 'basic',
        entitled: false,
      },
    ],
    ...overrides,
  };
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BillingSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('BillingSection', () => {
  beforeEach(() => {
    getStatus.mockReset();
    getCatalog.mockReset();
    checkout.mockReset();
    changePlan.mockReset();
    portal.mockReset();
  });

  it('shows plan cards when there is no subscription yet', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(catalogFixture());

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-start-basic'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-start-premium')).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-access')).toHaveTextContent(
      'No plan',
    );
  });

  it('shows upgrade when on Basic and portal is available', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'open',
      workspace_id: 'n.Workspace.demo',
      status: 'trialing',
      plan_key: 'basic',
      source: 'stripe',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(
      catalogFixture({
        portal_available: true,
        has_subscription: true,
        current_plan_key: 'basic',
        apps: catalogFixture().apps.map(row =>
          row.min_plan === 'basic' ? { ...row, entitled: true } : row,
        ),
      }),
    );

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-portal'),
    ).toBeInTheDocument();
    expect(
      await screen.findByTestId('settings-billing-upgrade-premium'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-plan')).toHaveTextContent(
      'Basic',
    );
    expect(screen.getByTestId('settings-billing-access')).toHaveTextContent(
      'Free trial',
    );
  });

  it('explains when billing is off', async () => {
    getStatus.mockResolvedValue({
      subscription_required: false,
      access: 'off',
      workspace_id: 'n.Workspace.demo',
    });

    renderSection();

    expect(await screen.findByTestId('settings-billing-off')).toBeInTheDocument();
  });

  it('starts checkout for Basic', async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(catalogFixture());
    checkout.mockResolvedValue({ url: 'https://checkout.example/session' });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    renderSection();
    await user.click(await screen.findByTestId('settings-billing-start-basic'));

    await waitFor(() => {
      expect(checkout).toHaveBeenCalledWith('n.Workspace.demo', 'basic');
      expect(assign).toHaveBeenCalledWith('https://checkout.example/session');
    });
    vi.unstubAllGlobals();
  });
});
