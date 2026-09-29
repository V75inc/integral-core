/**
 * Workspace billing — Basic / Premium plan tiers via Stripe.
 *
 * Free Apps (Documents, Organization) install without billing. Commercial
 * Apps unlock with the workspace's plan. Card entry stays on Stripe
 * Checkout / Portal.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { Link } from 'react-router-dom';

import { billingApi } from '../../../api/billing';
import { errorMessageFromAxios } from '../../../api/helpers';
import {
  appBySlug,
  appDisplayName,
  planByKey,
  type BillingPlan,
  type BillingStatus,
} from '../../../components/apps/billingAccess';
import { Button } from '../../../components/ui/Button';
import { Pill } from '../../../components/ui/Pill';
import { Skeleton } from '../../../components/ui/Skeleton';
import { useScope } from '../../../context/ScopeContext';
import { useToast } from '../../../context/ToastContext';
import { SettingsSection } from '../components/Field';
import { Text } from '../../../ui';

function billingQueryKey(workspaceId: string) {
  return ['billing', 'workspace', workspaceId] as const;
}

function formatGrace(graceUntil: string | null | undefined): string | null {
  if (!graceUntil) return null;
  try {
    const when = new Date(graceUntil);
    if (Number.isNaN(when.getTime())) return graceUntil;
    return when.toLocaleString();
  } catch {
    return graceUntil;
  }
}

const STATUS_COPY: Record<
  string,
  { label: string; pill: 'success' | 'warning' | 'neutral' | 'danger' }
> = {
  trialing: { label: 'Free trial', pill: 'success' },
  active: { label: 'Active', pill: 'success' },
  past_due: { label: 'Past due', pill: 'warning' },
  incomplete: { label: 'Setup incomplete', pill: 'neutral' },
  canceled: { label: 'Canceled', pill: 'neutral' },
  unpaid: { label: 'Unpaid', pill: 'danger' },
};

function statusPresentation(status: BillingStatus | null | undefined): {
  label: string;
  pill: 'success' | 'warning' | 'neutral' | 'danger';
} {
  const raw = (status?.status || status?.access || '').trim().toLowerCase();
  if (raw && STATUS_COPY[raw]) return STATUS_COPY[raw];
  if (status?.access === 'grace') {
    return { label: 'Grace period', pill: 'warning' };
  }
  if (status?.access === 'locked') {
    return { label: 'Locked', pill: 'danger' };
  }
  if (raw) {
    return {
      label: raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      pill: 'neutral',
    };
  }
  return { label: 'No plan', pill: 'neutral' };
}

function normalizePlanKey(key: string | null | undefined): string {
  const raw = (key || '').trim().toLowerCase();
  if (!raw || raw === 'base') return '';
  return raw;
}

function planTitle(
  catalogPlans: BillingPlan[],
  key: string | null | undefined,
): string | null {
  const normalized = normalizePlanKey(key);
  if (!normalized) return null;
  const fromCatalog = catalogPlans.find(row => row.key === normalized);
  if (fromCatalog?.title) return fromCatalog.title;
  return normalized.replace(/\b\w/g, c => c.toUpperCase());
}

function liveSubscription(status: BillingStatus | null | undefined): boolean {
  const raw = (status?.status || '').trim().toLowerCase();
  return raw === 'trialing' || raw === 'active' || raw === 'past_due';
}

export function BillingSection() {
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId || '';
  const toast = useToast();
  const qc = useQueryClient();

  const statusQuery = useQuery({
    queryKey: [...billingQueryKey(workspaceId), 'status'],
    queryFn: () => billingApi.getStatus(workspaceId),
    enabled: Boolean(workspaceId),
    retry: false,
  });

  const catalogQuery = useQuery({
    queryKey: [...billingQueryKey(workspaceId), 'catalog'],
    queryFn: () => billingApi.getCatalog(workspaceId),
    enabled:
      Boolean(workspaceId) &&
      statusQuery.isSuccess &&
      Boolean(statusQuery.data?.subscription_required),
    retry: false,
  });

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: billingQueryKey(workspaceId) });

  const checkoutMut = useMutation({
    mutationFn: (planKey: string) => billingApi.checkout(workspaceId, planKey),
    onSuccess: result => {
      if (result?.url) {
        window.location.assign(result.url);
        return;
      }
      toast.showToast('Checkout did not return a URL', 'error');
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not start checkout'),
        'error',
      );
    },
  });

  const changePlanMut = useMutation({
    mutationFn: (planKey: string) => billingApi.changePlan(workspaceId, planKey),
    onSuccess: result => {
      if (result?.url) {
        window.location.assign(result.url);
        return;
      }
      toast.showToast(
        result?.message ||
          'Payment will confirm this plan. Access updates when the webhook arrives.',
        'success',
      );
      invalidate();
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not change plan'),
        'error',
      );
    },
  });

  const portalMut = useMutation({
    mutationFn: () => billingApi.portal(workspaceId),
    onSuccess: result => {
      if (result?.url) {
        window.location.assign(result.url);
        return;
      }
      toast.showToast('Billing portal did not return a URL', 'error');
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not open billing portal'),
        'error',
      );
    },
  });

  const busy =
    checkoutMut.isPending ||
    changePlanMut.isPending ||
    portalMut.isPending;

  if (!workspaceId) {
    return (
      <Text variant="body" tone="muted" as="p">
        Select a workspace to manage billing.
      </Text>
    );
  }

  if (statusQuery.isPending) {
    return (
      <div className="flex flex-col gap-4" data-testid="settings-billing-loading">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (statusQuery.isError) {
    const message = errorMessageFromAxios(
      statusQuery.error,
      'Could not load billing status',
    );
    const denied =
      /admin|owner|permission|denied|403/i.test(message) ||
      (statusQuery.error as { response?: { status?: number } })?.response
        ?.status === 403;
    return (
      <div data-testid="settings-billing-denied">
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-2">
          {denied
            ? 'Billing is managed by workspace admins and owners.'
            : message}
        </Text>
      </div>
    );
  }

  const status = statusQuery.data;
  const catalog = catalogQuery.data ?? null;

  if (!status?.subscription_required) {
    return (
      <div data-testid="settings-billing-off">
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-2">
          Billing is off for this deployment. Apps install without a paywall.
        </Text>
      </div>
    );
  }

  const plans = catalog?.plans ?? [];
  const currentKey = normalizePlanKey(
    catalog?.current_plan_key || status.plan_key,
  );
  const currentPlan = planByKey(catalog, currentKey);
  const currentTitle = planTitle(plans, currentKey);
  const currentRank = currentPlan?.rank ?? (currentKey ? 0 : -1);
  const graceLabel = formatGrace(status.grace_until);
  const hasSubscription =
    Boolean(catalog?.has_subscription) || liveSubscription(status);
  const statusView = statusPresentation(hasSubscription ? status : null);
  const portalAvailable = Boolean(catalog?.portal_available);

  return (
    <div className="flex flex-col gap-6" data-testid="settings-billing">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1 max-w-2xl">
          Choose a plan to unlock commercial Apps. Documents and Organization
          stay free. Card details stay on Stripe — access updates when payment
          confirms.
        </Text>
      </div>

      <SettingsSection
        title="Your subscription"
        description={
          status.access === 'grace' && graceLabel
            ? `Payment is past due. Access stays open until ${graceLabel}.`
            : hasSubscription && currentTitle
              ? `You are on ${currentTitle}${
                  (status.status || '').toLowerCase() === 'trialing'
                    ? ' with a free trial'
                    : ''
                }.`
              : hasSubscription
                ? 'Your Stripe subscription is connected. Pick or change a plan below.'
                : 'Pick Basic or Premium below to start a subscription.'
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Pill variant={statusView.pill} tone="descriptive">
            <span data-testid="settings-billing-access">{statusView.label}</span>
          </Pill>
          {currentTitle ? (
            <Pill variant="neutral" tone="descriptive">
              <span data-testid="settings-billing-plan">{currentTitle}</span>
            </Pill>
          ) : null}
          {hasSubscription ? (
            <Text variant="meta" tone="subtle" as="span">
              Billed with Stripe
            </Text>
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {portalAvailable ? (
            <Button
              variant="secondary"
              size="sm"
              loading={portalMut.isPending}
              disabled={busy}
              onClick={() => portalMut.mutate()}
              data-testid="settings-billing-portal"
            >
              Payment methods & invoices
            </Button>
          ) : null}
          <Link
            to="/apps"
            className="inline-flex items-center justify-center rounded-[var(--radius-input)] px-3 py-1.5 text-sm font-medium text-[var(--text-muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
          >
            Manage apps
          </Link>
        </div>
      </SettingsSection>

      <SettingsSection
        title="Plans"
        description="Basic includes CRM and Guyana Payroll. Premium adds Sales. Upgrade here any time; cancel or downgrade in the Stripe portal."
      >
        {catalogQuery.isPending ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : plans.length === 0 ? (
          <Text variant="body" tone="muted" as="p">
            No plans are configured for this cell. Rebuild the API with the
            billing catalog mounted, then refresh.
          </Text>
        ) : (
          <ul className="grid gap-4 md:grid-cols-2">
            {plans.map(plan => {
              const isCurrent =
                hasSubscription && currentKey === plan.key;
              const isUpgrade =
                hasSubscription &&
                Boolean(currentKey) &&
                plan.rank > currentRank &&
                plan.price_configured;
              const isLower =
                hasSubscription &&
                Boolean(currentKey) &&
                plan.rank < currentRank;
              const appNames = plan.apps
                .map(slug => {
                  const app = appBySlug(catalog, slug);
                  return app ? appDisplayName(app) : slug;
                })
                .filter(Boolean);

              return (
                <li
                  key={plan.key}
                  className={[
                    'relative flex flex-col rounded-[var(--radius-card)] border p-5',
                    isCurrent
                      ? 'border-[var(--accent)] bg-[var(--panel)] shadow-[0_0_0_1px_var(--accent)]'
                      : 'border-[var(--border-subtle)] bg-[var(--panel-2)]',
                  ].join(' ')}
                  data-testid={`settings-billing-plan-${plan.key}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <Text variant="heading-sm" weight="semibold" as="h3">
                        {plan.title}
                      </Text>
                      {plan.description ? (
                        <Text
                          variant="body-sm"
                          tone="muted"
                          as="p"
                          className="mt-1"
                        >
                          {plan.description}
                        </Text>
                      ) : null}
                    </div>
                    {isCurrent ? (
                      <Pill variant="success" tone="descriptive">
                        Current
                      </Pill>
                    ) : null}
                  </div>
                  <ul className="mt-5 flex flex-col gap-2.5">
                    {appNames.map(label => (
                      <li key={label} className="flex items-start gap-2">
                        <Check
                          size={16}
                          strokeWidth={2}
                          className="mt-0.5 shrink-0 text-[var(--accent)]"
                          aria-hidden
                        />
                        <Text variant="body-sm" as="span">
                          {label}
                        </Text>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-auto pt-6">
                    {!plan.price_configured ? (
                      <Text variant="meta" tone="subtle" as="p">
                        Price not configured
                      </Text>
                    ) : isCurrent ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled
                        data-testid={`settings-billing-current-${plan.key}`}
                      >
                        Current plan
                      </Button>
                    ) : isLower ? (
                      <Text variant="meta" tone="subtle" as="p">
                        Included in {currentTitle || 'your plan'}. Manage
                        downgrades in the billing portal.
                      </Text>
                    ) : isUpgrade ? (
                      <Button
                        variant="primary"
                        size="sm"
                        loading={
                          changePlanMut.isPending &&
                          changePlanMut.variables === plan.key
                        }
                        disabled={busy}
                        onClick={() => changePlanMut.mutate(plan.key)}
                        data-testid={`settings-billing-upgrade-${plan.key}`}
                      >
                        Upgrade to {plan.title}
                      </Button>
                    ) : hasSubscription ? (
                      <Button
                        variant={plan.key === 'premium' ? 'primary' : 'secondary'}
                        size="sm"
                        loading={
                          changePlanMut.isPending &&
                          changePlanMut.variables === plan.key
                        }
                        disabled={busy || !plan.price_configured}
                        onClick={() => changePlanMut.mutate(plan.key)}
                        data-testid={`settings-billing-choose-${plan.key}`}
                      >
                        Choose {plan.title}
                      </Button>
                    ) : (
                      <Button
                        variant={plan.key === 'premium' ? 'primary' : 'secondary'}
                        size="sm"
                        loading={
                          checkoutMut.isPending &&
                          checkoutMut.variables === plan.key
                        }
                        disabled={busy}
                        onClick={() => checkoutMut.mutate(plan.key)}
                        data-testid={`settings-billing-start-${plan.key}`}
                      >
                        Start {plan.title}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>
    </div>
  );
}
