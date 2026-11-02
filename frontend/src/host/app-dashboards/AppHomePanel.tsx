import type { CSSProperties, ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { appsApi } from '../../api/apps';
import {
  dashboardsApi,
  isHomeComponentWidget,
  type AppHome,
  type AppHomeResponse,
  type HomeWidget,
} from '../../api/dashboards';
import { useScope } from '../../context/ScopeContext';
import { requestOpenCompanionChat } from '../../features/ai-chat/chatHandoff';
import { Button, Skeleton } from '../../components/ui';
import { Surface, Text } from '../../ui';
import { DashboardWidgetRenderer } from '../../features/app-dashboards/DashboardWidgetRegistry';
import { NativeHome } from '../../features/app-dashboards/NativeHome';
import type { HomeElement } from '../../features/app-dashboards/nativeHomeTypes';
import '../../features/app-dashboards/dashboardWidgets.css';
import './appHomeLayout.css';
import { NativeHomeWithCardActions } from './NativeHomeWithCardActions';
import {
  buildNativeCardChatActions,
  nativeCardActionLabelsForPackage,
} from './nativeHomeCardActions';

/** Legacy grid widgets (if present) map to the same home actions. */
const RECRUITMENT_IN_CARD_BY_WIDGET: Record<string, string> = {
  openings_to_share: 'Post a job opening',
  pipeline_candidates: 'Create a candidate',
};

type HomeAction = NonNullable<AppHome['actions']>[number];

function widgetActionVisible(
  action: HomeAction,
  widget: HomeWidget,
): boolean {
  if (!action.when || action.when.widget !== widget.id) return false;
  if (isHomeComponentWidget(widget)) return false;
  const data = widget.data;
  if (!data || data.error || typeof data.total_matched !== 'number') return false;
  return action.when.state === 'empty'
    ? data.total_matched === 0
    : data.total_matched > 0;
}

function HomeWidgetCard({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!footer) return <>{children}</>;
  return (
    <div className="dashboard-widget flex h-full min-h-[88px] flex-col overflow-hidden">
      <div className="min-h-0 flex-1 [&_.dashboard-widget]:h-full [&_.dashboard-widget]:rounded-none [&_.dashboard-widget]:border-0 [&_.dashboard-widget]:shadow-none">
        {children}
      </div>
      <div className="shrink-0 border-t border-[var(--panel-border)] bg-[var(--panel)] px-4 py-3">
        {footer}
      </div>
    </div>
  );
}

function ChatActionButton({
  label,
  draft,
  disabled,
}: {
  label: string;
  draft: string;
  disabled?: boolean;
}) {
  return (
    <Button
      type="button"
      variant="secondary"
      className="w-full sm:w-auto"
      disabled={disabled}
      onClick={() => requestOpenCompanionChat({ draftText: draft })}
    >
      {label}
    </Button>
  );
}

function resolveInCardFooter(
  widget: HomeWidget,
  opts: {
    isRecruitment: boolean;
    actionsByLabel: Map<string, HomeAction>;
    visibleActions: HomeAction[];
    preview: boolean;
  },
): ReactNode | null {
  const { isRecruitment, actionsByLabel, visibleActions, preview } = opts;
  const recruitmentLabel = isRecruitment
    ? RECRUITMENT_IN_CARD_BY_WIDGET[widget.id]
    : undefined;
  const pinned = recruitmentLabel
    ? actionsByLabel.get(recruitmentLabel)
    : undefined;
  const conditional = visibleActions.filter(action =>
    widgetActionVisible(action, widget),
  );

  const buttons: HomeAction[] = [];
  if (pinned) buttons.push(pinned);
  for (const action of conditional) {
    if (pinned && action.label === pinned.label) continue;
    buttons.push(action);
  }
  if (!buttons.length) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {buttons.map(action => (
        <ChatActionButton
          key={`${widget.id}-${action.label}`}
          label={action.label}
          draft={action.draft}
          disabled={preview}
        />
      ))}
    </div>
  );
}

export function AppHomeContent({
  appId,
  workspaceId,
  result,
  onRefresh,
  refreshVersion = 0,
  packageSlug,
}: {
  appId: string;
  workspaceId: string;
  result: AppHomeResponse;
  onRefresh: () => void;
  refreshVersion?: number;
  packageSlug?: string;
}) {
  const home = result.home;
  const preview = Boolean(result.preview);
  if (!home) {
    return <Text as="p" tone="muted">No home is available for this App.</Text>;
  }

  const slug = (packageSlug || '').trim().toLowerCase();
  const isRecruitment =
    slug === 'recruitment' || home.title === 'Recruitment home';
  const nativeCardLabelMap = nativeCardActionLabelsForPackage(slug);
  const inCardActionLabels = new Set([
    ...(isRecruitment ? Object.values(RECRUITMENT_IN_CARD_BY_WIDGET) : []),
    ...(nativeCardLabelMap ? Object.values(nativeCardLabelMap) : []),
  ]);

  const homeActions = home.actions ?? [];
  const nativeCardChatActions = nativeCardLabelMap
    ? buildNativeCardChatActions(homeActions, nativeCardLabelMap)
    : undefined;
  const useNativeCardChatActions = Boolean(nativeCardLabelMap);
  const homeWidgets = home.widgets ?? [];

  const actionsByLabel = new Map(
    homeActions.map(action => [action.label, action] as const),
  );

  const visibleActions = homeActions.filter(action => {
    if (!action.when) return true;
    const widget = homeWidgets.find(w => w.id === action.when?.widget);
    const data =
      widget && !isHomeComponentWidget(widget) ? widget.data : undefined;
    if (!data || data.error || typeof data.total_matched !== 'number') {
      return false;
    }
    return action.when.state === 'empty'
      ? data.total_matched === 0
      : data.total_matched > 0;
  });

  const globalActions = visibleActions.filter(
    action => !inCardActionLabels.has(action.label),
  );

  const renderNativeHome = (node: HomeElement) =>
    useNativeCardChatActions ? (
      <NativeHomeWithCardActions
        appId={appId}
        workspaceId={workspaceId}
        node={node}
        preview={preview}
        liveData={!preview || Boolean(result.live_data)}
        refreshVersion={refreshVersion}
        onChanged={onRefresh}
        cardChatActions={nativeCardChatActions}
      />
    ) : (
      <NativeHome
        appId={appId}
        workspaceId={workspaceId}
        node={node}
        preview={preview}
        liveData={!preview || Boolean(result.live_data)}
        refreshVersion={refreshVersion}
        onChanged={onRefresh}
      />
    );

  const renderWidget = (widget: HomeWidget) => {
    const inCardFooter = resolveInCardFooter(widget, {
      isRecruitment,
      actionsByLabel,
      visibleActions,
      preview,
    });

    return (
      <div
        key={widget.id}
        className="commercial-app-home-widget min-w-0 h-full"
        style={
          {
            '--home-widget-column': `${widget.grid.x + 1} / span ${widget.grid.w}`,
            '--home-widget-row': `${widget.grid.y + 1} / span ${widget.grid.h}`,
          } as CSSProperties
        }
      >
        {isHomeComponentWidget(widget) ? (
          renderNativeHome(widget.content)
        ) : preview && !result.live_data ? (
          <Surface tone="panel" className="space-y-2 p-4">
            <Text as="h3" variant="heading-sm">
              {widget.title}
            </Text>
            <Text as="p" tone="muted" variant="body-sm">
              Enable live data to preview this widget.
            </Text>
          </Surface>
        ) : widget.data?.error ? (
          <Surface tone="panel" className="space-y-3 p-4">
            <Text as="h3" variant="heading-sm">
              {widget.title}
            </Text>
            <Text as="p" tone="muted" variant="body-sm">
              We couldn’t read this information right now.
            </Text>
            <Button variant="secondary" onClick={onRefresh}>
              Try again
            </Button>
          </Surface>
        ) : (
          <HomeWidgetCard footer={inCardFooter ?? undefined}>
            <DashboardWidgetRenderer
              type={widget.type}
              title={widget.title || ''}
              data={widget.data}
              config={widget.config}
            />
          </HomeWidgetCard>
        )}
      </div>
    );
  };

  return (
    <section
      aria-label={home.title}
      className="commercial-app-home-section app-home-section space-y-5"
    >
      <div className="space-y-2">
        <Text as="h2" variant="heading-md">
          {home.title}
        </Text>
        {home.description ? (
          <Text as="p" tone="muted" variant="body-sm">
            {home.description}
          </Text>
        ) : null}
      </div>
      {globalActions.length ? (
        <div className="flex flex-wrap gap-2">
          {globalActions.map(action => (
            <ChatActionButton
              key={action.label}
              label={action.label}
              draft={action.draft}
              disabled={preview}
            />
          ))}
        </div>
      ) : null}
      {home.renderer === 'native' && home.content ? (
        renderNativeHome(home.content)
      ) : (
        <div className="commercial-app-home-grid">
          {homeWidgets.map(renderWidget)}
        </div>
      )}
    </section>
  );
}

/** Business host overlay: correct home grid + widget-scoped chat actions. */
export function AppHomePanel({
  appId,
  workspaceId,
}: {
  appId: string;
  workspaceId: string;
}) {
  const { scope } = useScope();
  const appQuery = useQuery({
    queryKey: ['app', appId],
    queryFn: () => appsApi.get(appId),
    enabled: Boolean(appId),
  });
  const query = useQuery({
    queryKey: ['app-home-data', appId, scope?.workspaceId],
    queryFn: () => dashboardsApi.home(appId),
    enabled: Boolean(workspaceId && workspaceId === scope?.workspaceId),
    refetchInterval: 15000,
  });

  if (query.isError && !query.data) {
    return (
      <div className="space-y-3">
        <Text as="p" tone="muted">
          This home could not be loaded.
        </Text>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!query.data) return <Skeleton className="h-48" />;

  const packageSlug =
    appQuery.data?.source_operational_model_slug ||
    appQuery.data?.installed_package_slug ||
    '';

  return (
    <>
      <div role="status" aria-live="polite" className="sr-only">
        {query.isFetching
          ? 'Refreshing App home.'
          : query.isError
            ? 'App home could not refresh. Showing the last loaded information.'
            : ''}
      </div>
      <AppHomeContent
        appId={appId}
        workspaceId={workspaceId}
        result={query.data}
        refreshVersion={query.dataUpdatedAt}
        onRefresh={() => void query.refetch()}
        packageSlug={packageSlug}
      />
    </>
  );
}
