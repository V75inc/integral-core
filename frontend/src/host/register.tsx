/**
 * Business commercial UI registration — overlays Core's empty host stub
 * (`src/host/register.tsx` at image build). Fills settings / admin /
 * composer accessory / install-denial mapping.
 *
 * Upgrade UX is driven by mapping Core's generic ``entitlement.required``
 * denial to a billing CTA — Core does not host plan chrome.
 */
import React, { lazy } from 'react';
import { CreditCard } from 'lucide-react';

import {
  registerAdminNavItem,
  registerAdminRoute,
  registerComposerAccessory,
  registerInstallDenialResolver,
  registerLayoutBanner,
  registerSettingsSection,
  registerSidebarAccountAction,
} from './registry';
import { resolveBusinessInstallDenial } from './components/apps/installDenial';
import { billingApi } from './api/billing';
import { AiQuotaBanner } from './components/layout/AiQuotaBanner';
import { useScope } from '../context/ScopeContext';

const BillingSection = lazy(() =>
  import('./features/settings/sections/BillingSection').then(m => ({
    default: m.BillingSection,
  })),
);
const AdminBillingPage = lazy(() =>
  import('./pages/admin/AdminBillingPage').then(m => ({
    default: m.AdminBillingPage,
  })),
);

function livePaidPlan(status: { status?: string | null } | null): boolean {
  const raw = (status?.status || '').trim().toLowerCase();
  return raw === 'trialing' || raw === 'active' || raw === 'past_due';
}

function BillingSidebarLabel() {
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId;
  const [label, setLabel] = React.useState('Upgrade plan');

  React.useEffect(() => {
    let cancelled = false;
    if (!workspaceId) {
      setLabel('Upgrade plan');
      return;
    }
    billingApi
      .status(workspaceId)
      .then(status => {
        if (cancelled) return;
        if (!status?.subscription_required) {
          setLabel(status?.access_mode === 'full' ? 'Full access' : 'Billing');
          return;
        }
        setLabel(livePaidPlan(status) ? 'Manage plan' : 'Upgrade plan');
      })
      .catch(() => {
        if (!cancelled) setLabel('Upgrade plan');
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  return <>{label}</>;
}

function ComposerQuotaHint({ workspaceId }: { workspaceId: string }) {
  const [usage, setUsage] = React.useState<Awaited<
    ReturnType<typeof billingApi.getAiUsage>
  > | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    billingApi
      .getAiUsage(workspaceId)
      .then(u => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        if (!cancelled) setUsage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  if (!usage || usage.is_unlimited || usage.is_exhausted) return null;
  if (usage.is_using_purchased) {
    const left = Math.max(0, Number(usage.purchased_remaining || 0));
    return (
      <div
        className="
          mb-2 rounded-[var(--radius-card)] border border-[var(--border-subtle)]
          px-3 py-2 text-xs text-[var(--text-muted)]
        "
        data-testid="composer-ai-purchased-hint"
      >
        Using purchased credits ({left.toLocaleString()} left).{' '}
        <a
          href="/settings#billing-credit-packs"
          className="font-medium underline underline-offset-2"
        >
          Top up
        </a>
      </div>
    );
  }
  if (!usage.is_soft_warning) return null;

  return (
    <div
      className="
        mb-2 rounded-[var(--radius-card)] border border-[var(--warn-fg)]/40
        px-3 py-2 text-xs text-[var(--warn-fg)]
      "
      data-testid="composer-ai-quota-hint"
    >
      Approaching the rolling AI credit limit ({Math.round(usage.percent)}
      %).{' '}
      <a
        href="/settings#billing"
        className="font-medium underline underline-offset-2"
      >
        View usage
      </a>
    </div>
  );
}

// Settings section
registerSettingsSection({
  id: 'billing',
  label: 'Billing',
  icon: CreditCard,
  render: () => <BillingSection />,
});

// Admin
registerAdminNavItem({
  to: '/admin/billing',
  label: 'Billing',
  icon: CreditCard,
  end: false,
});
registerAdminRoute({
  path: 'billing',
  element: <AdminBillingPage />,
});

// Layout banner
registerLayoutBanner(AiQuotaBanner);

// Composer hint
registerComposerAccessory(ComposerQuotaHint);

// Map both single-request and per-app batch denials to Business access guidance.
registerInstallDenialResolver(err =>
  resolveBusinessInstallDenial(err, () => {
    window.location.assign('/settings#billing');
  }),
);

registerSidebarAccountAction({
  id: 'billing',
  label: 'Upgrade plan',
  Label: BillingSidebarLabel,
  icon: CreditCard,
  testId: 'account-manage-subscriptions',
  onSelect: () => {
    window.location.assign('/settings#billing');
  },
});
