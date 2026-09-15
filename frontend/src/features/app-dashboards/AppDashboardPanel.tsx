import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import GridLayout from 'react-grid-layout/legacy';
import { GripVertical, LayoutDashboard, Plus, Sparkles, Trash2 } from 'lucide-react';
import 'react-grid-layout/css/styles.css';
import {
  dashboardsApi,
  type DashboardWidget,
  type DashboardWidgetTypeSpec
} from '../../api/dashboards';
import {
  Button,
  EmptyState,
  IconWell,
  LINE_ICON_STROKE,
  Skeleton
} from '../../components/ui';
import { useToast } from '../../context/ToastContext';
import { useChatPageFocus } from '../../context/ChatPageFocusContext';
import { Text } from '../../ui';
import { CreateDashboardForm } from './CreateDashboardForm';
import {
  DashboardWidgetRenderer,
  groupWidgetTypes
} from './DashboardWidgetRegistry';
import {
  layoutFromWidgets,
  layoutsEqual,
  needsDashboardReflow,
  nextWidgetPosition,
  packDashboardWidgets,
  type GridLayoutItem,
  widgetsFromLayout
} from './dashboardLayout';
import './dashboardWidgets.css';

export interface AppDashboardPanelProps {
  appId: string;
  appName?: string;
  canEdit: boolean;
  /** When true, omit the section heading — parent supplies tab chrome. */
  embedded?: boolean;
  /** Called after a dashboard is successfully created (e.g. switch tabs). */
  onDashboardCreated?: () => void;
}

export function AppDashboardPanel({
  appId,
  appName,
  canEdit,
  embedded = false,
  onDashboardCreated
}: AppDashboardPanelProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { setPageContext } = useChatPageFocus();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [localWidgets, setLocalWidgets] = useState<DashboardWidget[]>([]);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoPackedRef = useRef<string | null>(null);

  const listQuery = useQuery({
    queryKey: ['dashboards', appId],
    queryFn: () => dashboardsApi.list(appId)
  });

  const substrateQuery = useQuery({
    queryKey: ['dashboard-widget-substrate'],
    queryFn: () => dashboardsApi.getSubstrate()
  });

  const dashboards = useMemo(() => listQuery.data ?? [], [listQuery.data]);
  const activeDashboard = useMemo(
    () => dashboards.find(d => d.id === activeId) ?? dashboards[0] ?? null,
    [dashboards, activeId],
  );
  const activeDashboardId = activeDashboard?.id ?? null;
  const activeDashboardName = activeDashboard?.name ?? null;

  useEffect(() => {
    if (dashboards.length && !activeId) {
      setActiveId(dashboards.find(d => d.is_default)?.id ?? dashboards[0].id);
    }
  }, [dashboards, activeId]);

  useEffect(() => {
    if (!activeDashboardId) return;
    setPageContext({
      metadata: {
        ...(appName ? { app_name: appName } : {}),
        focused_dashboard_id: activeDashboardId,
        focused_dashboard_name: activeDashboardName
      }
    });
  }, [activeDashboardId, activeDashboardName, appName, setPageContext]);

  const dataQuery = useQuery({
    queryKey: ['dashboard-data', appId, activeDashboard?.id],
    queryFn: () =>
      dashboardsApi.getData(appId, activeDashboard!.id),
    enabled: !!activeDashboard?.id,
    refetchInterval: 60_000
  });

  const widgetData = dataQuery.data ?? {};

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['dashboards', appId] });
    if (activeDashboard?.id) {
      void queryClient.invalidateQueries({
        queryKey: ['dashboard-data', appId, activeDashboard.id]
      });
    }
  }, [queryClient, appId, activeDashboard?.id]);

  const createMutation = useMutation({
    mutationFn: (body: Parameters<typeof dashboardsApi.create>[1]) =>
      dashboardsApi.create(appId, body),
    onSuccess: d => {
      setActiveId(d.id);
      invalidate();
      onDashboardCreated?.();
      showToast('Dashboard created', 'success');
    },
    onError: () => showToast('Failed to create dashboard', 'error')
  });

  const updateMutation = useMutation({
    mutationFn: ({
      dashboardId,
      body
    }: {
      dashboardId: string;
      body: Parameters<typeof dashboardsApi.update>[2];
    }) => dashboardsApi.update(appId, dashboardId, body),
    onSuccess: () => invalidate(),
    onError: () => showToast('Failed to save dashboard', 'error')
  });

  const deleteMutation = useMutation({
    mutationFn: (dashboardId: string) =>
      dashboardsApi.remove(appId, dashboardId),
    onSuccess: () => {
      setActiveId(null);
      invalidate();
      showToast('Dashboard deleted', 'success');
    },
    onError: () => showToast('Failed to delete dashboard', 'error')
  });

  const updateDashboardRef = useRef(updateMutation.mutate);
  updateDashboardRef.current = updateMutation.mutate;

  useEffect(() => {
    if (!activeDashboard) return;
    const cols = activeDashboard.layout?.columns ?? 12;
    const raw = activeDashboard.widgets ?? [];
    const packed = packDashboardWidgets(raw, cols);
    setLocalWidgets(prev => (layoutsEqual(prev, packed) ? prev : packed));

    if (
      canEdit &&
      needsDashboardReflow(raw, cols) &&
      !layoutsEqual(raw, packed) &&
      activeDashboard.id !== autoPackedRef.current
    ) {
      autoPackedRef.current = activeDashboard.id;
      updateDashboardRef.current({
        dashboardId: activeDashboard.id,
        body: { widgets: packed }
      });
    }
    // Field-level deps deliberately: this effect auto-packs and SAVES the
    // layout, so re-running it on every refetch of the dashboards query
    // would write repeatedly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeDashboard?.id,
    activeDashboard?.widgets,
    activeDashboard?.layout?.columns,
    canEdit,
  ]);

  const scheduleSave = useCallback(
    (widgets: DashboardWidget[]) => {
      if (!activeDashboard || !canEdit) return;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        updateMutation.mutate({
          dashboardId: activeDashboard.id,
          body: { widgets }
        });
      }, 600);
    },
    [activeDashboard, canEdit, updateMutation],
  );

  // react-grid-layout types its callback payload as `readonly LayoutItem[]`,
  // which is not assignable to a mutable array param. Accept it readonly and
  // copy before handing it to code that expects a mutable list.
  const onLayoutChange = (layout: readonly GridLayoutItem[]) => {
    const next = widgetsFromLayout(localWidgets, [...layout]);
    setLocalWidgets(next);
    if (editMode) scheduleSave(next);
  };

  const autoArrange = () => {
    const cols = activeDashboard?.layout?.columns ?? 12;
    const packed = packDashboardWidgets(localWidgets, cols);
    setLocalWidgets(packed);
    scheduleSave(packed);
    showToast('Layout auto-arranged', 'success');
  };

  const addWidget = (spec: DashboardWidgetTypeSpec) => {
    const id = `w_${Date.now().toString(36)}`;
    const cols = activeDashboard?.layout?.columns ?? 12;
    const gw = spec.default_size?.w ?? 4;
    const gh = spec.default_size?.h ?? 3;
    const pos = nextWidgetPosition(localWidgets, gw, gh, cols);
    const chartDataSource =
      spec.type === 'chart_line'
        ? { kind: 'grouped_count', group_by: 'date' }
        : spec.type === 'chart_bar' || spec.type === 'chart_pie'
          ? { kind: 'grouped_count', group_by: 'status' }
          : {};
    const next: DashboardWidget = {
      id,
      type: spec.type,
      title: spec.label,
      grid: {
        x: pos.x,
        y: pos.y,
        w: gw,
        h: gh
      },
      config: {},
      data_source: chartDataSource
    };
    const widgets = [...localWidgets, next];
    setLocalWidgets(widgets);
    scheduleSave(widgets);
  };

  const removeWidget = (widgetId: string) => {
    const widgets = localWidgets.filter(w => w.id !== widgetId);
    setLocalWidgets(widgets);
    scheduleSave(widgets);
  };

  const handleSuggest = async () => {
    try {
      const suggestion = await dashboardsApi.suggest(appId);
      createMutation.mutate({
        name: suggestion.name,
        layout: suggestion.layout,
        widgets: suggestion.widgets,
        is_default: dashboards.length === 0
      });
    } catch {
      showToast('Could not suggest dashboard', 'error');
    }
  };

  const widgetTypeGroups = groupWidgetTypes(
    substrateQuery.data?.widget_types ?? [],
  );

  const rowHeight = activeDashboard?.layout?.row_height ?? 80;
  const columns = activeDashboard?.layout?.columns ?? 12;
  const containerRef = useRef<HTMLDivElement>(null);
  const [gridWidth, setGridWidth] = useState(900);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setGridWidth(w);
    });
    ro.observe(el);
    setGridWidth(el.clientWidth || 900);
    return () => ro.disconnect();
  }, [activeDashboard?.id]);

  return (
    <section className={embedded ? '' : 'mb-10'} aria-label="App dashboards">
      {!embedded ? (
        <div className="mb-4 flex items-center gap-2">
          <IconWell size="sm" aria-hidden>
            <LayoutDashboard size={16} strokeWidth={LINE_ICON_STROKE} />
          </IconWell>
          <Text variant="body" as="h2" className="font-semibold">
            Dashboards
          </Text>
        </div>
      ) : null}

      {listQuery.isPending ? (
        <Skeleton className="h-48 w-full rounded-[var(--radius-card)]" />
      ) : dashboards.length === 0 ? (
        <div className="space-y-4">
          <EmptyState
            icon={
              <IconWell size="lg" aria-hidden>
                <LayoutDashboard size={22} strokeWidth={LINE_ICON_STROKE} />
              </IconWell>
            }
            title="No dashboards yet"
            description="Create a dashboard manually, from a template, or ask Integral to suggest one for this App."
          />
          {canEdit ? (
            <CreateDashboardForm
              widgetTypes={substrateQuery.data?.widget_types ?? []}
              creating={createMutation.isPending}
              suggesting={createMutation.isPending}
              onCreateBlank={name =>
                createMutation.mutate({ name, widgets: [], is_default: true })
              }
              onCreateFromTemplate={(name, widgets) =>
                createMutation.mutate({ name, widgets, is_default: true })
              }
              onSuggest={() => void handleSuggest()}
            />
          ) : null}
        </div>
      ) : (
        <>
          <div className="dashboard-tab-row mb-4 flex items-center gap-3">
            <div className="dashboard-tab-scroll flex min-w-0 flex-1 items-center gap-2 overflow-x-auto pb-0.5">
              {dashboards.map(d => (
                <Button
                  key={d.id}
                  variant={d.id === activeDashboard?.id ? 'primary' : 'outline'}
                  size="sm"
                  className="shrink-0"
                  onClick={() => setActiveId(d.id)}
                >
                  {d.name}
                </Button>
              ))}
              {canEdit ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  icon={<Plus size={14} strokeWidth={LINE_ICON_STROKE} />}
                  onClick={() => {
                    const name = `Dashboard ${dashboards.length + 1}`;
                    createMutation.mutate({ name, widgets: [] });
                  }}
                >
                  New
                </Button>
              ) : null}
            </div>
            {activeDashboard && canEdit ? (
              <div className="flex shrink-0 items-center gap-2 border-l border-[var(--panel-border)] pl-3">
                {editMode ? (
                  <Button
                    variant="outline"
                    size="sm"
                    icon={<Sparkles size={14} strokeWidth={LINE_ICON_STROKE} />}
                    onClick={autoArrange}
                  >
                    Auto-arrange
                  </Button>
                ) : null}
                <Button
                  variant={editMode ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => setEditMode(v => !v)}
                >
                  {editMode ? 'Done editing' : 'Edit layout'}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  icon={<Trash2 size={14} strokeWidth={LINE_ICON_STROKE} />}
                  onClick={() => deleteMutation.mutate(activeDashboard.id)}
                >
                  Delete
                </Button>
              </div>
            ) : null}
          </div>

          {editMode && canEdit ? (
            <div className="dashboard-toolbar mb-4 flex flex-wrap gap-2">
              {Object.entries(widgetTypeGroups).map(([group, specs]) => (
                <div key={group} className="flex flex-wrap items-center gap-1">
                  <Text variant="meta" tone="muted" as="span" className="mr-1">
                    {group}:
                  </Text>
                  {specs.map(s => (
                    <Button
                      key={s.type}
                      variant="outline"
                      size="sm"
                      onClick={() => addWidget(s)}
                    >
                      + {s.label}
                    </Button>
                  ))}
                </div>
              ))}
            </div>
          ) : null}

          {activeDashboard ? (
            <div ref={containerRef} className="relative min-h-[320px] w-full">
              <GridLayout
                className={`dashboard-grid layout${editMode ? ' dashboard-grid--editing' : ''}`}
                layout={layoutFromWidgets(localWidgets)}
                cols={columns}
                rowHeight={rowHeight}
                width={gridWidth}
                margin={[12, 12]}
                compactType="vertical"
                isDraggable={editMode && canEdit}
                isResizable={editMode && canEdit}
                resizeHandles={['se', 's', 'e', 'sw', 'nw', 'ne', 'n', 'w']}
                draggableCancel=".dashboard-no-drag, button, a, input, textarea, .recharts-wrapper"
                onLayoutChange={onLayoutChange}
              >
                {localWidgets.map(w => (
                  <div key={w.id} className="dashboard-grid-item h-full">
                    {editMode ? (
                      <div className="dashboard-edit-chrome">
                        <span
                          className="dashboard-drag-handle"
                          title="Drag to move"
                          aria-label="Drag widget"
                        >
                          <GripVertical size={14} strokeWidth={LINE_ICON_STROKE} />
                        </span>
                        <button
                          type="button"
                          className="dashboard-edit-badge dashboard-no-drag"
                          onClick={() => removeWidget(w.id)}
                        >
                          Remove
                        </button>
                      </div>
                    ) : null}
                    <DashboardWidgetRenderer
                      type={w.type}
                      title={w.title}
                      data={widgetData[w.id] as Record<string, unknown> | undefined}
                      config={w.config}
                    />
                  </div>
                ))}
              </GridLayout>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
