import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CreditCard } from 'lucide-react';

import { billingApi } from '../../api/billing';
import { errorMessageFromAxios } from '../../api/helpers';
import type { HostedSubscription } from '../../components/apps/billingAccess';
import { AdminEntityLink } from '../../components/admin/AdminEntityLink';
import { Badge, LINE_ICON_STROKE, PageHeading, PageSection, PageShell, Skeleton } from '../../components/ui';
import { Button } from '../../components/ui/Button';
import { Modal } from '../../components/ui/Modal';
import { useSetCrumbs } from '../../context/CrumbsContext';
import { useToast } from '../../context/ToastContext';

const STATUS_FILTERS = [
  '',
  'trialing',
  'active',
  'past_due',
  'canceled',
  'incomplete',
] as const;

const SOURCE_FILTERS = ['', 'stripe', 'manual'] as const;

function accessBadgeVariant(access: string): string {
  if (access === 'open') return 'success';
  if (access === 'grace') return 'warning';
  if (access === 'locked') return 'danger';
  return 'default';
}

export function AdminBillingPage() {
  useSetCrumbs([
    { label: 'Admin', to: '/admin' },
    { label: 'Billing' },
  ]);

  const toast = useToast();
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') || '';
  const sourceFilter = searchParams.get('source') || '';

  const [overrideRow, setOverrideRow] = useState<HostedSubscription | null>(
    null,
  );
  const [overrideStatus, setOverrideStatus] = useState('active');
  const [overrideCustomer, setOverrideCustomer] = useState('');
  const [overrideSubscription, setOverrideSubscription] = useState('');

  const queryKey = useMemo(
    () => ['admin', 'billing', statusFilter, sourceFilter],
    [statusFilter, sourceFilter],
  );

  const { data, isPending, error, refetch } = useQuery({
    queryKey,
    queryFn: () =>
      billingApi.listSubscriptions({
        status: statusFilter || undefined,
        source: sourceFilter || undefined,
      }),
  });

  const reconcileMut = useMutation({
    mutationFn: () => billingApi.reconcile(),
    onSuccess: () => {
      toast.showToast('Reconcile started', 'success');
      qc.invalidateQueries({ queryKey: ['admin', 'billing'] });
      refetch();
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Reconcile failed'),
        'error',
      );
    },
  });

  const overrideMut = useMutation({
    mutationFn: () =>
      billingApi.setSubscription({
        workspace_id: overrideRow!.workspace_id,
        status: overrideStatus,
        billing_account_id: overrideRow?.billing_account_id || '',
        plan_key: overrideRow?.plan_key || 'basic',
        external_customer_id: overrideCustomer,
        external_subscription_id: overrideSubscription,
      }),
    onSuccess: () => {
      toast.showToast('Subscription override saved', 'success');
      setOverrideRow(null);
      qc.invalidateQueries({ queryKey: ['admin', 'billing'] });
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not save override'),
        'error',
      );
    },
  });

  function setFilter(key: 'status' | 'source', value: string) {
    const params = new URLSearchParams(searchParams);
    if (value) params.set(key, value);
    else params.delete(key);
    setSearchParams(params);
  }

  function openOverride(row: HostedSubscription) {
    setOverrideRow(row);
    setOverrideStatus(row.status || 'active');
    setOverrideCustomer(row.external_customer_id || '');
    setOverrideSubscription(row.external_subscription_id || '');
  }

  const rows = data?.subscriptions ?? [];

  return (
    <PageShell>
      <PageSection>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <PageHeading>Billing</PageHeading>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              Hosted base-plan projections across every workspace. Manual
              overrides are not overwritten by the payment provider.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            icon={<CreditCard size={14} strokeWidth={LINE_ICON_STROKE} />}
            loading={reconcileMut.isPending}
            disabled={reconcileMut.isPending}
            onClick={() => reconcileMut.mutate()}
            data-testid="admin-billing-reconcile"
          >
            Reconcile now
          </Button>
        </div>
      </PageSection>

      <PageSection className="mt-6">
        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_FILTERS.map(value => (
            <button
              key={`status-${value || 'all'}`}
              type="button"
              onClick={() => setFilter('status', value)}
              className={[
                'rounded-lg px-3 py-1.5 text-xs',
                statusFilter === value
                  ? 'bg-[var(--nav-active-bg)] font-medium'
                  : 'text-[var(--text-muted)]',
              ].join(' ')}
            >
              {value || 'All statuses'}
            </button>
          ))}
        </div>
        <div className="mb-4 flex flex-wrap gap-2">
          {SOURCE_FILTERS.map(value => (
            <button
              key={`source-${value || 'all'}`}
              type="button"
              onClick={() => setFilter('source', value)}
              className={[
                'rounded-lg px-3 py-1.5 text-xs',
                sourceFilter === value
                  ? 'bg-[var(--nav-active-bg)] font-medium'
                  : 'text-[var(--text-muted)]',
              ].join(' ')}
            >
              {value || 'All sources'}
            </button>
          ))}
        </div>

        {isPending ? (
          <Skeleton className="h-48 w-full" />
        ) : error ? (
          <div className="rounded-lg border border-[var(--border-subtle)] p-4">
            <p className="text-sm text-[var(--danger-fg)]">
              {errorMessageFromAxios(error, 'Could not load subscriptions')}
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => refetch()}
            >
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p
            className="text-sm text-[var(--text-muted)]"
            data-testid="admin-billing-empty"
          >
            No subscription rows match these filters.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-[var(--border-subtle)]">
            <table className="min-w-full text-left text-sm" data-testid="admin-billing-table">
              <thead className="bg-[var(--panel-2)] text-xs uppercase tracking-wide text-[var(--text-subtle)]">
                <tr>
                  <th className="px-4 py-3 font-medium">Workspace</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Access</th>
                  <th className="px-4 py-3 font-medium">Source</th>
                  <th className="px-4 py-3 font-medium">Plan</th>
                  <th className="px-4 py-3 font-medium">External</th>
                  <th className="px-4 py-3 font-medium">Updated</th>
                  <th className="px-4 py-3 font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr
                    key={row.workspace_id}
                    className="border-t border-[var(--border-subtle)]"
                    data-testid={`admin-billing-row-${row.workspace_id}`}
                  >
                    <td className="px-4 py-3">
                      <AdminEntityLink
                        type="workspace"
                        id={row.workspace_id}
                        label={row.workspace_id}
                      />
                    </td>
                    <td className="px-4 py-3">{row.status}</td>
                    <td className="px-4 py-3">
                      <Badge variant={accessBadgeVariant(row.access)}>
                        {row.access}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">{row.source}</td>
                    <td className="px-4 py-3">{row.plan_key}</td>
                    <td className="px-4 py-3 font-mono text-xs text-[var(--text-muted)]">
                      <div>{row.external_customer_id || '—'}</div>
                      <div>{row.external_subscription_id || '—'}</div>
                    </td>
                    <td className="px-4 py-3 text-[var(--text-muted)]">
                      {row.updated_at
                        ? new Date(row.updated_at).toLocaleString()
                        : '—'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => openOverride(row)}
                        data-testid={`admin-billing-override-${row.workspace_id}`}
                      >
                        Override
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-[var(--border-subtle)] px-4 py-2 text-xs text-[var(--text-subtle)]">
              {data?.total ?? rows.length} subscription
              {(data?.total ?? rows.length) === 1 ? '' : 's'}
            </p>
          </div>
        )}
      </PageSection>

      <Modal
        open={Boolean(overrideRow)}
        onClose={() => setOverrideRow(null)}
        title="Manual subscription override"
        variant="compact"
      >
        {overrideRow ? (
          <div className="flex flex-col gap-4" data-testid="admin-billing-override-form">
            <p className="text-sm text-[var(--text-muted)]">
              Sets <span className="font-mono">{overrideRow.workspace_id}</span>{' '}
              to <code>source=manual</code>. Provider reconcile will not
              overwrite this row.
            </p>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">Status</span>
              <select
                value={overrideStatus}
                onChange={e => setOverrideStatus(e.target.value)}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
              >
                {STATUS_FILTERS.filter(Boolean).map(value => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">
                External customer id
              </span>
              <input
                value={overrideCustomer}
                onChange={e => setOverrideCustomer(e.target.value)}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2 font-mono text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">
                External subscription id
              </span>
              <input
                value={overrideSubscription}
                onChange={e => setOverrideSubscription(e.target.value)}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2 font-mono text-xs"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setOverrideRow(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                loading={overrideMut.isPending}
                disabled={overrideMut.isPending}
                onClick={() => overrideMut.mutate()}
                data-testid="admin-billing-override-save"
              >
                Save override
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
    </PageShell>
  );
}
