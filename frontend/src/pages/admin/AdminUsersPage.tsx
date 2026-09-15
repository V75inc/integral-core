import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { adminApi } from '../../api/admin';
import {
  Avatar,
  Badge,
  LINE_ICON_STROKE,
  PageHeading,
  PageSection,
  PageShell,
  Skeleton,
} from '../../components/ui';
import { useSetCrumbs } from '../../context/CrumbsContext';
import { formatRelativeTime } from '../../utils';
import { AdminEntityLink } from '../../components/admin/AdminEntityLink';

export function AdminUsersPage() {
  useSetCrumbs([
    { label: 'Admin', to: '/admin' },
    { label: 'Users' },
  ]);

  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, Number(searchParams.get('page') || '1'));
  const status = (searchParams.get('status') || 'all') as 'all' | 'active' | 'inactive';
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
    () => ['admin', 'users', page, status, debouncedSearch],
    [page, status, debouncedSearch],
  );

  const { data, isPending, error, refetch } = useQuery({
    queryKey,
    queryFn: () =>
      adminApi.listUsers({
        page,
        per_page: 20,
        search: debouncedSearch || undefined,
        status,
      }),
  });

  function setStatus(next: 'all' | 'active' | 'inactive') {
    const params = new URLSearchParams(searchParams);
    params.set('status', next);
    params.set('page', '1');
    setSearchParams(params);
  }

  function setPage(next: number) {
    const params = new URLSearchParams(searchParams);
    params.set('page', String(next));
    setSearchParams(params);
  }

  return (
    <PageShell>
      <PageSection>
        <PageHeading>Users</PageHeading>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          View and manage all platform users.
        </p>
      </PageSection>
      <PageSection className="mt-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative max-w-md flex-1">
            <Search
              size={16}
              strokeWidth={LINE_ICON_STROKE}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-subtle)]"
            />
            <input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by name or email…"
              className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--panel)] py-2 pl-9 pr-3 text-sm"
            />
          </div>
          <div className="flex gap-2">
            {(['all', 'active', 'inactive'] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatus(s)}
                className={[
                  'rounded-lg px-3 py-1.5 text-xs capitalize',
                  status === s
                    ? 'bg-[var(--nav-active-bg)] text-[var(--text)] font-medium'
                    : 'text-[var(--text-muted)] hover:bg-[var(--panel)]',
                ].join(' ')}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </PageSection>
      <PageSection className="mt-6">
        {isPending && <Skeleton className="h-64 w-full rounded-xl" />}
        {error && (
          <p className="text-sm text-[var(--danger-text)]">
            Could not load users.{' '}
            <button type="button" className="underline" onClick={() => refetch()}>
              Retry
            </button>
          </p>
        )}
        {data && (
          <>
            <div className="overflow-x-auto rounded-xl border border-[var(--border-subtle)]">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-left text-[var(--text-subtle)]">
                    <th className="px-4 py-3 font-medium">User</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Role</th>
                    <th className="px-4 py-3 font-medium">Created</th>
                    <th className="px-4 py-3 font-medium">Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((user) => (
                    <tr
                      key={user.id}
                      className="border-b border-[var(--border-subtle)] last:border-0 hover:bg-[var(--panel)]"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <Avatar name={user.display_name} size="sm" />
                          <div>
                            <AdminEntityLink
                              type="user"
                              id={user.id}
                              label={user.display_name || '—'}
                            />
                            <p className="text-xs text-[var(--text-muted)]">{user.email || '—'}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={user.is_active ? 'success' : 'danger'}>
                          {user.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        {user.is_platform_admin ? (
                          <Badge variant="info">Super Admin</Badge>
                        ) : (
                          <span className="text-[var(--text-muted)]">Member</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-[var(--text-muted)]">
                        {user.created_at
                          ? formatRelativeTime(user.created_at)
                          : '—'}
                      </td>
                      <td className="px-4 py-3 text-[var(--text-muted)]">
                        {user.last_accessed
                          ? formatRelativeTime(user.last_accessed)
                          : '—'}
                      </td>
                    </tr>
                  ))}
                  {data.users.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-[var(--text-muted)]">
                        No users match your filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {data.total_pages > 1 && (
              <div className="mt-4 flex items-center justify-between text-sm text-[var(--text-muted)]">
                <span>
                  Page {data.page} of {data.total_pages} ({data.total} total)
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!data.has_previous}
                    onClick={() => setPage(page - 1)}
                    className="rounded-lg px-3 py-1.5 disabled:opacity-40 hover:bg-[var(--panel)]"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={!data.has_next}
                    onClick={() => setPage(page + 1)}
                    className="rounded-lg px-3 py-1.5 disabled:opacity-40 hover:bg-[var(--panel)]"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </PageSection>
    </PageShell>
  );
}
