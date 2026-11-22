/**
 * Detached contract for one already-authorized, fresh host share read.
 * This module neither obtains server authority nor performs I/O.
 */

export interface ManagedShareScope {
  principalId: string;
  workspaceId: string;
  /** Local auth-lifecycle fence, never a credential, cookie, or API field. */
  sessionFence: string;
}

export interface ManagedSharePublication {
  profileKey: string;
  publishedVersion: number;
}

export interface ManagedShareOwner extends ManagedShareScope {
  slug: string;
  provisionId: string;
  routeEpoch: string;
  publication: ManagedSharePublication;
}

const shareProof = Symbol('managed-channel-authorized-share');
const decodedShares = new WeakSet<VerifiedManagedShare>();

interface VerifiedManagedShare {
  readonly [shareProof]: true;
  readonly state: 'verified';
  readonly owner: Readonly<ManagedShareOwner>;
  readonly fullPageUrl: string;
}

export type ManagedChannelFullPageShareRead =
  | { state: 'unavailable' | 'checking' }
  | VerifiedManagedShare;

const KIND = 'approved-full-page-share-v1';
const SHARE_FIELDS = [
  'kind', 'workspace_id', 'slug', 'provision_id', 'route_epoch',
  'profile_key', 'published_version', 'full_page_url',
] as const;

function safeFullPageUrl(raw: unknown): string | null {
  if (typeof raw !== 'string' || raw.length > 2048 || !raw.startsWith('https://') ||
      !/^[\x21-\x7e]+$/.test(raw) || /[\\?#]/.test(raw)) return null;
  try {
    const url = new URL(raw);
    const host = url.hostname.replace(/\.$/, '');
    const loopback = host === 'localhost' || host.endsWith('.localhost') || host.startsWith('127.') ||
      host === '[::1]' || host === '0.0.0.0';
    if (url.protocol !== 'https:' || !host || loopback || url.username || url.password ||
        url.search || url.hash || url.port === '0' || url.href !== raw) return null;
    return url.href;
  } catch {
    return null;
  }
}

function id(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 && value === value.trim();
}

/**
 * Decode exactly one future dedicated authorized read, bound to its captured
 * caller/scope and publication. Never call this on catalogue/configuration or
 * after the originating auth/view was lost; retaining the captured owner
 * allows the renderer to detect later context drift too.
 */
export function decodeAuthorizedManagedShare(
  raw: unknown, requestedFor: ManagedShareOwner,
): ManagedChannelFullPageShareRead {
  const unavailable = { state: 'unavailable' } as const;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) ||
      Object.keys(raw).length !== SHARE_FIELDS.length ||
      !SHARE_FIELDS.every(field => Object.prototype.hasOwnProperty.call(raw, field)) ||
      !requestedFor?.publication || !id(requestedFor.principalId) || !id(requestedFor.workspaceId) || !id(requestedFor.sessionFence) ||
      !id(requestedFor.slug) || !id(requestedFor.provisionId) || !id(requestedFor.routeEpoch) ||
      !id(requestedFor.publication.profileKey) ||
      !Number.isSafeInteger(requestedFor.publication.publishedVersion) ||
      requestedFor.publication.publishedVersion < 1) return unavailable;
  const projection = raw as Record<(typeof SHARE_FIELDS)[number], unknown>;
  if (projection.kind !== KIND || projection.workspace_id !== requestedFor.workspaceId ||
      projection.slug !== requestedFor.slug || projection.provision_id !== requestedFor.provisionId ||
      projection.route_epoch !== requestedFor.routeEpoch ||
      projection.profile_key !== requestedFor.publication.profileKey ||
      projection.published_version !== requestedFor.publication.publishedVersion) return unavailable;
  const url = safeFullPageUrl(projection.full_page_url);
  if (!url) return unavailable;
  const owner = Object.freeze({
    principalId: requestedFor.principalId,
    workspaceId: requestedFor.workspaceId,
    sessionFence: requestedFor.sessionFence,
    slug: requestedFor.slug,
    provisionId: requestedFor.provisionId,
    routeEpoch: requestedFor.routeEpoch,
    publication: Object.freeze({
      profileKey: requestedFor.publication.profileKey,
      publishedVersion: requestedFor.publication.publishedVersion,
    }),
  });
  const read: VerifiedManagedShare = Object.freeze({
    [shareProof]: true as const, state: 'verified' as const, owner, fullPageUrl: url,
  });
  decodedShares.add(read);
  return read;
}

/** Do not let a previously verified result survive any selected-owner change. */
export function currentManagedShareUrl(read: ManagedChannelFullPageShareRead, owner: ManagedShareOwner): string | null {
  if (read.state !== 'verified' || !decodedShares.has(read)) return null;
  const verified = read.owner;
  if (verified.principalId !== owner.principalId || verified.workspaceId !== owner.workspaceId ||
      verified.sessionFence !== owner.sessionFence || verified.slug !== owner.slug ||
      verified.provisionId !== owner.provisionId || verified.routeEpoch !== owner.routeEpoch ||
      verified.publication.profileKey !== owner.publication.profileKey ||
      verified.publication.publishedVersion !== owner.publication.publishedVersion) return null;
  return read.fullPageUrl;
}
