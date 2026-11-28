import apiClient from './client';

export type ConnectorBindingScope = "workspace" | "personal";

export interface ConnectorPackageField {
  key: string;
  label: string;
  type: 'string' | 'text' | 'url' | 'secret' | 'number' | 'boolean' | 'select';
  required: boolean;
  scope?: ConnectorBindingScope;
  help?: string;
  options?: { value: string; label: string }[];
  multiple?: boolean;
  options_from?: {
    action: string;
    items: string;
    value: string;
    label: string;
  };
}

export interface ConnectorPackageSummary {
  id: string;
  name: string;
  description: string;
  version: string;
  fingerprint?: string;
  capabilities: string[];
  host_managed?: boolean;
  binding_scopes?: ConnectorBindingScope[];
  auth_type?: 'oauth2' | 'mcp_oauth';
  channel_mode?: 'webhook' | 'managed-text-v1';
  configuration: ConnectorPackageField[];
  grant?: {
    edge: string;
    present: boolean;
    id?: string;
    enabled?: boolean;
  };
}

export interface ConnectorPackageInstallation {
  id: string;
  package_id: string;
  name: string;
  connection_mode?: "shared" | "per_user";
  version: string;
  fingerprint?: string;
  admitted_fingerprint?: string;
  revision: number;
  enabled: boolean;
  health_status: string;
  config: Record<string, unknown>;
  pending_review: boolean;
  package_available?: boolean;
  upgrade_available?: boolean;
  rollback_available?: boolean;
}

export interface ConnectorPackageCatalogue {
  packages: ConnectorPackageSummary[];
  rejected: { id: string; reason: string }[];
  installations: ConnectorPackageInstallation[];
}

export const connectorPackagesApi = {
  async list(workspaceId: string): Promise<ConnectorPackageCatalogue> {
    const response = await apiClient.get<ConnectorPackageCatalogue>('/agentive/connector-packages', {
      params: { workspace_id: workspaceId },
    });
    return response.data;
  },
  async rescan(workspaceId: string): Promise<ConnectorPackageCatalogue> {
    const response = await apiClient.post<ConnectorPackageCatalogue>(
      '/agentive/connector-packages/rescan',
      {},
      { params: { workspace_id: workspaceId } },
    );
    return response.data;
  },
  async install(workspaceId: string, packageId: string, values: Record<string, unknown>, bindingScope?: ConnectorBindingScope) {
    const response = await apiClient.post(
      `/agentive/connector-packages/${packageId}/install`,
      { workspace_id: workspaceId, values, binding_scope: bindingScope },
      { headers: { 'X-Integral-Scope': `ws:${workspaceId}` } },
    );
    return response.data as ConnectorPackageInstallation;
  },
  async startOAuth(workspaceId: string, packageId: string, installationId: string) {
    const response = await apiClient.post<{ consent_url: string }>(
      `/agentive/connector-packages/${packageId}/oauth/start`,
      { connector_id: installationId },
      { headers: { 'X-Integral-Scope': `ws:${workspaceId}` } },
    );
    return response.data;
  },
  async configure(workspaceId: string, installationId: string, values: Record<string, unknown>) {
    const response = await apiClient.put(
      `/agentive/connector-packages/installations/${installationId}`,
      { workspace_id: workspaceId, values },
    );
    return response.data as ConnectorPackageInstallation;
  },
  async setEnabled(workspaceId: string, installationId: string, enabled: boolean) {
    const response = await apiClient.put(
      `/agentive/connector-packages/installations/${installationId}/enabled`,
      { enabled },
      { params: { workspace_id: workspaceId } },
    );
    return response.data as ConnectorPackageInstallation;
  },
  async upgrade(workspaceId: string, installationId: string) {
    const response = await apiClient.post(
      `/agentive/connector-packages/installations/${installationId}/upgrade`,
      {},
      { params: { workspace_id: workspaceId } },
    );
    return response.data as ConnectorPackageInstallation;
  },
  async rollback(workspaceId: string, installationId: string) {
    const response = await apiClient.post(
      `/agentive/connector-packages/installations/${installationId}/rollback`,
      {},
      { params: { workspace_id: workspaceId } },
    );
    return response.data as ConnectorPackageInstallation;
  },
  async remove(workspaceId: string, installationId: string) {
    await apiClient.delete(`/agentive/connector-packages/installations/${installationId}`, {
      params: { workspace_id: workspaceId },
    });
  },
};
