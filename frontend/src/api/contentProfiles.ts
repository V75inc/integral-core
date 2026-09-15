import apiClient from './client';
import { unwrapResource } from './helpers';
import type { ContentProfileNode } from '../types';

export interface PackagePreviewItem {
  package_name: string;
  package_description?: string | null;
  entry_type_count: number;
  view_count: number;
  tag_count: number;
  validation_errors: string[];
}

export interface ImportPreviewResponse {
  packages: PackagePreviewItem[];
  archive_type: 'single' | 'archive';
}

export const contentProfilesApi = {
  list: async (): Promise<ContentProfileNode[]> => {
    const { data } = await apiClient.get('/content-profiles');
    const list = (data as { content_profiles?: ContentProfileNode[] })?.content_profiles;
    return Array.isArray(list) ? list : [];
  },
  get: (id: string) =>
    apiClient
      .get(`/content-profiles/${id}`)
      .then(r => unwrapResource<ContentProfileNode>(r.data, 'content_profile')),

  getAttachedForTrack: (trackId: string) =>
    apiClient
      .get(`/tracks/${trackId}/content-profile`)
      .then(r => unwrapResource<ContentProfileNode>(r.data, 'content_profile')),

  getAttachedForApp: (appId: string) =>
    apiClient
      .get(`/apps/${appId}/content-profile`)
      .then(r => unwrapResource<ContentProfileNode>(r.data, 'content_profile')),

  patchAttachedForTrack: (
    trackId: string,
    body: {
      entry_types?: Array<{ name: string; icon?: string; form_schema?: Record<string, unknown> }>;
      tags?: Array<{ name: string; color?: string }>;
      views?: Array<{ name: string; type?: string; config?: Record<string, unknown>; is_default?: boolean }>;
    }
  ) =>
    apiClient
      .patch(`/tracks/${trackId}/content-profile`, body)
      .then(r => unwrapResource<ContentProfileNode>(r.data, 'content_profile')),

  patchAttachedForApp: (
    appId: string,
    body: Record<string, unknown>
  ) =>
    apiClient
      .patch(`/apps/${appId}/content-profile`, body)
      .then(r => unwrapResource<ContentProfileNode>(r.data, 'content_profile')),

  mergeLibraryIntoTrack: (trackId: string, libraryContentProfileId: string) =>
    apiClient
      .post(`/tracks/${trackId}/content-profile/merge-library`, {
        library_content_profile_id: libraryContentProfileId
      })
      .then(r => r.data),

  mergeLibraryIntoApp: (appId: string, libraryContentProfileId: string) =>
    apiClient
      .post(`/apps/${appId}/content-profile/merge-library`, {
        library_content_profile_id: libraryContentProfileId
      })
      .then(r => r.data),

  // In-place customization: entry types
  addEntryTypeToTrackProfile: (trackId: string, params: {
    name: string;
    icon?: string;
    form_schema?: Record<string, unknown>;
  }) =>
    apiClient
      .post(`/tracks/${trackId}/content-profile/entry-types`, params)
      .then(r => r.data),

  removeEntryTypeFromTrackProfile: (trackId: string, entryTypeId: string) =>
    apiClient
      .delete(`/tracks/${trackId}/content-profile/entry-types/${entryTypeId}`)
      .then(r => r.data),

  // In-place customization: views
  addViewToTrackProfile: (trackId: string, params: {
    name: string;
    view_type?: string;
    type?: string;
    config?: Record<string, unknown>;
    is_default?: boolean;
  }) =>
    apiClient
      .post(`/tracks/${trackId}/content-profile/views`, params)
      .then(r => r.data),

  removeViewFromTrackProfile: (trackId: string, viewId: string) =>
    apiClient
      .delete(`/tracks/${trackId}/content-profile/views/${viewId}`)
      .then(r => r.data),

  // Detach and revert
  detachLibraryFromTrackProfile: (trackId: string) =>
    apiClient
      .post(`/tracks/${trackId}/content-profile/detach-library`)
      .then(r => r.data),

  revertTrackProfileCustomizations: (trackId: string) =>
    apiClient
      .post(`/tracks/${trackId}/content-profile/revert-customizations`)
      .then(r => r.data),

  // Derive library profile
  deriveFromTrack: (trackId: string, params?: {
    name?: string;
    description?: string;
    version?: string;
    workspace_id?: string;
  }) =>
    apiClient
      .post(`/content-profiles/from-track/${trackId}`, params || {})
      .then(r => r.data),

  deriveFromApp: (appId: string, params?: {
    name?: string;
    description?: string;
    version?: string;
    workspace_id?: string;
  }) =>
    apiClient
      .post(`/content-profiles/from-app/${appId}`, params || {})
      .then(r => r.data),

  importPreview: (file: File, workspaceId: string): Promise<ImportPreviewResponse> => {
    const form = new FormData();
    form.append('file', file);
    return apiClient
      .post(`/content-profiles/import?preview=true&workspace_id=${encodeURIComponent(workspaceId)}`, form, {
        headers: { 'X-Integral-Scope': `ws:${workspaceId}` }
      })
      .then(r => r.data as ImportPreviewResponse);
  },

  importPublish: (file: File, workspaceId: string): Promise<{ published?: number; content_profile?: ContentProfileNode }> => {
    const form = new FormData();
    form.append('file', file);
    return apiClient
      .post(`/content-profiles/import?workspace_id=${encodeURIComponent(workspaceId)}`, form, {
        headers: { 'X-Integral-Scope': `ws:${workspaceId}` }
      })
      .then(r => r.data as { published?: number; content_profile?: ContentProfileNode });
  },

  delete: (id: string): Promise<void> =>
    apiClient.delete(`/content-profiles/${id}`).then(() => undefined)
};
