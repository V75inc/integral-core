import { useState, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, ClipboardList, MessageSquare } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { appsApi, tracksApi } from '../api';
import { TrackModal } from '../components/tracks/TrackModal';
import { PinButton } from '../components/sidebar/PinButton';
import {
  Skeleton,
  EmptyState,
  Button,
  IconWell,
  LINE_ICON_STROKE,
  PageHeading,
  PageShell,
  PageSection,
  filterBar,
  SearchRow,
  PageSearchInput,
  TrackDot
} from '../components/ui';
import { formatRelativeTime } from '../utils';
import { humanizeFieldKey } from '../utils/humanizeFieldKey';
import { useSetCrumbs } from '../context/CrumbsContext';
import { useScope } from '../context/ScopeContext';
import { useAssistantDockOptional } from '../context/AssistantDockContext';
import { WorkspaceCreationRightsNotice } from '../components/collab/WorkspaceCreationRightsNotice';
import { useWorkspaceCreationRights } from '../hooks/useWorkspaceCreationRights';
import { appsListQueryKey, tracksListQueryKey } from '../queryKeys';
import type { Track } from '../types';
import { usePublishPageContext } from '../hooks/usePublishPageContext';

type SectionKind = 'app' | 'standalone' | 'anchor';

type TrackSection = {
  kind: SectionKind;
  /** App id for ``kind: 'app'``; ``null`` for the catch-all sections. */
  appId: string | null;
  appName: string;
  tracks: Track[];
  /** Phase 36-B — app's workspace-scoped position (only set for ``kind: 'app'``). */
  appPosition?: number | null;
};

function sortTracksByRecency(tracks: Track[]): Track[] {
  return [...tracks].sort((a, b) => {
    const da = a.updated_at || a.created_at || '';
    const db = b.updated_at || b.created_at || '';
    return db.localeCompare(da);
  });
}

/** Groups tracks into three buckets surfaced in render order:
 *
 *  1. App sections — one per parent App, alphabetical by app name.
 *  2. ``Other tracks`` — user-created standalone tracks (no App, no anchor).
 *  3. ``Entry extensions`` — tracks auto-spawned by an Entry's relation
 *     field (``anchor_source`` present); de-emphasized below.
 *
 *  Anchor tracks dominate App membership: a track that is both anchored and
 *  CONTAINS-ed under an App is rendered under ``Entry extensions``, since
 *  its lifecycle is entry-driven and surfacing it inline would clutter the
 *  app's primary track list.
 */
function buildTrackSections(
  tracks: Track[],
  appPositions: Map<string, number>,
): TrackSection[] {
  const byApp = new Map<string, { name: string; tracks: Track[] }>();
  const appIds: string[] = [];
  const standalone: Track[] = [];
  const anchored: Track[] = [];

  for (const t of tracks) {
    if (t.anchor_source) {
      anchored.push(t);
      continue;
    }
    const sid = t.app?.id;
    if (sid) {
      let g = byApp.get(sid);
      if (!g) {
        g = { name: (t.app!.name || '').trim() || 'App', tracks: [] };
        byApp.set(sid, g);
        appIds.push(sid);
      }
      g.tracks.push(t);
    } else {
      standalone.push(t);
    }
  }

  const sentinel = Number.POSITIVE_INFINITY;
  const sections: TrackSection[] = appIds
    .map(id => {
      const g = byApp.get(id)!;
      return {
        kind: 'app' as const,
        appId: id,
        appName: g.name,
        tracks: sortTracksByRecency(g.tracks),
        appPosition: appPositions.get(id)
      };
    })
    .sort((a, b) => {
      const pa = a.appPosition ?? sentinel;
      const pb = b.appPosition ?? sentinel;
      if (pa !== pb) return pa - pb;
      return a.appName.localeCompare(b.appName, undefined, {
        sensitivity: 'base'
      });
    });

  if (standalone.length) {
    sections.push({
      kind: 'standalone',
      appId: null,
      appName: 'Other tracks',
      tracks: sortTracksByRecency(standalone)
    });
  }
  if (anchored.length) {
    sections.push({
      kind: 'anchor',
      appId: null,
      appName: 'Entry extensions',
      tracks: sortTracksByRecency(anchored)
    });
  }
  return sections;
}

function tracksPageErrorMessage(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } } };
  return String(e?.response?.data?.detail || 'Failed to load tracks');
}

export function TracksPage() {
  useSetCrumbs([{ label: 'Tracks' }]);
  const navigate = useNavigate();
  const dock = useAssistantDockOptional();
  const { activeWorkspace } = useScope();
  const { canCreateTracks, lacksTrackCreationInOrg } = useWorkspaceCreationRights();
  const scopeLabel = activeWorkspace?.name?.trim() || 'Workspace';
  const workspaceId = activeWorkspace?.id ?? '__none__';

  const openHarnessChat = () => {
    if (dock) {
      dock.openDock({ view: 'chat' });
      return;
    }
    navigate('/agent');
  };

  const tracksQuery = useQuery({
    queryKey: [...tracksListQueryKey(''), workspaceId] as const,
    queryFn: () => tracksApi.list({ limit: 100 }),
    enabled: Boolean(activeWorkspace?.id)
  });
  const appsQuery = useQuery({
    queryKey: appsListQueryKey('tracks-page', workspaceId),
    queryFn: () => appsApi.list(),
    enabled: Boolean(activeWorkspace?.id)
  });

  const tracks = useMemo(() => tracksQuery.data ?? [], [tracksQuery.data]);
  const apps = useMemo(() => appsQuery.data ?? [], [appsQuery.data]);

  usePublishPageContext({
    pageKind: 'tracks_list',
    visibleData: {
      tracks: tracks.map((tr) => ({ id: tr.id, title: tr.title || undefined })),
      total_count: tracks.length,
    },
    metadata: {
      workspace_name: activeWorkspace?.name,
      app_count: apps.length,
    },
  });
  const loading = tracksQuery.isPending || appsQuery.isPending;
  const error =
    tracksQuery.isError
      ? tracksPageErrorMessage(tracksQuery.error)
      : null;

  const [showModal, setShowModal] = useState(false);
  const [search, setSearch] = useState('');
  const [anchorExpanded, setAnchorExpanded] = useState(false);

  const reload = () => {
    void tracksQuery.refetch();
    void appsQuery.refetch();
  };

  const q = search.trim().toLowerCase();
  const { filtered, sections } = useMemo(() => {
    const f = tracks.filter(t => {
      if (!q) return true;
      return (
        t.title.toLowerCase().includes(q) ||
        (t.purpose || '').toLowerCase().includes(q) ||
        (t.app?.name || '').toLowerCase().includes(q) ||
        (t.anchor_source?.entry_title || '').toLowerCase().includes(q)
      );
    });
    const appPositions = new Map<string, number>();
    for (const a of apps) {
      if (typeof a.position === 'number') appPositions.set(a.id, a.position);
    }
    const sec = buildTrackSections(f, appPositions);
    return { filtered: f, sections: sec };
  }, [tracks, q, apps]);
  const anchorSectionOpen = anchorExpanded || q.length > 0;

  return (
    <PageShell>
      <PageSection>
        <header className="mb-8 md:mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <PageHeading>Tracks</PageHeading>
            <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
              <span>
                {tracks.length} {tracks.length === 1 ? 'track' : 'tracks'} in {scopeLabel}
              </span>
              <span aria-hidden>·</span>
              <span>One workstream per initiative, client, or focus area</span>
            </div>
          </div>
          {canCreateTracks ? (
            <Button
              className="shrink-0 self-start sm:self-end w-full sm:w-auto"
              variant="primary"
              size="sm"
              icon={<Plus size={14} strokeWidth={LINE_ICON_STROKE} />}
              onClick={() => setShowModal(true)}
            >
              New track
            </Button>
          ) : null}
          <WorkspaceCreationRightsNotice
            resource="tracks"
            show={lacksTrackCreationInOrg}
            className="mt-2 sm:mt-0 sm:ml-auto sm:max-w-md sm:text-right"
          />
        </header>
      </PageSection>

      <PageSection.Separator />

      <PageSection className={filterBar.sectionTop}>
        {error && (
          <div
            className="mb-6 rounded-[var(--radius-card)] border border-[color:var(--danger-fg)]/30 bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-fg)]"
            role="alert"
          >
            {error}
            <button type="button" className="ml-3 underline" onClick={reload}>
              Retry
            </button>
          </div>
        )}

        <SearchRow>
          <PageSearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search tracks…"
          />
        </SearchRow>

        {loading ? (
          <div className="space-y-1">
            {[1, 2, 3, 4, 5].map(i => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={
              <IconWell size="lg" aria-hidden>
                <ClipboardList size={22} strokeWidth={LINE_ICON_STROKE} />
              </IconWell>
            }
            title={search ? 'No tracks found' : 'No tracks yet'}
            description={
              search
                ? 'Try a different search term.'
                : 'Create your first track to start organizing your work.'
            }
            action={
              !search ? (
                <div className="flex flex-wrap gap-2 justify-center">
                  {canCreateTracks ? (
                    <Button
                      variant="primary"
                      size="sm"
                      icon={<Plus size={14} strokeWidth={LINE_ICON_STROKE} />}
                      onClick={() => setShowModal(true)}
                    >
                      Create track
                    </Button>
                  ) : null}
                  <Button
                    variant={canCreateTracks ? 'ghost' : 'primary'}
                    size="sm"
                    icon={
                      <MessageSquare size={14} strokeWidth={LINE_ICON_STROKE} />
                    }
                    onClick={openHarnessChat}
                  >
                    Ask Integral to scaffold…
                  </Button>
                </div>
              ) : undefined
            }
          />
        ) : (
          <div className="flex flex-col gap-10">
            {sections.map(section => {
              const headingId =
                section.kind === 'app'
                  ? `tracks-app-${section.appId}`
                  : section.kind === 'anchor'
                  ? 'tracks-anchor-heading'
                  : 'tracks-other-heading';
              const isAnchor = section.kind === 'anchor';
              const showList = isAnchor ? anchorSectionOpen : true;
              return (
              <section
                key={
                  section.kind === 'app' ? section.appId! : section.kind
                }
                aria-labelledby={headingId}
              >
                <div className="mb-3 flex items-baseline justify-between gap-3">
                  {isAnchor ? (
                    <button
                      type="button"
                      id={headingId}
                      onClick={() => setAnchorExpanded(v => !v)}
                      aria-expanded={anchorSectionOpen}
                      className="text-[12px] uppercase tracking-[0.16em] text-[var(--text-subtle)]/80 font-medium hover:text-[var(--text-muted)] transition-colors duration-fast inline-flex items-center gap-1.5"
                    >
                      <span
                        aria-hidden
                        className="inline-block w-2 text-center"
                      >
                        {anchorSectionOpen ? '▾' : '▸'}
                      </span>
                      {section.appName}
                    </button>
                  ) : (
                    <h2
                      id={headingId}
                      className="text-[13px] uppercase tracking-[0.16em] text-[var(--text-subtle)] font-medium"
                    >
                      {section.kind === 'app' ? (
                        <Link
                          to={`/apps/${section.appId}`}
                          className="hover:text-[var(--text-muted)] transition-colors duration-fast"
                        >
                          {section.appName}
                        </Link>
                      ) : (
                        section.appName
                      )}
                    </h2>
                  )}
                  <span
                    className={
                      isAnchor
                        ? 'text-xs tabular-nums text-[var(--text-subtle)]/80'
                        : 'text-xs tabular-nums text-[var(--text-subtle)]'
                    }
                  >
                    {section.tracks.length}{' '}
                    {section.tracks.length === 1 ? 'track' : 'tracks'}
                  </span>
                </div>
                {showList && (
                <ul>
                  {section.tracks.map(t => (
                    <li
                      key={t.id}
                      className="
                        grid grid-cols-[auto_minmax(0,1fr)_auto_auto] gap-x-[18px] items-center
                        border-b border-[var(--border-subtle)] last:border-b-0
                        px-4 rounded-[2px]
                        transition-colors duration-fast
                        hover:bg-[var(--panel)]
                      "
                    >
                      <Link
                        to={`/tracks/${t.id}`}
                        className="
                          col-span-3 grid grid-cols-subgrid items-start py-4
                          focus-visible:outline-none
                        "
                      >
                        <TrackDot
                          color={t.accent_color}
                          size="md"
                          className="mt-[8px] shrink-0"
                          title={t.title}
                        />
                        <div className="min-w-0">
                          <p className="text-[15px] font-medium text-[var(--text)] truncate">
                            {t.title}
                          </p>
                          {isAnchor && t.anchor_source ? (
                            <p className="text-xs text-[var(--text-subtle)] mt-0.5 line-clamp-1">
                              Extends{' '}
                              <span className="text-[var(--text-muted)]">
                                {t.anchor_source.entry_title || 'entry'}
                              </span>
                              {t.anchor_source.field_key
                                ? ` · ${humanizeFieldKey(t.anchor_source.field_key)}`
                                : ''}
                            </p>
                          ) : t.purpose || t.description ? (
                            <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
                              {t.purpose || t.description}
                            </p>
                          ) : null}
                        </div>
                        <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 text-right pt-0.5">
                          <div>
                            {t.entry_count || 0}{' '}
                            {(t.entry_count || 0) === 1 ? 'entry' : 'entries'}
                          </div>
                          {t.updated_at ? (
                            <div className="mt-0.5">
                              {formatRelativeTime(t.updated_at)}
                            </div>
                          ) : null}
                        </div>
                      </Link>
                      <PinButton kind="track" id={t.id} label={t.title} size="sm" />
                    </li>
                  ))}
                </ul>
                )}
              </section>
              );
            })}
          </div>
        )}

        <TrackModal
          open={showModal}
          onClose={() => setShowModal(false)}
          onCreated={() => {
            void tracksQuery.refetch();
          }}
        />
      </PageSection>
    </PageShell>
  );
}
