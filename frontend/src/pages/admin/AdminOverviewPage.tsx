import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { adminApi } from '../../api/admin';
import { PageHeading, PageSection, PageShell, Skeleton } from '../../components/ui';
import { useSetCrumbs } from '../../context/CrumbsContext';

function MetricTile({
  label,
  value,
  href,
}: {
  label: string;
  value: number | string;
  href?: string;
}) {
  const inner = (
    <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--panel)] p-5">
      <p className="text-xs uppercase tracking-[0.08em] text-[var(--text-subtle)]">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-[var(--text)]">{value}</p>
    </div>
  );
  if (href) {
    return (
      <Link to={href} className="block transition-opacity hover:opacity-90">
        {inner}
      </Link>
    );
  }
  return inner;
}

export function AdminOverviewPage() {
  useSetCrumbs([{ label: 'Admin', to: '/admin' }, { label: 'Overview' }]);

  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['admin', 'overview'],
    queryFn: () => adminApi.getOverview(),
  });

  return (
    <PageShell>
      <PageSection>
        <PageHeading>Platform overview</PageHeading>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          Cross-tenant counts for users, workspaces, and resources.
        </p>
      </PageSection>
      <PageSection className="mt-8">
        {isPending && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-[var(--danger-border)] bg-[var(--danger-bg)] p-4 text-sm">
            <p className="text-[var(--danger-text)]">Could not load overview.</p>
            <button
              type="button"
              className="mt-2 text-[var(--link)] underline"
              onClick={() => refetch()}
            >
              Retry
            </button>
          </div>
        )}
        {data && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricTile label="Total users" value={data.total_users} href="/admin/users" />
            <MetricTile label="Active users" value={data.active_users} href="/admin/users?status=active" />
            <MetricTile label="Inactive users" value={data.inactive_users} href="/admin/users?status=inactive" />
            <MetricTile label="Platform admins" value={data.platform_admins} />
            <MetricTile label="Personal workspaces" value={data.personal_workspaces} href="/admin/workspaces?kind=personal" />
            <MetricTile label="Org workspaces" value={data.organization_workspaces} href="/admin/workspaces?kind=organization" />
            <MetricTile label="Apps" value={data.total_apps} href="/admin/apps" />
            <MetricTile label="Tracks" value={data.total_tracks} href="/admin/tracks" />
          </div>
        )}
      </PageSection>
    </PageShell>
  );
}
