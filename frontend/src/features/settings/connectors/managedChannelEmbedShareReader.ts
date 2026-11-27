import apiClient, { getActiveScopeHeader } from '../../../api/client';
import { getAccessToken, getSessionGeneration } from '../../../api/session';
import {
  decodeApprovedManagedEmbed,
  isCanonicalManagedEmbedOrigin,
  type ManagedChannelEmbedRead,
  type ManagedEmbedOwner,
} from './managedChannelEmbedProof';

const UNAVAILABLE = Object.freeze({ state: 'unavailable' } as const);

export type ManagedChannelEmbedShareReader = (
  owner: ManagedEmbedOwner,
  signal: AbortSignal,
) => Promise<ManagedChannelEmbedRead>;

function identity(value: unknown, limit = 256): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= limit &&
    value === value.trim() && !/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
}

function version(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0;
}

function snapshot(owner: ManagedEmbedOwner): ManagedEmbedOwner | null {
  if (!owner?.publication || !owner.policy || !identity(owner.slug, 64) ||
      !/^[a-z][a-z0-9_]{0,63}$/.test(owner.slug) ||
      !identity(owner.principalId) || !identity(owner.sessionFence) ||
      !identity(owner.workspaceId) || !identity(owner.provisionId) ||
      typeof owner.routeEpoch !== 'string' || !/^(?:0|[1-9][0-9]{0,19})$/.test(owner.routeEpoch) ||
      !identity(owner.publication.profileKey, 128) || !version(owner.publication.publishedVersion) ||
      !version(owner.policy.policyRevision) || !isCanonicalManagedEmbedOrigin(owner.policy.parentOrigin)) return null;

  return {
    principalId: owner.principalId,
    workspaceId: owner.workspaceId,
    sessionFence: owner.sessionFence,
    slug: owner.slug,
    provisionId: owner.provisionId,
    routeEpoch: owner.routeEpoch,
    publication: { profileKey: owner.publication.profileKey, publishedVersion: owner.publication.publishedVersion },
    policy: { parentOrigin: owner.policy.parentOrigin, policyRevision: owner.policy.policyRevision },
  };
}

function unchanged(owner: ManagedEmbedOwner, requested: ManagedEmbedOwner): boolean {
  return owner.principalId === requested.principalId && owner.sessionFence === requested.sessionFence &&
    owner.workspaceId === requested.workspaceId && owner.slug === requested.slug &&
    owner.provisionId === requested.provisionId && owner.routeEpoch === requested.routeEpoch &&
    owner.publication?.profileKey === requested.publication.profileKey &&
    owner.publication?.publishedVersion === requested.publication.publishedVersion &&
    owner.policy?.parentOrigin === requested.policy.parentOrigin &&
    owner.policy?.policyRevision === requested.policy.policyRevision;
}

/**
 * Detached manager-only read of an already issued embed share. A future caller
 * supplies its current server-authorized publication and origin policy, aborts
 * on scope changes, and compares the returned proof against the current UI owner
 * before display. This never discovers origins, issues links, or stores results.
 */
export const readManagedChannelEmbedShare: ManagedChannelEmbedShareReader = async (owner, signal) => {
  try {
    const requested = snapshot(owner);
    const generation = getSessionGeneration();
    const token = getAccessToken();
    if (!requested || signal.aborted || !token || requested.sessionFence !== String(generation)) return UNAVAILABLE;
    const scope = `ws:${requested.workspaceId}`;
    if (getActiveScopeHeader() !== scope) return UNAVAILABLE;

    const settings = {
      params: {
        workspace_id: requested.workspaceId,
        provision_id: requested.provisionId,
        route_epoch: requested.routeEpoch,
        profile_key: requested.publication.profileKey,
        published_version: requested.publication.publishedVersion,
        embed_policy_revision: requested.policy.policyRevision,
        parent_origin: requested.policy.parentOrigin,
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
    const response = await apiClient.get<unknown>(`/agentive/managed-channels/${requested.slug}/embed-share`, settings);

    const contentType = response.headers['content-type'];
    const cacheControl = response.headers['cache-control'];
    if (signal.aborted || !unchanged(owner, requested) || getActiveScopeHeader() !== scope ||
        getSessionGeneration() !== generation || getAccessToken() !== token || response.status !== 200 ||
        typeof contentType !== 'string' || contentType.split(';')[0].trim().toLowerCase() !== 'application/json' ||
        typeof cacheControl !== 'string' || !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(cacheControl)) return UNAVAILABLE;

    return decodeApprovedManagedEmbed(response.data, requested);
  } catch {
    // Errors, denial and malformed/stale projections are all unavailable. Do
    // not surface transport errors which can contain private response data.
    return UNAVAILABLE;
  }
};
