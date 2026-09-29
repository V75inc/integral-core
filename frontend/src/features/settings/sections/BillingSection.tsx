/**
 * Workspace billing — Free / Basic / Premium tiers via Stripe.
 *
 * Free Apps (Documents, Organization) install without billing. Commercial
 * Apps unlock with Basic or Premium. Card entry stays on Stripe Checkout /
 * Portal; cancel also uses the Portal.
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
import { formatDateFieldDisplay } from '../../../utils/dateFieldValue';

const FREE_APPS = ['Documents', 'Organization'] as const;

function billingQueryKey(workspaceId: string) {
  return ['billing', 'workspace', workspaceId] as const;
}

function formatGrace(graceUntil: string | null | undefined): string | null {
  if (!graceUntil) return null;
  const label = formatDateFieldDisplay(graceUntil, 'datetime');
  return label || null;
}

function formatPeriodDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const label = formatDateFieldDisplay(iso, 'date');
  return label || null;
}

function periodCopy(status: BillingStatus | null | undefined): string | null {
  if (!status || !liveSubscription(status)) return null;
  const when = formatPeriodDate(status.current_period_end);
  if (!when) return null;
  if (status.cancel_at_period_end) {
    return `Cancels ${when}`;
  }
  const raw = (status.status || '').trim().toLowerCase();
  if (raw === 'trialing') {
    return `Trial ends ${when}`;
  }
  return `Renews ${when}`;
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

function statusPresentation(
  status: BillingStatus | null | undefined,
  onFree: boolean,
): { label: string; pill: 'success' | 'warning' | 'neutral' | 'danger' } {
  if (onFree) {
    return { label: 'Free', pill: 'neutral' };
  }
  if (status?.cancel_at_period_end && liveSubscription(status)) {
    return { label: 'Canceling', pill: 'warning' };
  }
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
  return { label: 'Free', pill: 'neutral' };
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

function PlanFeatureList({ labels }: { labels: string[] }) {
  return (
    <ul className="mt-5 flex flex-col gap-2.5">
      {labels.map(label => (
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
  );
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
          'Your plan is updating. This usually takes a moment.',
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
  const onFree = !hasSubscription;
  const statusView = statusPresentation(status, onFree);
  const portalAvailable = Boolean(catalog?.portal_available);
  const displayPlanTitle = onFree ? 'Free' : currentTitle;
  const periodLabel = periodCopy(status);

  return (
    <div className="flex flex-col gap-6" data-testid="settings-billing">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1 max-w-2xl">
          Free includes Documents and Organization. Upgrade to unlock commercial
          Apps. Card details stay on Stripe — access updates when payment
          confirms.
        </Text>
      </div>

      <SettingsSection
        title="Your subscription"
        description={
          status.access === 'grace' && graceLabel
            ? `Payment is past due. Access stays open until ${graceLabel}.`
            : status.cancel_at_period_end && periodLabel
              ? `Cancellation is scheduled. You keep access until then — reopen the billing portal to undo.`
              : onFree
                ? 'You are on Free. Documents and Organization install without a paid plan.'
                : currentTitle
                  ? `You are on ${currentTitle}${
                      (status.status || '').toLowerCase() === 'trialing'
                        ? ' with a free trial'
                        : ''
                    }.`
                  : 'Your Stripe subscription is connected. Change plans below.'
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          {onFree ? (
            <Pill variant="neutral" tone="descriptive">
              <span data-testid="settings-billing-access">Free</span>
            </Pill>
          ) : (
            <>
              <Pill variant={statusView.pill} tone="descriptive">
                <span data-testid="settings-billing-access">
                  {statusView.label}
                </span>
              </Pill>
              {displayPlanTitle ? (
                <Pill variant="neutral" tone="descriptive">
                  <span data-testid="settings-billing-plan">
                    {displayPlanTitle}
                  </span>
                </Pill>
              ) : null}
            </>
          )}
          {periodLabel ? (
            <span
              className="text-[12px] leading-[16px] font-normal text-[var(--text-subtle)]"
              data-testid="settings-billing-period"
            >
              {periodLabel}
            </span>
          ) : hasSubscription ? (
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
              Manage billing
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
        description="Upgrade or switch paid tiers here. Cancel a paid subscription in the Stripe portal."
      >
        {catalogQuery.isPending ? (
          <div className="grid gap-4 md:grid-cols-3">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : (
          <ul className="grid gap-4 md:grid-cols-3">
            <li
              className={[
                'relative flex flex-col rounded-[var(--radius-card)] border p-5',
                onFree
                  ? 'border-[var(--accent)] bg-[var(--panel)] shadow-[0_0_0_1px_var(--accent)]'
                  : 'border-[var(--border-subtle)] bg-[var(--panel-2)]',
              ].join(' ')}
              data-testid="settings-billing-plan-free"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Text variant="heading-sm" weight="semibold" as="h3">
                    Free
                  </Text>
                  <Text variant="body-sm" tone="muted" as="p" className="mt-1">
                    Community Apps with no card required.
                  </Text>
                </div>
                {onFree ? (
                  <Pill variant="success" tone="descriptive">
                    Current
                  </Pill>
                ) : null}
              </div>
              <PlanFeatureList labels={[...FREE_APPS]} />
              <div className="mt-auto pt-6">
                {onFree ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled
                    data-testid="settings-billing-current-free"
                  >
                    Current plan
                  </Button>
                ) : (
                  <Text variant="meta" tone="subtle" as="p">
                    Cancel your paid plan via Manage billing to return to Free.
                  </Text>
                )}
              </div>
            </li>

            {plans.length === 0 ? (
              <li className="md:col-span-2">
                <Text variant="body" tone="muted" as="p">
                  No paid plans are configured for this cell.
                </Text>
              </li>
            ) : (
              plans.map(plan => {
                const isCurrent = hasSubscription && currentKey === plan.key;
                const isUpgrade =
                  hasSubscription &&
                  Boolean(currentKey) &&
                  plan.rank > currentRank &&
                  plan.price_configured;
                const isDowngrade =
                  hasSubscription &&
                  Boolean(currentKey) &&
                  plan.rank < currentRank &&
                  plan.price_configured;
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
                    <PlanFeatureList labels={appNames} />
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
                      ) : isDowngrade ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={
                            changePlanMut.isPending &&
                            changePlanMut.variables === plan.key
                          }
                          disabled={busy}
                          onClick={() => changePlanMut.mutate(plan.key)}
                          data-testid={`settings-billing-downgrade-${plan.key}`}
                        >
                          Switch to {plan.title}
                        </Button>
                      ) : (
                        <Button
                          variant={
                            plan.key === 'premium' ? 'primary' : 'secondary'
                          }
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
              })
            )}
          </ul>
        )}
      </SettingsSection>
    </div>
  );
}
