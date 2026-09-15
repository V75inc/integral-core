import { useCallback, useEffect, useMemo } from 'react';
import { notifyApiFailure } from '../components/system';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { missionControlApi } from '../api';
import { formatRelativeTime } from '../utils';
import { markdownToPlainExcerpt } from '../utils/markdownExcerpt';
import type { App, Entry, Track } from '../types';
// Phase 9 Plan 09-02 (NOTIF-01) — migrated to the canonical useNotifications
// hook. Mission Control's "unread" metric tile now shares the single
// notifications cache used by NotificationsPage + the header bell.
import { useNotifications } from '../hooks/useNotifications';
import { useSetCrumbs } from '../context/CrumbsContext';
import { workspaceIdOf, useScope } from '../context/ScopeContext';
import { missionControlSnapshotQueryKey } from '../queryKeys';
import { Avatar, PageHeading, PageShell, PageSection, Skeleton, TrackDot } from '../components/ui';
import { PendingInvitationsPanel } from '../components/invitations/PendingInvitationsPanel';
import { usePublishPageContext } from '../hooks/usePublishPageContext';

/**
 * MissionControlPage — cross-scope birds-eye.
 *
 * Unlike the Feed/Chat/Sidebar which honour the active workspace scope,
 * Mission Control deliberately ignores it. It's the only surface that
 * shows highlights across **all** workspaces a user belongs to:
 *
 *   1. Metrics — totals across everything the user can read.
 *   2. Workspaces — Personal + each org as a single row, each row
 *      summarising last activity. Click to switch scope and dive in.
 *   3. Recent apps — top 5 by recency, scope-agnostic, with a small
 *      workspace chip. Section hides entirely when the user has no
 *      apps anywhere readable.
 *   4. Tracks in motion — top 5 by recency, scope-agnostic, with a
 *      small workspace chip per row so the user knows where each
 *      lives at a glance.
 *   5. Latest activity — same treatment for entries.
 *
 * Information density is deliberately capped: a small fixed number of
 * rows per section, one secondary metadata column, no nested grids.
 * The ⌘K command palette is the escape hatch for anything else.
 */

const WORKSPACES_LIMIT = 6;
const APPS_LIMIT = 5;
const MOTION_LIMIT = 5;
const ACTIVITY_LIMIT = 6;


interface WorkspaceSummary {
  id: string;
  kind: 'personal' | 'org';
  label: string;
  accent?: string;
  avatarUrl?: string;
  lastActivityIso?: string;
  href: string;
}

export function MissionControlPage() {
  // Mission Control is the home surface; Layout's leading "Home" crumb
  // covers it without a tail.
  useSetCrumbs([]);

  const queryClient = useQueryClient();
  const { workspaces, setScope, scope } = useScope();
  const {
    unreadCount,
    loading: notificationsLoading,
    error: notificationsError,
    refetch: refetchNotifications
  } = useNotifications();

  const missionControlQuery = useQuery({
    queryKey: missionControlSnapshotQueryKey(scope?.workspaceId),
    queryFn: () => missionControlApi.getSnapshot(50)
  });

  const tracks: Track[] = useMemo(
    () => missionControlQuery.data?.tracks ?? [],
    [missionControlQuery.data],
  );
  const apps: App[] = useMemo(
    () => missionControlQuery.data?.apps ?? [],
    [missionControlQuery.data],
  );
  const previewEntries: Entry[] = missionControlQuery.data?.preview_entries ?? [];

  usePublishPageContext({
    pageKind: 'mission_control',
    // Counts in metadata + a small sample — full inventory is a tool read,
    // not prompt ballast on every Mission Control greeting.
    visibleData: {
      tracks: tracks.slice(0, 5).map((tr) => ({ id: tr.id, title: tr.title || undefined })),
      entries: previewEntries.slice(0, 5).map((e) => ({
        id: e.id,
        title: e.title || undefined,
        status: e.status || undefined,
        entry_type: e.type || undefined,
      })),
      total_count: Math.max(tracks.length, previewEntries.length),
    },
    metadata: {
      workspace_count: workspaces.length,
      app_count: apps.length,
      track_count: tracks.length,
      entry_preview_count: previewEntries.length,
      unread_notifications: unreadCount,
    },
  });
  const appsLoading = missionControlQuery.isPending;
  const countersLoading = missionControlQuery.isPending;

  const loading =
    missionControlQuery.isPending ||
    notificationsLoading;

  // Errors surface through the global system-bar — see useEffect below.

  const retryAll = useCallback(() => {
    void Promise.all([
      queryClient.invalidateQueries({
        queryKey: missionControlSnapshotQueryKey(scope?.workspaceId)
      }),
      refetchNotifications(),
    ]);
  }, [queryClient, refetchNotifications, scope?.workspaceId]);

  // Attach page-specific retry to the global API-failure notification
  // whenever a Mission Control load fails. The axios interceptor pushes
  // the notification first; this effect upgrades it with a Retry action.
  const firstError = missionControlQuery.error ?? notificationsError ?? null;
  useEffect(() => {
    if (firstError) {
      notifyApiFailure(firstError, {
        context: 'loading Mission Control',
        onRetry: retryAll
      });
    }
  }, [firstError, retryAll]);

  const unread = unreadCount;

  const activeTracks = missionControlQuery.data?.active_tracks ?? 0;
  const entriesToday = missionControlQuery.data?.entries_today ?? 0;

  // Group tracks by their containing Workspace (workspaceIdOf reads the
  // direct workspace_id and falls back to the parent App's id).
  const workspaceSummaries = useMemo<WorkspaceSummary[]>(() => {
    const byWorkspace = new Map<
      string,
      { tracks: Track[]; lastActivityIso?: string }
    >();
    for (const t of tracks) {
      const wsId = workspaceIdOf(t);
      if (!wsId) continue;
      const bucket = byWorkspace.get(wsId) ?? { tracks: [] };
      bucket.tracks.push(t);
      const candidate = t.updated_at || t.created_at;
      if (
        candidate &&
        (!bucket.lastActivityIso || candidate > bucket.lastActivityIso)
      ) {
        bucket.lastActivityIso = candidate;
      }
      byWorkspace.set(wsId, bucket);
    }

    const summaries: WorkspaceSummary[] = workspaces.map(ws => {
      const bucket = byWorkspace.get(ws.id) ?? { tracks: [] };
      return {
        id: ws.id,
        kind: ws.kind === 'personal' ? 'personal' : 'org',
        label: ws.name?.trim() || (ws.kind === 'personal' ? 'Personal' : 'Workspace'),
        accent: ws.accent_color,
        avatarUrl: ws.avatar_url,
        lastActivityIso: bucket.lastActivityIso,
        href: ws.kind === 'personal' ? '/tracks' : `/workspaces/${ws.id}`
      };
    });

    return summaries
      .sort((a, b) => {
        if (a.kind === 'personal') return -1;
        if (b.kind === 'personal') return 1;
        const ax = a.lastActivityIso || '';
        const bx = b.lastActivityIso || '';
        return bx.localeCompare(ax);
      })
      .slice(0, WORKSPACES_LIMIT);
  }, [tracks, workspaces]);

  const recentTracks = useMemo(() => {
    const score = (t: Track) =>
      new Date(t.updated_at || t.created_at || 0).getTime();
    return [...tracks].sort((a, b) => score(b) - score(a)).slice(0, MOTION_LIMIT);
  }, [tracks]);

  const recentApps = useMemo(() => {
    const score = (a: App) =>
      new Date(a.updated_at || a.created_at || 0).getTime();
    return [...apps].sort((a, b) => score(b) - score(a)).slice(0, APPS_LIMIT);
  }, [apps]);

  // Build workspace chip lookup for activity + tracks rows.
  const workspaceChipFor = useMemo(() => {
    const wsById = new Map<string, typeof workspaces[number]>();
    for (const ws of workspaces) wsById.set(ws.id, ws);
    return (item: {
      workspace_id?: string | null;
      space?: { workspace_id?: string | null };
    }):
      | {
          label: string;
          accent?: string;
          avatarUrl?: string;
          isPersonal?: boolean;
        }
      | null => {
      const wsId = workspaceIdOf(item);
      if (!wsId) return null;
      const ws = wsById.get(wsId);
      if (!ws) return { label: 'Workspace' };
      return {
        label: ws.name?.trim() || (ws.kind === 'personal' ? 'Personal' : 'Workspace'),
        accent: ws.accent_color,
        avatarUrl: ws.avatar_url,
        isPersonal: ws.kind === 'personal'
      };
    };
  }, [workspaces]);

  const switchAndGo = (ws: WorkspaceSummary) => {
    setScope({ workspaceId: ws.id });
  };

  return (
    /* PageShell owns vertical rhythm; PageSections re-apply the gutter
       so the full-bleed separator below can span main column edge-to-edge. */
    <PageShell>
      <PageSection>
        {/* Editorial header */}
        <header className="mb-8 md:mb-10">
          <PageHeading>Mission Control</PageHeading>
          <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
            <span>
              {workspaces.length} workspace{workspaces.length === 1 ? '' : 's'}
            </span>
            <span aria-hidden>·</span>
            <span>
              {unread} unread {unread === 1 ? 'notification' : 'notifications'}
            </span>
            {previewEntries[0]?.created_at ? (
              <>
                <span aria-hidden>·</span>
                {/* B-MC-01: the stat aggregates across every workspace the
                    user can read (matches Mission Control's bird's-eye
                    framing below), so the label spells that out instead
                    of looking like an active-scope number. */}
                <span>
                  Last activity across workspaces{' '}
                  {formatRelativeTime(previewEntries[0].created_at)}
                </span>
              </>
            ) : null}
          </div>
          <p className="mt-3 text-sm text-[var(--text-muted)] max-w-2xl">
            Bird's-eye across every workspace you can read. Open a workspace to
            focus the sidebar and dive in.
          </p>
        </header>
      </PageSection>

      {/* Full-bleed section divider — main column edge to edge. */}
      <PageSection.Separator />

      <PageSection className="mt-8">
        {/* Load failures route through the global system-bar; the
            page renders the data it has + a quiet skeleton elsewhere. */}

        {/* Metrics — totals across everything readable. */}
        <section
          aria-label="At a glance"
          className="grid grid-cols-2 md:grid-cols-5 gap-x-10 gap-y-6 mb-12"
        >
          <Metric label="Workspaces" value={workspaces.length} />
          <Metric label="Total tracks" value={tracks.length} />
          <Metric label="Entries today" value={countersLoading ? '—' : entriesToday} to="/feed" />
          <Metric label="Unread" value={unread} />
          <Metric label="Active tracks" value={countersLoading ? '—' : activeTracks} />
        </section>

        <PendingInvitationsPanel />

        {/* Workspaces — birds-eye across all the user belongs to. */}
        <section aria-labelledby="mc-workspaces" className="mb-12">
          <div className="flex items-baseline justify-between mb-4">
            <h2
              id="mc-workspaces"
              className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
            >
              Workspaces
            </h2>
            <Link
              to="/workspaces"
              className="text-xs text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors duration-fast"
            >
              View all →
            </Link>
          </div>
          {loading ? (
            <WorkspaceSkeletonList />
          ) : workspaceSummaries.length === 0 ? (
            <p className="text-sm text-[var(--text-subtle)] italic py-6">
              No workspaces with tracks yet. Create a collaborative workspace or
              start a personal track.
            </p>
          ) : (
            <ul>
              {workspaceSummaries.map(ws => (
                <li key={ws.id}>
                  <Link
                    to={ws.href}
                    onClick={() => switchAndGo(ws)}
                    className="
                      grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4
                      border-b border-[var(--border-subtle)] last:border-b-0
                      px-4 rounded-[2px]
                      transition-colors duration-fast
                      hover:bg-[var(--panel)]
                    "
                  >
                    <Avatar
                      name={ws.label}
                      url={ws.avatarUrl}
                      size="sm"
                      ringVariant="none"
                      className="mt-[3px]"
                    />
                    <div className="min-w-0">
                      <p className="text-[15px] font-medium text-[var(--text)] truncate">
                        {ws.label}
                      </p>
                      <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
                        {ws.kind === 'personal'
                          ? 'Your private tracks and apps'
                          : 'Organization workspace'}
                      </p>
                    </div>
                    <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 text-right pt-0.5">
                      {ws.lastActivityIso ? (
                        <div>
                          {formatRelativeTime(ws.lastActivityIso)}
                        </div>
                      ) : (
                        <div className="italic">No recent activity</div>
                      )}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Recent apps — cross-scope; hidden entirely if user has no apps. */}
        {(appsLoading || recentApps.length > 0) && (
          <section aria-labelledby="mc-apps" className="mb-12">
            <div className="flex items-baseline justify-between mb-4">
              <h2
                id="mc-apps"
                className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
              >
                Recent apps
              </h2>
              <Link
                to="/apps"
                className="text-xs text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors duration-fast"
              >
                View all →
              </Link>
            </div>
            {appsLoading && recentApps.length === 0 ? (
              <RowSkeletonList rows={3} />
            ) : (
              <ul>
                {recentApps.map(app => {
                  const chip = workspaceChipFor(app);
                  return (
                    <li key={app.id}>
                      <Link
                        to={`/apps/${app.id}`}
                        className="
                          grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4
                          border-b border-[var(--border-subtle)] last:border-b-0
                          px-4 rounded-[2px]
                          transition-colors duration-fast
                          hover:bg-[var(--panel)]
                        "
                      >
                        <TrackDot
                          color={app.accent_color}
                          size="md"
                          className="mt-[8px] shrink-0"
                          title={app.name}
                        />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 min-w-0">
                            <p className="text-[15px] font-medium text-[var(--text)] truncate">
                              {app.name}
                            </p>
                            <ScopeChip chip={chip} />
                          </div>
                          {app.description ? (
                            <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
                              {app.description}
                            </p>
                          ) : null}
                        </div>
                        <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 text-right pt-0.5">
                          {app.updated_at || app.created_at ? (
                            <div>
                              {formatRelativeTime(app.updated_at || app.created_at!)}
                            </div>
                          ) : null}
                        </div>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}

        {/* Tracks in motion — cross-scope; each row carries a small workspace chip. */}
        <section aria-labelledby="mc-tracks" className="mb-12">
          <div className="flex items-baseline justify-between mb-4">
            <h2
              id="mc-tracks"
              className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
            >
              Tracks in motion
            </h2>
            <Link
              to="/tracks"
              className="text-xs text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors duration-fast"
            >
              View all →
            </Link>
          </div>
          {loading ? (
            <RowSkeletonList rows={3} />
          ) : recentTracks.length === 0 ? (
            <p className="text-sm text-[var(--text-subtle)] italic py-6">
              No tracks yet. Create one to get started.
            </p>
          ) : (
            <ul>
              {recentTracks.map(track => {
                const chip = workspaceChipFor(track);
                return (
                  <li key={track.id}>
                    <Link
                      to={`/tracks/${track.id}`}
                      className="
                        grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4
                        border-b border-[var(--border-subtle)] last:border-b-0
                        px-4 rounded-[2px]
                        transition-colors duration-fast
                        hover:bg-[var(--panel)]
                      "
                    >
                      <TrackDot
                        color={track.accent_color}
                        size="md"
                        className="mt-[8px] shrink-0"
                        title={track.title}
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <p className="text-[15px] font-medium text-[var(--text)] truncate">
                            {track.title}
                          </p>
                          <ScopeChip chip={chip} />
                        </div>
                        <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
                          {track.purpose ||
                            track.description ||
                            'No purpose yet.'}
                        </p>
                      </div>
                      <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 text-right pt-0.5">
                        <div>{track.entry_count || 0} {(track.entry_count || 0) === 1 ? 'entry' : 'entries'}</div>
                        {track.updated_at ? (
                          <div className="mt-0.5">
                            {formatRelativeTime(track.updated_at)}
                          </div>
                        ) : null}
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Latest activity — cross-scope. */}
        <section aria-labelledby="mc-latest" className="mb-12">
          <div className="flex items-baseline justify-between mb-4">
            <h2
              id="mc-latest"
              className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
            >
              Latest activity
            </h2>
            <Link
              to="/feed"
              className="text-xs text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors duration-fast"
            >
              View feed →
            </Link>
          </div>
          {loading ? (
            <RowSkeletonList rows={4} />
          ) : previewEntries.length === 0 ? (
            <p className="text-sm text-[var(--text-subtle)] italic py-6">
              Nothing to show yet — posts will surface here as your workspaces
              grow.
            </p>
          ) : (
            <ul>
              {previewEntries.slice(0, ACTIVITY_LIMIT).map(entry => {
                const trackOfEntry = tracks.find(t => t.id === entry.track_id);
                const chip = trackOfEntry ? workspaceChipFor(trackOfEntry) : null;
                return (
                  <li key={entry.id}>
                    <Link
                      to={
                        entry.track_id
                          ? `/tracks/${entry.track_id}?entry=${encodeURIComponent(entry.id)}`
                          : '/feed'
                      }
                      className="
                        grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4
                        border-b border-[var(--border-subtle)] last:border-b-0
                        px-4 rounded-[2px]
                        transition-colors duration-fast
                        hover:bg-[var(--panel)]
                      "
                    >
                      <TrackDot
                        size="md"
                        className="mt-[8px] shrink-0"
                        title={trackOfEntry?.title}
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <p className="text-[15px] font-medium text-[var(--text)] truncate">
                            {entry.title || `New ${entry.type} update`}
                          </p>
                          <ScopeChip chip={chip} />
                        </div>
                        <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
                          {entry.body
                            ? markdownToPlainExcerpt(entry.body, 200)
                            : 'Open this thread for details.'}
                        </p>
                      </div>
                      <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 pt-0.5">
                        {formatRelativeTime(entry.created_at)}
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </PageSection>
    </PageShell>
  );
}

/** Inline metric — label above, value below, no chrome. */
function Metric({
  label,
  value,
  to
}: {
  label: string;
  /** Pass a number for computed values; pass '—' while data is loading
   *  to show a neutral placeholder instead of a stale / wrong count. */
  value: number | string;
  /** When provided, the metric becomes a Link to the given path.
   *  Used for the "Entries today" tile so users can drill into the
   *  per-day feed instead of staring at a dead number. */
  to?: string;
}) {
  const body = (
    <>
      <p className="text-xs uppercase tracking-[0.08em] text-[var(--text-subtle)] font-medium">
        {label}
      </p>
      <p className="mt-2 text-[28px] font-semibold tracking-tight text-[var(--text)] tabular-nums leading-none">
        {value}
      </p>
    </>
  );
  if (to) {
    return (
      <Link
        to={to}
        className="
          min-w-0 rounded-[var(--radius-input)] -mx-1 -my-1 px-1 py-1
          transition-colors duration-fast
          hover:bg-[var(--panel-2)]
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]
        "
      >
        {body}
      </Link>
    );
  }
  return <div className="min-w-0">{body}</div>;
}

function ScopeChip({
  chip
}: {
  chip: { label: string; accent?: string; avatarUrl?: string } | null;
}) {
  // No chip data → the resource has no workspace (shouldn't normally
  // happen post-W2 backfill, but render a quiet placeholder).
  const label = chip?.label || 'Personal';
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted)] shrink-0"
      title={label}
    >
      <Avatar
        name={label}
        url={chip?.avatarUrl}
        size="xs"
        ringVariant="none"
      />
      <span className="truncate max-w-[110px]">{label}</span>
    </span>
  );
}

function WorkspaceSkeletonList() {
  return (
    <div>
      {[1, 2, 3].map(i => (
        <div
          key={i}
          className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4 border-b border-[var(--border-subtle)] last:border-b-0"
        >
          <Skeleton className="w-[18px] h-[18px] rounded-md mt-[6px] shrink-0" />
          <div className="min-w-0">
            <Skeleton className="h-4 w-[40%] max-w-[260px] rounded" />
            <Skeleton className="h-3 w-[60%] max-w-[400px] rounded mt-2" />
          </div>
          <div className="text-right space-y-1.5">
            <Skeleton className="h-3 w-16 rounded ml-auto" />
            <Skeleton className="h-3 w-20 rounded ml-auto" />
          </div>
        </div>
      ))}
    </div>
  );
}

function RowSkeletonList({ rows }: { rows: number }) {
  return (
    <div>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-[18px] py-4 border-b border-[var(--border-subtle)] last:border-b-0"
        >
          <Skeleton className="w-2.5 h-2.5 rounded-full mt-[7px] shrink-0" />
          <div className="min-w-0">
            <Skeleton className="h-4 w-[45%] max-w-[300px] rounded" />
            <Skeleton className="h-3 w-[65%] max-w-[440px] rounded mt-2" />
          </div>
          <Skeleton className="h-3 w-20 rounded shrink-0" />
        </div>
      ))}
    </div>
  );
}
