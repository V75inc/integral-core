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
  const currentKey = catalog?.current_plan_key || status.plan_key || null;
  const currentPlan = planByKey(catalog, currentKey);
  const graceLabel = formatGrace(status.grace_until);
  const hasSubscription = Boolean(catalog?.has_subscription);

  return (
    <div className="flex flex-col gap-6" data-testid="settings-billing">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1 max-w-2xl">
          Choose a plan to unlock commercial Apps. Documents and Organization
          stay free. Card details are entered on Stripe — access updates when
          payment confirms.
        </Text>
      </div>

      <SettingsSection
        title="Account"
        description={
          status.access === 'grace' && graceLabel
            ? `Payment is past due. Access stays open until ${graceLabel}.`
            : hasSubscription
              ? currentPlan
                ? `You are on ${currentPlan.title}.`
                : 'Your Stripe billing account is connected.'
              : 'Pick Basic or Premium below to start a subscription.'
        }
      >
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <Text variant="body" tone="subtle" as="dt">
            Status
          </Text>
          <Text variant="body" as="dd">
            <span data-testid="settings-billing-access">
              {hasSubscription ? status.status || status.access : 'No plan'}
            </span>
          </Text>
          {currentPlan ? (
            <>
              <Text variant="body" tone="subtle" as="dt">
                Plan
              </Text>
              <Text variant="body" as="dd">
                <span data-testid="settings-billing-plan">
                  {currentPlan.title}
                </span>
              </Text>
            </>
          ) : null}
          {status.source ? (
            <>
              <Text variant="body" tone="subtle" as="dt">
                Source
              </Text>
              <Text variant="body" as="dd">
                {status.source}
              </Text>
            </>
          ) : null}
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          {catalog?.portal_available ? (
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
        description="Basic includes CRM and Guyana Payroll. Premium adds Sales. Upgrade any time; cancel or downgrade in the Stripe portal."
      >
        {catalogQuery.isPending ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : plans.length === 0 ? (
          <Text variant="body" tone="muted" as="p">
            No plans are configured for this cell.
          </Text>
        ) : (
          <ul className="grid gap-4 md:grid-cols-2">
            {plans.map(plan => {
              const isCurrent =
                hasSubscription &&
                (currentKey || '').toLowerCase() === plan.key;
              const currentRank = currentPlan?.rank ?? -1;
              const isUpgrade =
                hasSubscription && plan.rank > currentRank && plan.price_configured;
              const isLower =
                hasSubscription && plan.rank < currentRank;
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
                        Included in {currentPlan?.title || 'your plan'}. Manage
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
