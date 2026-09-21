import apiClient from './client';
import { unwrapResource } from './helpers';
import type { OperationalModelNode, App, Track } from '../types';

export interface BatchInstallEntry {
  library_cp_id: string;
  app_id?: string | null;
  name?: string | null;
  status?: string | null;
  reason?: string | null;
  error?: string | null;
  error_code?: string | null;
  install_token?: string | null;
  settings_schema?: Record<string, unknown> | null;
}

export interface BatchInstallResponse {
  installed: BatchInstallEntry[];
  skipped: BatchInstallEntry[];
  failed: BatchInstallEntry[];
  order?: string[];
}

export interface UninstallResponse {
  status: 'uninstalled' | 'force_uninstalled';
  app_id: string;
}

export interface FinalizeInstallResponse {
  status: string;
  app_id: string;
  installed_at?: string;
}

export interface AppSettingsResponse {
  app_id: string;
  settings: Record<string, unknown>;
  settings_schema: Record<string, unknown>;
  lifecycle_state?: string;
}

export const appsApi = {
  list: async (): Promise<App[]> => {
    const { data } = await apiClient.get('/apps');
    const apps = (data as { apps?: App[] })?.apps;
    return Array.isArray(apps) ? apps : [];
  },
  create: (body: {
    name: string;
    description?: string;
    workspace_id?: string;
    visibility?: string;
    library_operational_model_id?: string;
    accent_color?: string;
    include_seed_data?: boolean;
  }) =>
    apiClient
      .post('/apps', body)
      .then(r => unwrapResource<App>(r.data, 'app')),
  get: (id: string) =>
    apiClient
      .get(`/apps/${id}`)
      .then(r => unwrapResource<App>(r.data, 'app')),
  update: (
    id: string,
    body: {
      name?: string;
      description?: string;
      workspace_id?: string | null;
      visibility?: string;
      accent_color?: string;
    }
  ) =>
    apiClient
      .put(`/apps/${id}`, body)
      .then(r => unwrapResource<App>(r.data, 'app')),
  delete: (id: string) => apiClient.delete(`/apps/${id}`),
  listTracks: async (appId: string): Promise<Track[]> => {
    const { data } = await apiClient.get(`/apps/${appId}/tracks`);
    const tracks = (data as { tracks?: Track[] })?.tracks;
    return Array.isArray(tracks) ? tracks : [];
  },
  addTrack: (appId: string, track_id: string) =>
    apiClient.post(`/apps/${appId}/tracks`, { track_id }),
  removeTrack: (appId: string, trackId: string) =>
    apiClient.delete(`/apps/${appId}/tracks/${trackId}`),
  /**
   * Phase 34 — persist the display order of Tracks under an App.
   * Backend writes the new positions onto the App-CONTAINS-Track edges
   * so subsequent `listTracks` calls return them in the same order.
   */
  reorderTracks: (appId: string, trackIds: string[]) =>
    apiClient.patch(`/apps/${appId}/tracks/order`, { track_ids: trackIds }),
  listCollaborators: async (appId: string) => {
    const { data } = await apiClient.get(`/apps/${appId}/collaborators`);
    return (data as { collaborators?: unknown[] })?.collaborators || [];
  },
  addCollaborator: (
    appId: string,
    body: { collaborator_user_id: string; role?: string }
  ) =>
    apiClient.post(`/apps/${appId}/collaborators`, {
      collaborator_user_id: body.collaborator_user_id,
      role: body.role || 'editor',
    }),
  removeCollaborator: (appId: string, userId: string) =>
    apiClient.delete(`/apps/${appId}/collaborators/${userId}`),
  /** Update an existing direct collaborator's role on an App. Owner-only.
   *  Accepts ``admin | editor | commenter | viewer``. */
  updateCollaboratorRole: (
    appId: string,
    userId: string,
    role: 'admin' | 'editor' | 'commenter' | 'viewer'
  ) =>
    apiClient.patch(`/apps/${appId}/collaborators/${userId}`, { role }),

  transferOwnership: (appId: string, new_owner_user_id: string) =>
    apiClient
      .post(`/apps/${appId}/transfer-ownership`, {
        new_owner_user_id,
      })
      .then(r => unwrapResource<App>(r.data, 'app')),

  getOperationalModel: (appId: string) =>
    apiClient
      .get(`/apps/${appId}/operational-model`)
      .then(r => unwrapResource<OperationalModelNode>(r.data, 'operational_model')),

  patchOperationalModel: (
    appId: string,
    body: {
      name?: string;
      description?: string;
      version?: string;
      manifest?: Record<string, unknown>;
    }
  ) =>
    apiClient
      .patch(`/apps/${appId}/operational-model`, body)
      .then(r => unwrapResource<OperationalModelNode>(r.data, 'operational_model')),

  mergeLibraryIntoApp: (appId: string, library_operational_model_id: string) =>
    apiClient.post(`/apps/${appId}/operational-model/merge-library`, {
      library_operational_model_id,
    }),

  /**
   * Phase 32 — batch install N library bundles in topological dep order.
   * Each item's `name` + `description` default to the bundle manifest's
   * `package.name` + `package.description` when omitted.
   */
  batchInstall: async (
    items: Array<{
      library_cp_id: string;
      name?: string;
      description?: string;
      settings?: Record<string, unknown>;
      include_seed_data?: boolean;
    }>,
    options?: { include_seed_data?: boolean },
  ): Promise<BatchInstallResponse> => {
    const globalIncludeSeeds = options?.include_seed_data;
    const payload = {
      items: items.map(item => ({
        ...item,
        include_seed_data:
          item.include_seed_data ?? globalIncludeSeeds ?? true,
      })),
    };
    const { data } = await apiClient.post('/apps/batch-install', payload);
    return data as BatchInstallResponse;
  },

  listTrackTemplates: async (appId: string): Promise<OperationalModelNode[]> => {
    const { data } = await apiClient.get(
      `/apps/${appId}/operational-model/track-templates`
    );
    const list = (data as { track_templates?: OperationalModelNode[] })
      ?.track_templates;
    return Array.isArray(list) ? list : [];
  },

  createTrackTemplate: (
    appId: string,
    body: { name: string; description?: string }
  ) =>
    apiClient
      .post(`/apps/${appId}/operational-model/track-templates`, body)
      .then(r =>
        unwrapResource<OperationalModelNode>(r.data, 'track_template')
      ),

  deleteTrackTemplate: (appId: string, templateId: string) =>
    apiClient.delete(
      `/apps/${appId}/operational-model/track-templates/${templateId}`
    ),

  applyTrackTemplateToTrack: (
    appId: string,
    templateId: string,
    track_id: string
  ) =>
    apiClient.post(
      `/apps/${appId}/operational-model/track-templates/${templateId}/apply-to-track`,
      { track_id }
    ),

  uninstall: (appId: string, options?: { force?: boolean }) =>
    apiClient
      .post<UninstallResponse>(
        `/apps/${appId}/uninstall${options?.force ? '?force=true' : ''}`,
        // uninstall_app_endpoint (app/api/apps.py) has force/archive as
        // plain typed params, which jvspatial's @endpoint turns into an
        // auto-generated request-body model — a bodyless POST (axios'
        // default when no data arg is passed) fails validation with
        // "Field required" at ('body',) before the handler even runs.
        // Found via live testing: every app uninstall 500'd platform-wide.
        {},
      )
      .then(r => r.data),

  finalizeInstall: (
    appId: string,
    body: { install_token: string; settings: Record<string, unknown> },
  ) =>
    apiClient
      .post<FinalizeInstallResponse>(`/apps/${appId}/install/settings`, body)
      .then(r => r.data),

  getAppSettings: (appId: string) =>
    apiClient
      .get<AppSettingsResponse>(`/apps/${appId}/settings`)
      .then(r => r.data),
};
