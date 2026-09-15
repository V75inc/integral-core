import type { ComponentType, ReactNode } from 'react';
import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { DashboardWidgetTypeSpec } from '../../api/dashboards';
import { Text } from '../../ui';
import {
  DASHBOARD_CHART_AXIS,
  DASHBOARD_CHART_GRID,
  dashboardTooltipItemStyle,
  dashboardTooltipLabelStyle,
  dashboardTooltipStyle,
  getDashboardChartColors,
} from './dashboardChartTheme';
import { WidgetShell } from './WidgetShell';

function DashboardChartTooltip() {
  return (
    <Tooltip
      contentStyle={dashboardTooltipStyle()}
      labelStyle={dashboardTooltipLabelStyle()}
      itemStyle={dashboardTooltipItemStyle()}
    />
  );
}

export function MissingDashboardWidget({
  widgetType,
}: {
  widgetType: string;
}) {
  return (
    <div className="dashboard-widget flex h-full min-h-[88px] items-center justify-center border-dashed">
      <Text variant="meta" tone="muted" as="p" className="text-center px-4">
        Unknown widget: <code>{widgetType}</code>
      </Text>
    </div>
  );
}

function WidgetDataError({ title, message }: { title: string; message: string }) {
  return (
    <WidgetShell title={title}>
      <div className="flex flex-1 items-center justify-center py-4">
        <Text variant="meta" tone="muted" as="p" className="text-center px-4">
          Couldn&apos;t load: {message}
        </Text>
      </div>
    </WidgetShell>
  );
}

function widgetDataError(
  title: string,
  data?: Record<string, unknown>,
): ReactNode | null {
  const err = data?.error;
  if (err == null || err === '') return null;
  return (
    <WidgetDataError title={title} message={String(err)} />
  );
}

export function MetricCardWidget({
  title,
  data,
  config,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const value = data?.value ?? '—';
  const suffix = config?.suffix != null ? String(config.suffix) : '';
  return (
    <WidgetShell title={title}>
      <div className="flex flex-1 flex-col justify-center">
        <Text
          variant="display-sm"
          as="p"
          className="dashboard-metric-value text-3xl font-semibold"
        >
          {String(value)}
          {suffix ? (
            <Text variant="body" tone="muted" weight="medium" as="span" className="ml-1 text-xl">
              {suffix}
            </Text>
          ) : null}
        </Text>
      </div>
    </WidgetShell>
  );
}

export function MetricRowWidget({
  title,
  data,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const metrics = (data?.metrics as { label: string; value: number }[]) ?? [];
  return (
    <WidgetShell title={title}>
      <div className="grid flex-1 grid-cols-2 gap-4 sm:grid-cols-4">
        {metrics.map(m => (
          <div key={m.label || String(m.value)}>
            <Text variant="meta" tone="muted" as="p" className="mb-0.5">
              {m.label}
            </Text>
            <Text
              variant="body"
              as="p"
              className="dashboard-metric-value text-xl font-semibold"
            >
              {m.value}
            </Text>
          </div>
        ))}
      </div>
    </WidgetShell>
  );
}

function chartSeriesFromData(
  data?: Record<string, unknown>,
): { label: string; value: number }[] {
  if (!data) return [];
  const rawSeries = data.series;
  if (Array.isArray(rawSeries)) {
    return rawSeries
      .map(point => {
        if (!point || typeof point !== 'object') return null;
        const row = point as Record<string, unknown>;
        const label = String(row.label ?? row.key ?? row.name ?? '');
        const value = Number(row.value ?? row.count ?? 0);
        if (!label && !Number.isFinite(value)) return null;
        return { label, value: Number.isFinite(value) ? value : 0 };
      })
      .filter((row): row is { label: string; value: number } => row != null);
  }
  const groups = data.groups;
  if (Array.isArray(groups)) {
    return groups
      .map(group => {
        if (!group || typeof group !== 'object') return null;
        const row = group as Record<string, unknown>;
        return {
          label: String(row.label ?? row.key ?? ''),
          value: Number(row.value ?? row.count ?? 0),
        };
      })
      .filter((row): row is { label: string; value: number } => row != null);
  }
  return [];
}

function ChartSeries({
  title,
  data,
  config,
  kind,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
  kind: 'bar' | 'line' | 'pie';
}) {
  // Hoisted above the error return: a hook after a conditional return changes
  // the hook count between renders, so a widget that starts errored and later
  // resolves (or the reverse) threw "rendered fewer hooks than expected".
  const colors = useMemo(() => getDashboardChartColors(), []);

  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const series = chartSeriesFromData(data);
  const accent = colors[0];
  const horizontal = config?.orientation === 'horizontal';
  const showLegend =
    kind === 'pie'
      ? config?.show_legend !== false
      : Boolean(config?.show_legend);

  if (series.length === 0) {
    const matched = Number(data?.total_matched ?? 0);
    return (
      <WidgetShell title={title} variant="chart">
        <div className="flex flex-1 items-center justify-center py-6">
          <Text variant="meta" tone="muted" as="p">
            {matched > 0 ? 'No chart groups to display' : 'No data yet'}
          </Text>
        </div>
      </WidgetShell>
    );
  }

  return (
    <WidgetShell title={title} variant="chart">
      <div className="dashboard-chart-canvas min-h-[140px] w-full flex-1">
        <ResponsiveContainer width="100%" height={160}>
          {kind === 'bar' ? (
            <BarChart
              data={series}
              layout={horizontal ? 'vertical' : 'horizontal'}
              margin={{ top: 4, right: 8, left: horizontal ? 4 : -8, bottom: 0 }}
            >
              <CartesianGrid {...DASHBOARD_CHART_GRID} vertical={false} />
              {horizontal ? (
                <>
                  <XAxis type="number" {...DASHBOARD_CHART_AXIS} />
                  <YAxis
                    type="category"
                    dataKey="label"
                    {...DASHBOARD_CHART_AXIS}
                    width={72}
                  />
                </>
              ) : (
                <>
                  <XAxis dataKey="label" {...DASHBOARD_CHART_AXIS} />
                  <YAxis {...DASHBOARD_CHART_AXIS} width={36} />
                </>
              )}
              <DashboardChartTooltip />
              {showLegend ? <Legend /> : null}
              <Bar dataKey="value" fill={accent} radius={[4, 4, 0, 0]} />
            </BarChart>
          ) : kind === 'line' ? (
            <LineChart data={series} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
              <CartesianGrid {...DASHBOARD_CHART_GRID} vertical={false} />
              <XAxis dataKey="label" {...DASHBOARD_CHART_AXIS} />
              <YAxis {...DASHBOARD_CHART_AXIS} width={36} />
              <DashboardChartTooltip />
              {showLegend ? <Legend /> : null}
              <Line
                type="monotone"
                dataKey="value"
                stroke={accent}
                strokeWidth={2}
                dot={{ r: 3, fill: accent, strokeWidth: 0 }}
                activeDot={{ r: 4, fill: accent }}
              />
            </LineChart>
          ) : (
            <PieChart>
              <Pie
                data={series}
                dataKey="value"
                nameKey="label"
                cx="50%"
                cy="50%"
                innerRadius={36}
                outerRadius={60}
                paddingAngle={2}
              >
                {series.map((_, i) => (
                  <Cell key={i} fill={colors[i % colors.length]} stroke="transparent" />
                ))}
              </Pie>
              <DashboardChartTooltip />
              {showLegend ? <Legend /> : null}
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>
    </WidgetShell>
  );
}

export function ChartBarWidget(props: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  return <ChartSeries {...props} kind="bar" />;
}

export function ChartLineWidget(props: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  return <ChartSeries {...props} kind="line" />;
}

export function ChartPieWidget(props: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  return <ChartSeries {...props} kind="pie" />;
}

export function ActivityDigestWidget({
  title,
  data,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const tracks =
    (data?.tracks as {
      title?: string;
      track_id?: string;
      entry_count?: number;
      recent_count?: number;
    }[]) ?? [];
  return (
    <WidgetShell title={title}>
      <ul className="min-h-0 flex-1 overflow-y-auto text-sm">
        {tracks.length === 0 ? (
          <li>
            <Text variant="body-sm" tone="muted" as="span">
              No recent activity
            </Text>
          </li>
        ) : (
          tracks.map(t => (
            <li
              key={t.track_id}
              className="dashboard-list-row flex items-center justify-between gap-2 py-2.5"
            >
              <Text variant="body-sm" as="span" truncate className="min-w-0 flex-1">
                {t.title || t.track_id}
              </Text>
              <Text variant="body-sm" tone="muted" as="span" className="shrink-0 tabular-nums">
                {t.recent_count ?? 0} recent
              </Text>
            </li>
          ))
        )}
      </ul>
    </WidgetShell>
  );
}

export function RecentEntriesWidget({
  title,
  data,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const entries =
    (data?.entries as {
      id: string;
      title?: string;
      updated_at?: string;
    }[]) ?? [];
  return (
    <WidgetShell title={title}>
      <ul className="min-h-0 flex-1 space-y-0 overflow-y-auto text-sm">
        {entries.length === 0 ? (
          <li>
            <Text variant="body-sm" tone="muted" as="span">
              No entries
            </Text>
          </li>
        ) : (
          entries.map(e => (
            <li key={e.id} className="dashboard-list-row py-2.5">
              <Text variant="body-sm" as="span" truncate>
                {e.title || e.id}
              </Text>
            </li>
          ))
        )}
      </ul>
    </WidgetShell>
  );
}

export function TrackBreakdownWidget({
  title,
  data,
}: {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const errorUi = widgetDataError(title, data);
  if (errorUi) return errorUi;

  const tracks =
    (data?.tracks as {
      title?: string;
      track_id?: string;
      entry_count?: number;
    }[]) ?? [];
  return (
    <WidgetShell title={title}>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <Text variant="body-sm" tone="muted" as="th" className="pb-2 text-left font-normal">
                Track
              </Text>
              <Text variant="body-sm" tone="muted" as="th" className="pb-2 text-right font-normal">
                Entries
              </Text>
            </tr>
          </thead>
          <tbody>
            {tracks.map(t => (
              <tr key={t.track_id} className="dashboard-list-row">
                <Text variant="body-sm" as="td" truncate className="py-2.5">
                  {t.title || t.track_id}
                </Text>
                <Text variant="body-sm" as="td" className="py-2.5 text-right tabular-nums">
                  {t.entry_count ?? 0}
                </Text>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </WidgetShell>
  );
}

type WidgetRendererProps = {
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
};

const WIDGET_RENDERERS: Record<string, ComponentType<WidgetRendererProps>> = {
  metric_card: MetricCardWidget,
  metric_row: MetricRowWidget,
  chart_bar: ChartBarWidget,
  chart_line: ChartLineWidget,
  chart_pie: ChartPieWidget,
  activity_digest: ActivityDigestWidget,
  recent_entries: RecentEntriesWidget,
  track_breakdown: TrackBreakdownWidget,
};

export function DashboardWidgetRenderer({
  type,
  title,
  data,
  config,
}: {
  type: string;
  title: string;
  data?: Record<string, unknown>;
  config?: Record<string, unknown>;
}) {
  const Component = WIDGET_RENDERERS[type];
  if (!Component) {
    return <MissingDashboardWidget widgetType={type} />;
  }
  return <Component title={title} data={data} config={config} />;
}

export function groupWidgetTypes(
  specs: DashboardWidgetTypeSpec[],
): Record<string, DashboardWidgetTypeSpec[]> {
  return specs.reduce<Record<string, DashboardWidgetTypeSpec[]>>((acc, s) => {
    const g = s.palette_group || 'general';
    acc[g] = acc[g] ?? [];
    acc[g].push(s);
    return acc;
  }, {});
}
