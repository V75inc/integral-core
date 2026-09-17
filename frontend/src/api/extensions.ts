import { apiClient } from './client';

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
};
