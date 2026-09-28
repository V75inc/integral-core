/**
 * Workspace billing — subscribe, portal, and commercial App add-ons.
 *
 * Card entry stays on Stripe Checkout / Customer Portal. This section only
 * starts those flows and explains the current base-plan access.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { billingApi } from '../../../api/billing';
import { errorMessageFromAxios } from '../../../api/helpers';
import {
  paywallForSlug,
  paywallLabel,
  type BillingAddon,
} from '../../../components/apps/billingAccess';
import { Button } from '../../../components/ui/Button';
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

function accessCopy(access: string, graceUntil?: string | null): string {
  if (access === 'locked') {
    return 'This workspace needs an active Integral Business subscription before you can create content or install Apps.';
  }
  if (access === 'grace') {
    const until = formatGrace(graceUntil);
    return until
      ? `Payment is past due. Access stays open until ${until}.`
      : 'Payment is past due. Access stays open until the grace period ends.';
  }
  if (access === 'open') {
    return 'Your base plan is active. Commercial Apps are optional add-ons.';
  }
  return 'Billing status is unavailable.';
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
            ? 'Billing is managed by workspace admins and owners. Ask an admin to subscribe or open the customer portal.'
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
          Billing is off for this deployment. Subscriptions are not required
          to create content or install Apps.
        </Text>
      </div>
    );
  }

  const addons: BillingAddon[] = catalog?.addons ?? [];

  return (
    <div className="flex flex-col gap-5" data-testid="settings-billing">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Billing
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Base plan and commercial App add-ons for this workspace. Payment
          methods are entered on the Stripe-hosted checkout and portal pages.
        </Text>
      </div>

      <SettingsSection
        title="Base plan"
        description={accessCopy(status.access, status.grace_until)}
      >
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <Text variant="body" tone="subtle" as="dt">
            Access
          </Text>
          <Text variant="body" as="dd">
            <span data-testid="settings-billing-access">{status.access}</span>
          </Text>
          {status.status ? (
            <>
              <Text variant="body" tone="subtle" as="dt">
                Status
              </Text>
              <Text variant="body" as="dd">
                {status.status}
              </Text>
            </>
          ) : null}
          {status.plan_key ? (
            <>
              <Text variant="body" tone="subtle" as="dt">
                Plan
              </Text>
              <Text variant="body" as="dd">
                {status.plan_key}
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
          {status.access === 'locked' && status.checkout_available ? (
            <Button
              variant="primary"
              size="sm"
              loading={checkoutMut.isPending}
              disabled={busy}
              onClick={() => checkoutMut.mutate()}
              data-testid="settings-billing-subscribe"
            >
              Subscribe
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
        title="App add-ons"
        description="Documents, CRM, and Sales are optional. Access updates after Stripe confirms the change."
      >
        {catalogQuery.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : addons.length === 0 ? (
          <Text variant="body" tone="muted" as="p">
            No commercial add-ons are configured for this cell.
          </Text>
        ) : (
          <ul className="divide-y divide-[var(--border-subtle)]">
            {addons.map(addon => {
              const decision = paywallForSlug(addon.slug, status, catalog);
              const note = paywallLabel(decision, addon.slug);
              const canAdd =
                decision.reason === 'addon' && addon.price_configured;
              return (
                <li
                  key={addon.slug}
                  className="flex flex-wrap items-center justify-between gap-3 py-3"
                  data-testid={`settings-billing-addon-${addon.slug}`}
                >
                  <div className="min-w-0">
                    <Text variant="body" weight="medium" as="p">
                      {addon.slug}
                    </Text>
                    <Text variant="meta" tone="subtle" as="p">
                      {addon.entitled
                        ? 'Entitled'
                        : note ||
                          (addon.price_configured
                            ? 'Not entitled'
                            : 'Price not configured')}
                    </Text>
                  </div>
                  {canAdd ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={
                        addonMut.isPending &&
                        addonMut.variables === addon.slug
                      }
                      disabled={busy}
                      onClick={() => addonMut.mutate(addon.slug)}
                      data-testid={`settings-billing-add-${addon.slug}`}
                    >
                      Add
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>
    </div>
  );
}
