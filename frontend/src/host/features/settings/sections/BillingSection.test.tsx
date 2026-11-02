import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { BillingSection } from './BillingSection';

const checkout = vi.fn();
const checkoutCreditPack = vi.fn();
const changePlan = vi.fn();
const portal = vi.fn();
const getStatus = vi.fn();
const getCatalog = vi.fn();

vi.mock('../../../api/billing', () => ({
  billingApi: {
    getStatus: (...args: unknown[]) => getStatus(...args),
    getCatalog: (...args: unknown[]) => getCatalog(...args),
    checkout: (...args: unknown[]) => checkout(...args),
    checkoutCreditPack: (...args: unknown[]) => checkoutCreditPack(...args),
    changePlan: (...args: unknown[]) => changePlan(...args),
    portal: (...args: unknown[]) => portal(...args),
    getAiUsage: vi.fn(async () => ({
      workspace_id: 'n.Workspace.demo',
      used: 12,
      limit: 100,
      remaining: 88,
      weekly_remaining: 88,
      purchased_remaining: 0,
      effective_remaining: 88,
      window_days: 7,
      percent: 12,
      is_unlimited: false,
      is_soft_warning: false,
      is_exhausted: false,
      weekly_exhausted: false,
      is_using_purchased: false,
      enforcement_enabled: true,
      soft_warn_threshold: 0.8,
      meter: 'ai_credits',
      unit: 'credits',
    })),
  },
}));

vi.mock('../../../../context/ScopeContext', () => ({
  useScope: () => ({
    scope: { workspaceId: 'n.Workspace.demo' },
    activeWorkspace: { id: 'n.Workspace.demo', name: 'Demo Workspace', kind: 'organization' },
  }),
}));

vi.mock('../../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

const confirmMock = vi.fn();
vi.mock('../../../../context/ConfirmContext', () => ({
  useConfirm: () => confirmMock,
}));

function catalogFixture(overrides: Record<string, unknown> = {}) {
  return {
    any_plan_configured: true,
    any_credit_pack_configured: true,
    portal_available: false,
    has_subscription: false,
    current_plan_key: null,
    free_ai_credits_per_7d: 100,
    credit_packs: [
      {
        key: 'credits_2_5k',
        title: '2,500 credits',
        credits: 2500,
        display_price_usd: 24.99,
        badge: '',
        price_configured: true,
      },
      {
        key: 'credits_5k',
        title: '5,000 credits',
        credits: 5000,
        display_price_usd: 39.99,
        badge: '',
        price_configured: true,
      },
      {
        key: 'credits_20k',
        title: '20,000 credits',
        credits: 20000,
        display_price_usd: 119.99,
        badge: '',
        price_configured: true,
      },
    ],
    plans: [
      {
        key: 'basic',
        title: 'Basic',
        description: 'CRM and payroll for growing teams.',
        rank: 10,
        price_configured: true,
        apps: ['crm', 'payroll-guyana'],
        ai_credits_per_7d: 3000,
      },
      {
        key: 'premium',
        title: 'Premium',
        description: 'Everything in Basic, plus Sales.',
        rank: 20,
        price_configured: true,
        apps: ['crm', 'payroll-guyana', 'sales'],
        ai_credits_per_7d: 10000,
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
        slug: 'payroll-guyana',
        entitlement_key: 'payroll-guyana',
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
    checkoutCreditPack.mockReset();
    changePlan.mockReset();
    portal.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('shows Free as current when there is no subscription yet', async () => {
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
    expect(screen.getByTestId('settings-billing-workspace')).toHaveTextContent(
      'Demo Workspace',
    );
    expect(screen.getByTestId('settings-billing-start-premium')).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-plan-free')).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-plan-free')).toHaveTextContent(
      '100 weekly credits',
    );
    expect(screen.getByTestId('settings-billing-plan-free')).toHaveTextContent(
      'Documents',
    );
    expect(screen.getByTestId('settings-billing-plan-basic')).toHaveTextContent(
      'Documents',
    );
    expect(screen.getByTestId('settings-billing-plan-basic')).toHaveTextContent(
      'Organization',
    );
    expect(screen.getByTestId('settings-billing-plan-basic')).toHaveTextContent(
      '3,000 weekly credits',
    );
    expect(screen.getByTestId('settings-billing-plan-premium')).toHaveTextContent(
      'Documents',
    );
    expect(screen.getByTestId('settings-billing-plan-premium')).toHaveTextContent(
      '10,000 weekly credits',
    );
    expect(screen.getByTestId('settings-billing-current-free')).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-access')).toHaveTextContent(
      'Free',
    );
    expect(screen.queryByTestId('settings-billing-plan')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'AI usage' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Buy Additional AI credits' })).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-credit-packs')).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-buy-credits_2_5k')).toHaveTextContent(
      'US$24.99',
    );
    expect(screen.getByTestId('settings-billing-credit-pack-credits_2_5k')).toHaveTextContent(
      '2,500 credits',
    );
    expect(screen.getByTestId('settings-billing-credit-pack-credits_2_5k')).toHaveTextContent(
      '/ credit',
    );
    expect(
      screen.getByTestId('settings-billing-credit-pack-credits_20k'),
    ).toHaveTextContent('20,000 credits');
    expect(screen.getByTestId('settings-billing-buy-credits_20k')).toHaveTextContent(
      'US$119.99',
    );
    const plansHeading = screen.getByRole('heading', { name: 'Plans' });
    const subHeading = screen.getByRole('heading', { name: 'Your subscription' });
    expect(
      plansHeading.compareDocumentPosition(subHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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
    expect(screen.queryByTestId('settings-billing-cancel')).not.toBeInTheDocument();
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

  it('shows pending cancellation and period end', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'open',
      workspace_id: 'n.Workspace.demo',
      status: 'trialing',
      plan_key: 'basic',
      source: 'stripe',
      checkout_available: true,
      cancel_at_period_end: true,
      current_period_end: '2026-10-13T00:00:00+00:00',
    });
    getCatalog.mockResolvedValue(
      catalogFixture({
        portal_available: true,
        has_subscription: true,
        current_plan_key: 'basic',
      }),
    );

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-access'),
    ).toHaveTextContent('Canceling');
    expect(screen.getByTestId('settings-billing-period')).toHaveTextContent(
      /Cancels/,
    );
  });

  it('shows switch-to-Basic when on Premium', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'open',
      workspace_id: 'n.Workspace.demo',
      status: 'active',
      plan_key: 'premium',
      source: 'stripe',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(
      catalogFixture({
        portal_available: true,
        has_subscription: true,
        current_plan_key: 'premium',
        apps: catalogFixture().apps.map(row => ({ ...row, entitled: true })),
      }),
    );

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-downgrade-basic'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-current-premium')).toBeInTheDocument();
  });

  it('confirms before changing plan and skips when cancelled', async () => {
    const user = userEvent.setup();
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
    changePlan.mockResolvedValue({ message: 'Plan updating' });

    renderSection();
    const upgrade = await screen.findByTestId('settings-billing-upgrade-premium');

    confirmMock.mockResolvedValueOnce(false);
    await user.click(upgrade);
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(changePlan).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    await user.click(upgrade);
    await waitFor(() => {
      expect(changePlan).toHaveBeenCalledWith('n.Workspace.demo', 'premium');
    });
    expect(confirmMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        title: 'Upgrade to Premium?',
        confirmLabel: 'Upgrade to Premium',
      }),
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
    expect(screen.getByText(/Commercial apps still require workspace access/)).toBeInTheDocument();
    expect(screen.getByText(/Ask your workspace administrator/)).toBeInTheDocument();
    expect(screen.queryByText(/Apps install without a paywall/)).not.toBeInTheDocument();
    expect(checkout).not.toHaveBeenCalled();
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

  it('starts credit pack checkout', async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(catalogFixture());
    checkoutCreditPack.mockResolvedValue({
      url: 'https://checkout.example/credits',
    });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    renderSection();
    await user.click(await screen.findByTestId('settings-billing-buy-credits_5k'));

    await waitFor(() => {
      expect(checkoutCreditPack).toHaveBeenCalledWith(
        'n.Workspace.demo',
        'credits_5k',
      );
      expect(assign).toHaveBeenCalledWith('https://checkout.example/credits');
    });
    vi.unstubAllGlobals();
  });

  it('renders commercial apps as compact badges and expands/collapses when overflow exists', async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    const customCatalog = catalogFixture({
      plans: [
        {
          key: 'basic',
          title: 'Basic',
          description: 'Basic plan',
          rank: 10,
          price_configured: true,
          apps: ['crm', 'payroll-guyana'],
          ai_credits_per_7d: 3000,
        },
        {
          key: 'premium',
          title: 'Premium',
          description: 'Premium plan with many apps',
          rank: 20,
          price_configured: true,
          apps: ['crm', 'payroll-guyana', 'sales', 'finance', 'recruitment'],
          ai_credits_per_7d: 10000,
        },
      ],
      apps: [
        { slug: 'crm', entitlement_key: 'crm', title: 'CRM', min_plan: 'basic', entitled: false },
        { slug: 'payroll-guyana', entitlement_key: 'payroll-guyana', title: 'Guyana Payroll', min_plan: 'basic', entitled: false },
        { slug: 'sales', entitlement_key: 'sales', title: 'Sales', min_plan: 'premium', entitled: false },
        { slug: 'finance', entitlement_key: 'finance', title: 'Finance', min_plan: 'premium', entitled: false },
        { slug: 'recruitment', entitlement_key: 'recruitment', title: 'Recruitment', min_plan: 'premium', entitled: false },
      ],
    });
    getCatalog.mockResolvedValue(customCatalog);

    renderSection();

    // Basic has 2 apps, so no overflow toggle
    const basicApps = await screen.findByTestId('settings-billing-apps-basic');
    expect(basicApps).toHaveTextContent('Commercial apps (2)');
    expect(basicApps).toHaveTextContent('CRM');
    expect(basicApps).toHaveTextContent('Guyana Payroll');
    expect(screen.queryByTestId('settings-billing-apps-toggle-basic')).not.toBeInTheDocument();

    // Premium has 5 apps: initially shows 3 + toggle button (+2 more)
    const premiumApps = screen.getByTestId('settings-billing-apps-premium');
    expect(premiumApps).toHaveTextContent('Commercial apps (5)');
    expect(premiumApps).toHaveTextContent('CRM');
    expect(premiumApps).toHaveTextContent('Guyana Payroll');
    expect(premiumApps).toHaveTextContent('Sales');
    expect(premiumApps).not.toHaveTextContent('Finance');
    expect(premiumApps).not.toHaveTextContent('Recruitment');

    const toggle = screen.getByTestId('settings-billing-apps-toggle-premium');
    expect(toggle).toHaveTextContent('+2 more');

    // Click to expand
    await user.click(toggle);
    expect(premiumApps).toHaveTextContent('Finance');
    expect(premiumApps).toHaveTextContent('Recruitment');
    expect(toggle).toHaveTextContent('Show less');

    // Click to collapse
    await user.click(toggle);
    expect(premiumApps).not.toHaveTextContent('Finance');
    expect(premiumApps).not.toHaveTextContent('Recruitment');
    expect(toggle).toHaveTextContent('+2 more');
  });

  it('hides plan changes and shows Coming soon for credit packs', async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'open',
      workspace_id: 'n.Workspace.demo',
      status: 'active',
      plan_key: 'basic',
      checkout_available: false,
    });
    getCatalog.mockResolvedValue(
      catalogFixture({
        portal_available: true,
        has_subscription: true,
        current_plan_key: 'basic',
        plan_changes_supported: false,
        credit_packs_purchasable: false,
      }),
    );

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-current-basic'),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId('settings-billing-upgrade-premium'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('settings-billing-start-premium'),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId('settings-billing-buy-credits_2_5k'));
    expect(
      screen.getByTestId('settings-billing-coming-soon-credits_2_5k'),
    ).toHaveTextContent('Coming soon');
    expect(checkoutCreditPack).not.toHaveBeenCalled();
  });

  it('still offers Start when plan changes are unsupported and there is no subscription', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue(
      catalogFixture({ plan_changes_supported: false }),
    );

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-start-basic'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-start-premium')).toBeInTheDocument();
  });
});
