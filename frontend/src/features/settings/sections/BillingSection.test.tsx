import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { BillingSection } from './BillingSection';

const checkout = vi.fn();
const portal = vi.fn();
const getStatus = vi.fn();
const getCatalog = vi.fn();

vi.mock('../../../api/billing', () => ({
  billingApi: {
    getStatus: (...args: unknown[]) => getStatus(...args),
    getCatalog: (...args: unknown[]) => getCatalog(...args),
    checkout: (...args: unknown[]) => checkout(...args),
    portal: (...args: unknown[]) => portal(...args),
    addAddon: vi.fn(),
  },
}));

vi.mock('../../../context/ScopeContext', () => ({
  useScope: () => ({ scope: { workspaceId: 'n.Workspace.demo' } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

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
    portal.mockReset();
  });

  it('shows Set up billing when there is no subscription yet', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue({
      base_configured: true,
      portal_available: false,
      has_subscription: false,
      addons: [],
    });

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-subscribe'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('settings-billing-access')).toHaveTextContent(
      'Not set up',
    );
  });

  it('shows Manage billing when the portal is available', async () => {
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'open',
      workspace_id: 'n.Workspace.demo',
      status: 'active',
      plan_key: 'base',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue({
      base_configured: true,
      portal_available: true,
      has_subscription: true,
      addons: [
        {
          slug: 'documents',
          entitlement_key: 'documents',
          title: 'Documents',
          description: 'Knowledge library',
          requires: [],
          entitled: false,
          price_configured: true,
        },
      ],
    });

    renderSection();

    expect(
      await screen.findByTestId('settings-billing-portal'),
    ).toBeInTheDocument();
    expect(
      await screen.findByTestId('settings-billing-add-documents'),
    ).toBeInTheDocument();
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

  it('starts checkout from Set up billing', async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue({
      subscription_required: true,
      access: 'locked',
      workspace_id: 'n.Workspace.demo',
      checkout_available: true,
    });
    getCatalog.mockResolvedValue({
      base_configured: true,
      portal_available: false,
      has_subscription: false,
      addons: [],
    });
    checkout.mockResolvedValue({ url: 'https://checkout.example/session' });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    renderSection();
    await user.click(await screen.findByTestId('settings-billing-subscribe'));

    await waitFor(() => {
      expect(checkout).toHaveBeenCalledWith('n.Workspace.demo');
      expect(assign).toHaveBeenCalledWith('https://checkout.example/session');
    });
    vi.unstubAllGlobals();
  });
});
