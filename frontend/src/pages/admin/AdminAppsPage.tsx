import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { adminApi } from '../../api/admin';
import {
  LINE_ICON_STROKE,
  PageHeading,
  PageSection,
  PageShell,
  Skeleton,
} from '../../components/ui';
import { useSetCrumbs } from '../../context/CrumbsContext';
import { AdminEntityLink, AdminOwnerCell } from '../../components/admin/AdminEntityLink';
import { AdminResourceDetailPage } from './AdminResourceDetailPage';

function ResourceListPage({
  title,
  subtitle,
  listFn,
  resourceType,
}: {
  title: string;
  subtitle: string;
  listFn: typeof adminApi.listApps;
  resourceType: 'app' | 'track';
}) {
  useSetCrumbs([
    { label: 'Admin', to: '/admin' },
    { label: title },
  ]);

  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, Number(searchParams.get('page') || '1'));
  const [searchInput, setSearchInput] = useState(searchParams.get('search') || '');
  const [debouncedSearch, setDebouncedSearch] = useState(searchInput);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (debouncedSearch) next.set('search', debouncedSearch);
    else next.delete('search');
    next.set('page', '1');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const queryKey = useMemo(
    () => ['admin', title.toLowerCase(), page, debouncedSearch],
    [title, page, debouncedSearch],
  );

  const { data, isPending, error, refetch } = useQuery({
    queryKey,
    queryFn: () =>
      listFn({
        page,
        per_page: 20,
        search: debouncedSearch || undefined,
      }),
  });

  function setPage(next: number) {
    const params = new URLSearchParams(searchParams);
    params.set('page', String(next));
    setSearchParams(params);
  }

  return (
    <PageShell>
      <PageSection>
        <PageHeading>{title}</PageHeading>
        <p className="mt-2 text-sm text-[var(--text-muted)]">{subtitle}</p>
      </PageSection>
      <PageSection className="mt-6">
        <div className="relative max-w-md">
          <Search
            size={16}
            strokeWidth={LINE_ICON_STROKE}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-subtle)]"
          />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={`Search ${title.toLowerCase()}…`}
            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] py-2 pl-9 pr-3 text-sm"
          />
        </div>
      </PageSection>
      <PageSection className="mt-6">
        {isPending && <Skeleton className="h-64 w-full rounded-xl" />}
        {error && (
          <p className="text-sm text-[var(--danger-text)]">
            Could not load {title.toLowerCase()}.{' '}
            <button type="button" className="underline" onClick={() => refetch()}>Retry</button>
          </p>
        )}
        {data && (
          <>
            <div className="overflow-x-auto rounded-xl border border-[var(--border-subtle)]">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-left text-[var(--text-subtle)]">
                    <th className="px-4 py-3 font-medium">Name</th>
                    <th className="px-4 py-3 font-medium">Workspace</th>
                    <th className="px-4 py-3 font-medium">Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id} className="border-b border-[var(--border-subtle)] last:border-0 hover:bg-[var(--panel)]">
                      <td className="px-4 py-3">
                        <AdminEntityLink
                          type={resourceType}
                          id={item.id}
                          label={item.name || 'Untitled'}
                        />
                        <p className="text-xs text-[var(--text-subtle)] font-mono mt-0.5 truncate max-w-xs">
                          {item.id}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <AdminEntityLink
                          type="workspace"
                          id={item.workspace_id}
                          label={item.workspace_name}
                          className="text-[var(--link)] hover:underline"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <AdminOwnerCell owner={item.owner} />
                      </td>
                    </tr>
                  ))}
                  {data.items.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-4 py-8 text-center text-[var(--text-muted)]">
                        No {title.toLowerCase()} match your search.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {data.total_pages > 1 && (
              <div className="mt-4 flex items-center justify-between text-sm text-[var(--text-muted)]">
                <span>Page {data.page} of {data.total_pages}</span>
                <div className="flex gap-2">
                  <button type="button" disabled={!data.has_previous} onClick={() => setPage(page - 1)} className="rounded-lg px-3 py-1.5 disabled:opacity-40">Previous</button>
                  <button type="button" disabled={!data.has_next} onClick={() => setPage(page + 1)} className="rounded-lg px-3 py-1.5 disabled:opacity-40">Next</button>
                </div>
              </div>
            )}
          </>
        )}
      </PageSection>
    </PageShell>
  );
}

export function AdminAppsPage() {
  return (
    <ResourceListPage
      title="Apps"
      subtitle="Cross-workspace app directory."
      listFn={adminApi.listApps}
      resourceType="app"
    />
  );
}

export function AdminTracksPage() {
  return (
    <ResourceListPage
      title="Tracks"
      subtitle="Cross-workspace track directory."
      listFn={adminApi.listTracks}
      resourceType="track"
    />
  );
}

export function AdminAppDetailPage() {
  return <AdminResourceDetailPage resourceType="app" />;
}

export function AdminTrackDetailPage() {
  return <AdminResourceDetailPage resourceType="track" />;
}
