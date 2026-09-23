import { useState, useEffect, useCallback, useMemo } from 'react';
import { usePhantomClickGuard } from '../hooks/usePhantomClickGuard';
import { Link } from 'react-router-dom';
import { Layers, Package, GripVertical } from 'lucide-react';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { appsApi, workspacesApi } from '../api';
import type { Workspace } from '../api/workspaces';
import { useToast } from '../context/ToastContext';
import {
  Button,
  EmptyState,
  Skeleton,
  IconWell,
  LINE_ICON_STROKE,
  PageHeading,
  PageShell,
  PageSection,
  filterBar,
  SearchRow,
  PageSearchInput,
  TrackDot,
} from '../components/ui';
import { AppModal } from '../components/apps/AppModal';
import { AppManagerDialog } from '../components/apps/AppManagerDialog';
import { PinButton } from '../components/sidebar/PinButton';
import { WorkspaceCreationRightsNotice } from '../components/collab/WorkspaceCreationRightsNotice';
import { useSetCrumbs } from '../context/CrumbsContext';
import { useScope } from '../context/ScopeContext';
import { useWorkspaceCreationRights } from '../hooks/useWorkspaceCreationRights';
import type { App } from '../types';
import { formatRelativeTime } from '../utils';
import { usePublishPageContext } from '../hooks/usePublishPageContext';
import { CHANGE_EVENT_APPLIED } from '../hooks/useChangeEventInvalidation';
import type { ActivityEvent } from '../utils/changeEvent';

export function AppsPage() {
  useSetCrumbs([{ label: 'Apps' }]);
  const { activeWorkspace } = useScope();
  const { showToast } = useToast();
  const { canCreateApps, lacksAppCreationInOrg } = useWorkspaceCreationRights();
  const scopeLabel = activeWorkspace?.name?.trim() || 'Workspace';
  // Phase 36 — only allow drag-reorder when we're scoped to a single
  // workspace AND the caller has admin/owner rights (the backend
  // endpoint rejects everyone else). Outside a scoped workspace the
  // list spans multiple workspaces so a per-workspace position is
  // meaningless; render static rows instead.
  const canReorderApps =
    !!activeWorkspace?.id &&
    (activeWorkspace?.your_role === 'owner' ||
      activeWorkspace?.your_role === 'admin');
  const [apps, setApps] = useState<App[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  usePublishPageContext({
    pageKind: 'apps_list',
    metadata: {
      workspace_name: activeWorkspace?.name,
      app_count: apps.length,
      // `visibleData` models entries and tracks only, so the app names ride
      // in metadata rather than being dropped.
      app_names: apps.map((a) => a.name).filter(Boolean).slice(0, 25),
    },
  });
  const [managerModal, setManagerModal] = useState(false);
  const [blankAppModal, setBlankAppModal] = useState(false);
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Fetch independently so a workspace-list failure doesn't blank the apps list.
      const [appsRes, wsRes] = await Promise.allSettled([
        appsApi.list(),
        workspacesApi.list(),
      ]);
      if (appsRes.status === 'fulfilled') {
        setApps(appsRes.value);
      } else {
        const e = appsRes.reason as { response?: { data?: { detail?: string } } };
        setError(String(e?.response?.data?.detail || 'Failed to load apps'));
        setApps([]);
      }
      setWorkspaces(wsRes.status === 'fulfilled' ? wsRes.value : []);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refetch on workspace switch — /apps is scoped server-side via
  // X-Integral-Scope, so the active workspace decides what we receive.
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id]);

  // Agent writes are published as governed ChangeEvents. This page keeps its
  // list in local state, so React Query invalidation alone cannot refresh a
  // page the user opened while an authorized build was materializing.
  useEffect(() => {
    const onChangeEvent = (event: Event) => {
      const detail = (event as CustomEvent<ActivityEvent>).detail;
      if (detail?.action?.startsWith('app.')) {
        void load();
      }
    };
    window.addEventListener(CHANGE_EVENT_APPLIED, onChangeEvent);
    return () => window.removeEventListener(CHANGE_EVENT_APPLIED, onChangeEvent);
  }, [load]);

  const q = search.trim().toLowerCase();
  const filteredApps = apps.filter(s => {
    if (!q) return true;
    const wsName = (workspaces.find(w => w.id === s.workspace_id)?.name || '').toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q) ||
      wsName.includes(q)
    );
  });


  return (
    <PageShell>
      <PageSection>
        <header className="mb-8 md:mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <PageHeading>Apps</PageHeading>
            <div className="mt-3 md:mt-3.5 flex flex-wrap items-center gap-x-4 md:gap-x-6 gap-y-2 text-sm text-[var(--text-subtle)]">
              <span>
                {apps.length}{' '}
                {apps.length === 1 ? 'app' : 'apps'} in {scopeLabel}
              </span>
              <span aria-hidden>·</span>
              <span>Group tracks under an App; link to a workspace for team-wide access</span>
            </div>
          </div>
          <div className="flex gap-2 shrink-0 self-start sm:self-end w-full sm:w-auto">
            {canCreateApps ? (
              <Button
                variant="primary"
                size="sm"
                icon={<Package size={14} strokeWidth={LINE_ICON_STROKE} />}
                onClick={() => setManagerModal(true)}
              >
                Manage apps
              </Button>
            ) : null}
          </div>
          <WorkspaceCreationRightsNotice
            resource="apps"
            show={lacksAppCreationInOrg}
            className="mt-2 sm:mt-0 sm:ml-auto sm:max-w-md sm:text-right"
          />
        </header>
      </PageSection>

      <PageSection.Separator />

      <PageSection className={filterBar.sectionTop}>
        <SearchRow>
          <PageSearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search apps…"
          />
        </SearchRow>

        {error && (
          <div
            className="mb-6 rounded-[var(--radius-card)] border border-[color:var(--danger-fg)]/30 bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-fg)]"
            role="alert"
          >
            {error}
            <button type="button" className="ml-3 underline" onClick={load}>
              Retry
            </button>
          </div>
        )}

        {loading ? (
          <div className="space-y-1">
            {[1, 2, 3].map(i => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        ) : filteredApps.length === 0 ? (
          apps.length === 0 ? (
            <EmptyState
              icon={
                <IconWell size="lg" aria-hidden>
                  <Layers size={22} strokeWidth={LINE_ICON_STROKE} />
                </IconWell>
              }
              title="No Apps"
              description="Create an App to bundle related tracks and tools under a shared structure."
              action={
                canCreateApps ? (
                  <Button
                    variant="primary"
                    size="sm"
                    icon={<Package size={14} strokeWidth={LINE_ICON_STROKE} />}
                    onClick={() => setManagerModal(true)}
                  >
                    Manage apps
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <EmptyState
              icon={
                <IconWell size="lg" aria-hidden>
                  <Layers size={22} strokeWidth={LINE_ICON_STROKE} />
                </IconWell>
              }
              title="No Apps found"
              description="Try a different search term."
            />
          )
        ) : (
          <SortableAppList
            apps={filteredApps}
            workspaces={workspaces}
            canReorder={canReorderApps}
            onReordered={(next) => {
              // Optimistic: snap UI to new order, then persist. On failure
              // reload to restore the canonical server order.
              setApps((prev) => {
                const visibleIds = new Set(next.map((a) => a.id));
                const tail = prev.filter((a) => !visibleIds.has(a.id));
                return [...next, ...tail];
              });
              if (activeWorkspace?.id) {
                workspacesApi
                  .reorderApps(activeWorkspace.id, next.map((a) => a.id))
                  .catch(() => {
                    showToast('Failed to save app order', 'error');
                    load();
                  });
              }
            }}
          />
        )}

      <AppManagerDialog
        isOpen={managerModal}
        onClose={() => setManagerModal(false)}
        apps={apps}
        onChanged={load}
        onCreateBlankApp={() => {
          setManagerModal(false);
          setBlankAppModal(true);
        }}
      />
      <AppModal
        open={blankAppModal}
        mode="blank"
        onClose={() => setBlankAppModal(false)}
        onSaved={sp => {
          setApps(p => [sp, ...p]);
          setBlankAppModal(false);
        }}
      />
      </PageSection>
    </PageShell>
  );
}

/** Phase 36 — drag-drop wrapper for the Apps list inside a workspace.
 *  Hides the drag handle (and skips the DndContext entirely) when the
 *  user lacks admin/owner rights on the active workspace. */
function SortableAppList({
  apps,
  workspaces,
  canReorder,
  onReordered,
}: {
  apps: App[];
  workspaces: Workspace[];
  canReorder: boolean;
  onReordered: (next: App[]) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );
  const ids = useMemo(() => apps.map(a => a.id), [apps]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = apps.findIndex(a => a.id === active.id);
    const newIndex = apps.findIndex(a => a.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    onReordered(arrayMove(apps, oldIndex, newIndex));
  };

  if (!canReorder) {
    return (
      <ul>
        {apps.map(app => (
          <AppListRow key={app.id} app={app} workspaces={workspaces} />
        ))}
      </ul>
    );
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        <ul>
          {apps.map(app => (
            <SortableAppListRow key={app.id} app={app} workspaces={workspaces} />
          ))}
        </ul>
      </SortableContext>
    </DndContext>
  );
}

function SortableAppListRow({ app, workspaces }: { app: App; workspaces: Workspace[] }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: app.id });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  usePhantomClickGuard(isDragging);
  return (
    <AppListRow
      app={app}
      workspaces={workspaces}
      sortable={{
        setNodeRef,
        style,
        attributes: attributes as unknown as Record<string, unknown>,
        listeners,
        isDragging,
      }}
    />
  );
}

interface AppListRowSortable {
  setNodeRef: (node: HTMLElement | null) => void;
  style: React.CSSProperties;
  attributes: Record<string, unknown>;
  listeners: Record<string, unknown> | undefined;
  isDragging: boolean;
}

function AppListRow({
  app,
  workspaces,
  sortable,
}: {
  app: App;
  workspaces: Workspace[];
  sortable?: AppListRowSortable;
}) {
  const wsName = app.workspace_id
    ? workspaces.find(w => w.id === app.workspace_id)?.name || 'Workspace'
    : null;
  return (
    <li
      ref={sortable?.setNodeRef}
      style={sortable?.style}
      className={`
        group/row relative grid grid-cols-[auto_minmax(0,1fr)_auto_auto] gap-x-[18px] items-center
        border-b border-[var(--border-subtle)] last:border-b-0
        px-4 rounded-[2px]
        transition-colors duration-fast
        hover:bg-[var(--panel)]
        ${sortable?.isDragging ? 'opacity-60' : ''}
      `}
    >
      <Link
        to={`/apps/${app.id}`}
        className="
          col-span-3 grid grid-cols-subgrid items-start py-4
          focus-visible:outline-none
        "
      >
        <div className="relative shrink-0 mt-[8px] flex h-4 w-4 items-center justify-center">
          <TrackDot
            color={app.accent_color}
            size="md"
            className={
              sortable
                ? 'transition-opacity duration-fast group-hover/row:opacity-0'
                : ''
            }
            title={app.name}
          />
          {sortable && (
            <button
              type="button"
              aria-label={`Reorder ${app.name}`}
              {...(sortable.attributes as React.HTMLAttributes<HTMLButtonElement>)}
              {...(sortable.listeners as React.HTMLAttributes<HTMLButtonElement>)}
              onClick={e => e.preventDefault()}
              className="
                absolute inset-0 flex items-center justify-center
                text-[var(--text-subtle)] hover:text-[var(--text-muted)]
                opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100
                transition-opacity duration-fast
                cursor-grab active:cursor-grabbing
              "
            >
              <GripVertical size={14} strokeWidth={LINE_ICON_STROKE} aria-hidden />
            </button>
          )}
        </div>
        <div className="min-w-0">
          <p className="text-[15px] font-medium text-[var(--text)] truncate">
            {app.name}
          </p>
          <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-1">
            {app.description || 'No description'}
          </p>
        </div>
        <div className="text-xs text-[var(--text-subtle)] tabular-nums shrink-0 text-right pt-0.5">
          <div>{wsName || 'Personal'}</div>
          {app.updated_at ? (
            <div className="mt-0.5">{formatRelativeTime(app.updated_at)}</div>
          ) : null}
        </div>
      </Link>
      <PinButton kind="app" id={app.id} label={app.name} size="sm" />
    </li>
  );
}
