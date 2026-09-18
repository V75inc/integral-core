import apiClient from './client';

export interface ExtensionViewDescriptor {
  key: string;
  name: string;
  description?: string;
  entry: string;
  scope?: string;
}

export interface ExtensionViewsListResponse {
  app_id: string;
  package_version?: string;
  views: ExtensionViewDescriptor[];
  handshake_token: string;
  protocol: string;
}

export interface ExtensionViewHandshakeResponse {
  app_id: string;
  view_key: string;
  package_version?: string;
  handshake_token: string;
  protocol: string;
  theme?: Record<string, unknown>;
}

export interface AppOperationInvokeResponse {
  app_id: string;
  operation_key: string;
  output: Record<string, unknown>;
  object_refs?: Array<Record<string, unknown>>;
  evidence?: Record<string, unknown>;
}

export const extensionsApi = {
  async listViews(appId: string, viewKey?: string): Promise<ExtensionViewsListResponse> {
    const params = viewKey ? { view_key: viewKey } : undefined;
    const { data } = await apiClient.get<ExtensionViewsListResponse>(
      `/extensions/${appId}/views`,
      { params },
    );
    return data;
  },

  async handshake(
    appId: string,
    viewKey: string,
  ): Promise<ExtensionViewHandshakeResponse> {
    const { data } = await apiClient.get<ExtensionViewHandshakeResponse>(
      `/extensions/${appId}/views/${encodeURIComponent(viewKey)}/handshake`,
    );
    return data;
  },

  async invokeOperation(
    appId: string,
    operationKey: string,
    payload: Record<string, unknown> = {},
    idempotencyKey?: string,
  ): Promise<AppOperationInvokeResponse> {
    const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined;
    const { data } = await apiClient.post<AppOperationInvokeResponse>(
      `/extensions/${appId}/operations/${encodeURIComponent(operationKey)}`,
      { input: payload },
      { headers },
    );
    return data;
  },

  async listCapabilities(includePaused = false) {
    const { data } = await apiClient.get<{
      workspace_id: string;
      generation_id: string;
      capabilities: Array<Record<string, unknown>>;
    }>('/capabilities', {
      params: includePaused ? { include_paused: true } : undefined,
    });
    return data;
  },

  async invokeQuery(
    appId: string,
    queryKey: string,
    params: Record<string, unknown> = {},
  ) {
    const { data } = await apiClient.post<Record<string, unknown>>(
      `/extensions/${appId}/queries/${encodeURIComponent(queryKey)}`,
      { params },
    );
    return data;
  },

  async governedQuery(query: Record<string, unknown>) {
    const { data } = await apiClient.post<Record<string, unknown>>('/query', {
      query,
    });
    return data;
  },
};
