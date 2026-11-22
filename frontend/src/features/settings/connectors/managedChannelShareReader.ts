import apiClient, { getActiveScopeHeader } from '../../../api/client';
import { getAccessToken, getSessionGeneration } from '../../../api/session';
import type { ManagedShareOwner } from './managedChannelShareProof';

export type ManagedChannelShareReader = (owner: ManagedShareOwner, signal: AbortSignal) => Promise<unknown>;

/**
 * Detached adapter for a reviewed host's explicitly enabled Configure read.
 * Reuse the authenticated client, but never put a share result in a query
 * cache or allow a response from an obsolete credential or workspace.
 */
export const readManagedChannelShare: ManagedChannelShareReader = async (owner, signal) => {
  const unavailable = () => new Error('Full-page sharing could not be verified');
  const scope = `ws:${owner.workspaceId}`;
  const generation = getSessionGeneration();
  // Keep credentials only inside the existing session/client boundary. Compare
  // the captured token locally to reject even cross-tab credential changes.
  const token = getAccessToken();
  if (signal.aborted || !token || !owner.principalId || !owner.sessionFence ||
      !owner.workspaceId || !owner.provisionId || !owner.routeEpoch ||
      !owner.publication?.profileKey || !Number.isSafeInteger(owner.publication.publishedVersion) ||
      owner.publication.publishedVersion < 1 ||
      !/^[a-z][a-z0-9_]{0,63}$/.test(owner.slug) || getActiveScopeHeader() !== scope) throw unavailable();
  const settings = {
    params: {
      workspace_id: owner.workspaceId,
      provision_id: owner.provisionId,
      route_epoch: owner.routeEpoch,
      profile_key: owner.publication.profileKey,
      published_version: owner.publication.publishedVersion,
    },
    headers: {
      'X-Integral-Scope': scope,
      'Cache-Control': 'no-cache, no-store',
      Accept: 'application/json',
    },
    signal,
    timeout: 5000,
    __suppressSystemNotify: true,
  };
  try {
    const response = await apiClient.get<unknown>(`/agentive/managed-channels/${owner.slug}/share`, settings);
    const contentType = response.headers['content-type'];
    if (signal.aborted || getActiveScopeHeader() !== scope ||
        getSessionGeneration() !== generation || getAccessToken() !== token ||
        typeof contentType !== 'string' || contentType.split(';')[0].trim().toLowerCase() !== 'application/json' ||
        !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(String(response.headers['cache-control'] ?? ''))) {
      throw unavailable();
    }
    return response.data;
  } catch {
    throw unavailable();
  }
};
