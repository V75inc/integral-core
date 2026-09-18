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
  receipt?: Record<string, unknown> | null;
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
    const headers: Record<string, string> = {
      'X-Integral-Run-Origin': 'view',
    };
    if (idempotencyKey) {
      headers['Idempotency-Key'] = idempotencyKey;
    }
    const { data } = await apiClient.post<AppOperationInvokeResponse>(
      `/extensions/${appId}/operations/${encodeURIComponent(operationKey)}`,
      { input: payload },
      { headers },
    );
    return data;
  },
};
