import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CreditCard, Plus } from 'lucide-react';

import { adminApi } from '../../api/admin';
import { billingApi } from '../../api/billing';
import { errorMessageFromAxios } from '../../api/helpers';
import type { HostedSubscription } from '../../components/apps/billingAccess';
import { AdminEntityLink } from '../../components/admin/AdminEntityLink';
import {
  Badge,
  LINE_ICON_STROKE,
  PageHeading,
  PageSection,
  PageShell,
  Skeleton,
} from '../../components/ui';
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

const STATUS_LABELS: Record<string, string> = {
  trialing: 'Free Trial',
  active: 'Active',
  past_due: 'Past due',
  canceled: 'Canceled',
  incomplete: 'Incomplete',
};

const SOURCE_LABELS: Record<string, string> = {
  stripe: 'Stripe',
  manual: 'Manual',
};

const ACCESS_LABELS: Record<string, string> = {
  open: 'Open',
  grace: 'Grace',
  locked: 'Locked',
};

const PLAN_OPTIONS = [
  { value: 'basic', label: 'Basic' },
  { value: 'premium', label: 'Premium' },
] as const;

const FORM_STATUSES = ['trialing', 'active', 'canceled'] as const;

function statusLabel(value: string): string {
  return STATUS_LABELS[value] || value || '—';
}

function sourceLabel(value: string): string {
  return SOURCE_LABELS[value] || value || '—';
}

function accessLabel(value: string): string {
  return ACCESS_LABELS[value] || value || '—';
}

function planLabel(key: string): string {
  const normalized = (key || '').trim().toLowerCase();
  if (normalized === 'basic' || normalized === 'base') {
    return normalized === 'base' ? 'Basic (legacy)' : 'Basic';
  }
  if (normalized === 'premium') return 'Premium';
  return key || '—';
}

function accessBadgeVariant(access: string): string {
  if (access === 'open') return 'success';
  if (access === 'grace') return 'warning';
  if (access === 'locked') return 'danger';
  return 'default';
}

function toDatetimeLocal(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromDatetimeLocal(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

type FormMode = 'grant' | 'override';

interface FormState {
  mode: FormMode;
  workspaceId: string;
  workspaceLabel: string;
  planKey: string;
  status: string;
  accessUntilLocal: string;
  externalCustomer: string;
  externalSubscription: string;
  billingAccountId: string;
}

function emptyGrantForm(): FormState {
  return {
    mode: 'grant',
    workspaceId: '',
    workspaceLabel: '',
    planKey: 'basic',
    status: 'active',
    accessUntilLocal: '',
    externalCustomer: '',
    externalSubscription: '',
    billingAccountId: '',
  };
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

  const [form, setForm] = useState<FormState | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

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

  const workspacesQuery = useQuery({
    queryKey: ['admin', 'billing', 'workspaces'],
    queryFn: () => adminApi.listWorkspaces({ page: 1, per_page: 100 }),
    enabled: form?.mode === 'grant',
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

  const saveMut = useMutation({
    mutationFn: () => {
      if (!form) throw new Error('form closed');
      return billingApi.setSubscription({
        workspace_id: form.workspaceId,
        status: form.status,
        plan_key: form.planKey,
        billing_account_id:
          form.billingAccountId || `ba:${form.workspaceId}`,
        external_customer_id: form.externalCustomer,
        external_subscription_id: form.externalSubscription,
        access_until: fromDatetimeLocal(form.accessUntilLocal),
      });
    },
    onSuccess: () => {
      toast.showToast(
        form?.mode === 'grant' ? 'Plan granted' : 'Subscription override saved',
        'success',
      );
      setForm(null);
      setShowAdvanced(false);
      qc.invalidateQueries({ queryKey: ['admin', 'billing'] });
    },
    onError: err => {
      toast.showToast(
        errorMessageFromAxios(err, 'Could not save subscription'),
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

  function openGrant() {
    setShowAdvanced(false);
    setForm(emptyGrantForm());
  }

  function openOverride(row: HostedSubscription) {
    setShowAdvanced(Boolean(row.external_customer_id || row.external_subscription_id));
    setForm({
      mode: 'override',
      workspaceId: row.workspace_id,
      workspaceLabel: row.workspace_name || row.workspace_id,
      planKey:
        row.plan_key === 'base' ? 'basic' : row.plan_key || 'basic',
      status: FORM_STATUSES.includes(row.status as (typeof FORM_STATUSES)[number])
        ? row.status
        : 'active',
      accessUntilLocal: toDatetimeLocal(row.access_until),
      externalCustomer: row.external_customer_id || '',
      externalSubscription: row.external_subscription_id || '',
      billingAccountId: row.billing_account_id || '',
    });
  }

  function closeForm() {
    setForm(null);
    setShowAdvanced(false);
  }

  const rows = data?.subscriptions ?? [];
  const workspaces = workspacesQuery.data?.workspaces ?? [];
  const canSave =
    Boolean(form?.workspaceId) &&
    Boolean(form?.planKey) &&
    Boolean(form?.status);

  return (
    <PageShell>
      <PageSection>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <PageHeading>Billing</PageHeading>
            <p className="mt-2 max-w-2xl text-sm text-[var(--text-muted)]">
              Hosted plan projections across every workspace. Manual grants
              apply commercial App entitlements and are not overwritten by
              Stripe.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="primary"
                size="sm"
                icon={<Plus size={14} strokeWidth={LINE_ICON_STROKE} />}
                onClick={openGrant}
                data-testid="admin-billing-grant"
              >
                Grant plan
              </Button>
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
            <p
              className="max-w-sm text-right text-xs text-[var(--text-subtle)]"
              data-testid="admin-billing-reconcile-help"
            >
              Refetch Stripe subscriptions and re-apply access. Skips manual
              overrides. Use after missed webhooks.
            </p>
          </div>
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
              {value ? statusLabel(value) : 'All statuses'}
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
              {value ? sourceLabel(value) : 'All sources'}
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
            <table
              className="min-w-full text-left text-sm"
              data-testid="admin-billing-table"
            >
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
                        label={row.workspace_name || row.workspace_id}
                      />
                      {row.workspace_name ? (
                        <div className="mt-0.5 font-mono text-xs text-[var(--text-subtle)]">
                          {row.workspace_id}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">{statusLabel(row.status)}</td>
                    <td className="px-4 py-3">
                      <Badge variant={accessBadgeVariant(row.access)}>
                        {accessLabel(row.access)}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">{sourceLabel(row.source)}</td>
                    <td className="px-4 py-3">{planLabel(row.plan_key)}</td>
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
        open={Boolean(form)}
        onClose={closeForm}
        title={
          form?.mode === 'grant'
            ? 'Grant plan'
            : 'Manual subscription override'
        }
        variant="compact"
      >
        {form ? (
          <div
            className="flex flex-col gap-4"
            data-testid={
              form.mode === 'grant'
                ? 'admin-billing-grant-form'
                : 'admin-billing-override-form'
            }
          >
            <p className="text-sm text-[var(--text-muted)]">
              {form.mode === 'grant'
                ? 'Creates a manual subscription and grants commercial App entitlements for the selected plan.'
                : 'Sets this workspace to source=manual. Provider reconcile will not overwrite this row. Entitlements follow the selected plan.'}
            </p>

            {form.mode === 'grant' ? (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[var(--text-subtle)]">Workspace</span>
                <select
                  value={form.workspaceId}
                  onChange={e => {
                    const id = e.target.value;
                    const match = workspaces.find(ws => ws.id === id);
                    setForm({
                      ...form,
                      workspaceId: id,
                      workspaceLabel: match?.name || id,
                    });
                  }}
                  className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                  data-testid="admin-billing-workspace"
                >
                  <option value="">Select a workspace…</option>
                  {workspaces.map(ws => (
                    <option key={ws.id} value={ws.id}>
                      {ws.name || ws.id}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <p className="text-sm">
                <span className="text-[var(--text-subtle)]">Workspace</span>
                <br />
                <span className="font-medium">{form.workspaceLabel}</span>
                <span className="mt-0.5 block font-mono text-xs text-[var(--text-subtle)]">
                  {form.workspaceId}
                </span>
              </p>
            )}

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">Plan</span>
              <select
                value={form.planKey}
                onChange={e => setForm({ ...form, planKey: e.target.value })}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                data-testid="admin-billing-plan"
              >
                {PLAN_OPTIONS.map(option => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">Status</span>
              <select
                value={form.status}
                onChange={e => setForm({ ...form, status: e.target.value })}
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                data-testid="admin-billing-status"
              >
                {FORM_STATUSES.map(value => (
                  <option key={value} value={value}>
                    {statusLabel(value)}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-[var(--text-subtle)]">Access ends</span>
              <input
                type="datetime-local"
                value={form.accessUntilLocal}
                onChange={e =>
                  setForm({ ...form, accessUntilLocal: e.target.value })
                }
                className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2"
                data-testid="admin-billing-access-until"
              />
              <span className="text-xs text-[var(--text-subtle)]">
                Optional. When set, access locks after this time (manual trials
                and comps). Leave blank for no end date.
              </span>
            </label>

            <button
              type="button"
              className="self-start text-xs text-[var(--text-muted)] underline"
              onClick={() => setShowAdvanced(v => !v)}
            >
              {showAdvanced ? 'Hide' : 'Show'} Stripe ids (optional)
            </button>

            {showAdvanced ? (
              <>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[var(--text-subtle)]">
                    External customer id
                  </span>
                  <input
                    value={form.externalCustomer}
                    onChange={e =>
                      setForm({ ...form, externalCustomer: e.target.value })
                    }
                    className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2 font-mono text-xs"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[var(--text-subtle)]">
                    External subscription id
                  </span>
                  <input
                    value={form.externalSubscription}
                    onChange={e =>
                      setForm({
                        ...form,
                        externalSubscription: e.target.value,
                      })
                    }
                    className="rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2 font-mono text-xs"
                  />
                </label>
              </>
            ) : null}

            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={closeForm}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                loading={saveMut.isPending}
                disabled={!canSave || saveMut.isPending}
                onClick={() => saveMut.mutate()}
                data-testid="admin-billing-save"
              >
                {form.mode === 'grant' ? 'Grant plan' : 'Save override'}
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
    </PageShell>
  );
}
