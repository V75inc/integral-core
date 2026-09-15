import apiClient from './client';
import { unwrapResource } from './helpers';

export interface DashboardGridPlacement {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardWidget {
  id: string;
  type: string;
  title: string;
  grid: DashboardGridPlacement;
  config: Record<string, unknown>;
  data_source: Record<string, unknown>;
}

export interface Dashboard {
  id: string;
  name: string;
  app_id: string;
  workspace_id?: string;
  layout: { columns: number; row_height: number };
  widgets: DashboardWidget[];
  is_default: boolean;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface DashboardWidgetTypeSpec {
  type: string;
  label: string;
  description: string;
  palette_group: string;
  default_size: { w: number; h: number };
  config_schema?: Record<string, unknown>;
  data_source_schema?: Record<string, unknown>;
}

export interface DashboardSuggestResult {
  name: string;
  layout: { columns: number; row_height: number };
  widgets: DashboardWidget[];
  rationale: string;
}

export const dashboardsApi = {
  async list(appId: string): Promise<Dashboard[]> {
    const { data } = await apiClient.get(`/apps/${appId}/dashboards`);
    return unwrapResource<Dashboard[]>(data, 'dashboards');
  },

  async get(appId: string, dashboardId: string): Promise<Dashboard> {
    const { data } = await apiClient.get(
      `/apps/${appId}/dashboards/${dashboardId}`,
    );
    return data as Dashboard;
  },

  async create(
    appId: string,
    body: {
      name: string;
      layout?: Dashboard['layout'];
      widgets?: DashboardWidget[];
      is_default?: boolean;
    },
  ): Promise<Dashboard> {
    const { data } = await apiClient.post(`/apps/${appId}/dashboards`, body);
    return data as Dashboard;
  },

  async update(
    appId: string,
    dashboardId: string,
    body: Partial<{
      name: string;
      layout: Dashboard['layout'];
      widgets: DashboardWidget[];
      is_default: boolean;
    }>,
  ): Promise<Dashboard> {
    const { data } = await apiClient.patch(
      `/apps/${appId}/dashboards/${dashboardId}`,
      body,
    );
    return data as Dashboard;
  },

  async remove(appId: string, dashboardId: string): Promise<void> {
    await apiClient.delete(`/apps/${appId}/dashboards/${dashboardId}`);
  },

  async getData(
    appId: string,
    dashboardId: string,
  ): Promise<Record<string, unknown>> {
    const { data } = await apiClient.get(
      `/apps/${appId}/dashboards/${dashboardId}/data`,
    );
    return (data?.widget_data ?? {}) as Record<string, unknown>;
  },

  async suggest(appId: string): Promise<DashboardSuggestResult> {
    const { data } = await apiClient.get(`/apps/${appId}/dashboards/suggest`);
    return data as DashboardSuggestResult;
  },

  async getSubstrate(): Promise<{ widget_types: DashboardWidgetTypeSpec[] }> {
    const { data } = await apiClient.get('/dashboard-widget-substrate');
    return data as { widget_types: DashboardWidgetTypeSpec[] };
  },
};
