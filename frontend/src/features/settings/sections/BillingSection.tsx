/**
 * Workspace billing — paid App add-ons and the Stripe customer account.
 *
 * Free Apps (Documents, Organization) install without billing. Paid Apps
 * (CRM, Sales, Guyana Payroll) are unlockable add-ons. Card entry stays on
 * Stripe Checkout / Portal.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { billingApi } from '../../../api/billing';
import { errorMessageFromAxios } from '../../../api/helpers';
import {
  addonDisplayName,
  paywallForSlug,
  type BillingAddon,
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
    mutationFn: () => billingApi.checkout(workspaceId),
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

  const addonMut = useMutation({
    mutationFn: (slug: string) => billingApi.addAddon(workspaceId, slug),
    onSuccess: result => {
      if (result?.url) {
        window.location.assign(result.url);
        return;
      }
      toast.showToast(
        result?.message ||
          'Payment will confirm this add-on. Access updates when the webhook arrives.',
        'success',
      );
      invalidate();
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not add this App'),
        'error',
      );
    },
  });

  const busy =
    checkoutMut.isPending || portalMut.isPending || addonMut.isPending;

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

  const addons: BillingAddon[] = catalog?.addons ?? [];
  const graceLabel = formatGrace(status.grace_until);
  const needsBillingSetup =
    !catalog?.has_subscription && Boolean(status.checkout_available);

  return (
    <div className="flex flex-col gap-5" data-testid="settings-billing">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Documents and Organization install free. Unlock CRM, Sales, or Guyana
          Payroll when you need them — card details are entered on Stripe.
        </Text>
      </div>

      <SettingsSection
        title="Payment account"
        description={
          status.access === 'grace' && graceLabel
            ? `Payment is past due. Access stays open until ${graceLabel}.`
            : catalog?.has_subscription
              ? 'Your Stripe billing account is connected for this workspace.'
              : 'Set up billing once, then unlock paid Apps as you need them.'
        }
      >
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <Text variant="body" tone="subtle" as="dt">
            Account
          </Text>
          <Text variant="body" as="dd">
            <span data-testid="settings-billing-access">
              {catalog?.has_subscription
                ? status.status || status.access
                : 'Not set up'}
            </span>
          </Text>
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
          {needsBillingSetup ? (
            <Button
              variant="primary"
              size="sm"
              loading={checkoutMut.isPending}
              disabled={busy}
              onClick={() => checkoutMut.mutate()}
              data-testid="settings-billing-subscribe"
            >
              Set up billing
            </Button>
          ) : null}
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
        title="Paid Apps"
        description="These packages need an active add-on. Documents and Organization are free — install them from Manage apps."
      >
        {catalogQuery.isPending ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Skeleton className="h-36 w-full" />
            <Skeleton className="h-36 w-full" />
          </div>
        ) : addons.length === 0 ? (
          <Text variant="body" tone="muted" as="p">
            No paid Apps are configured for this cell.
          </Text>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {addons.map(addon => {
              const decision = paywallForSlug(addon.slug, status, catalog);
              const name = addonDisplayName(addon);
              const canUnlock =
                (decision.reason === 'addon' || !catalog?.has_subscription) &&
                addon.price_configured &&
                !addon.entitled;
              const lockedByDependency = decision.reason === 'dependency';
              return (
                <li
                  key={addon.slug}
                  className="flex flex-col rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel-2)] p-4"
                  data-testid={`settings-billing-addon-${addon.slug}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <Text variant="body" weight="semibold" as="p">
                        {name}
                      </Text>
                      <Text variant="meta" tone="subtle" as="p" className="mt-0.5 font-mono">
                        {addon.slug}
                      </Text>
                    </div>
                    <Pill
                      variant={addon.entitled ? 'success' : 'neutral'}
                      tone="descriptive"
                    >
                      {addon.entitled ? 'Included' : 'Not included'}
                    </Pill>
                  </div>
                  {addon.description ? (
                    <Text variant="body-sm" tone="muted" as="p" className="mt-3">
                      {addon.description}
                    </Text>
                  ) : null}
                  {lockedByDependency ? (
                    <Text variant="meta" tone="subtle" as="p" className="mt-3">
                      Unlock {decision.missing.join(' and ')} first.
                    </Text>
                  ) : null}
                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    {addon.entitled ? null : canUnlock ? (
                      <Button
                        variant="primary"
                        size="sm"
                        loading={
                          addonMut.isPending && addonMut.variables === addon.slug
                        }
                        disabled={busy}
                        onClick={() => addonMut.mutate(addon.slug)}
                        data-testid={`settings-billing-add-${addon.slug}`}
                      >
                        {catalog?.has_subscription ? 'Unlock' : 'Set up & unlock'}
                      </Button>
                    ) : !addon.price_configured ? (
                      <Text variant="meta" tone="subtle" as="p">
                        Price not configured
                      </Text>
                    ) : null}
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
