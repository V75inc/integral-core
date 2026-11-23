import { canonicalManagedEmbedHttps, isCanonicalManagedEmbedOrigin } from '../../../api/managedChannelEmbedAuthority';
import type { ManagedShareOwner } from './managedChannelShareProof';

export { isCanonicalManagedEmbedOrigin } from '../../../api/managedChannelEmbedAuthority';

/** Independent of the full-page link and the channel's route epoch. */
export interface ManagedEmbedPolicy {
  parentOrigin: string;
  policyRevision: number;
}

export interface ManagedEmbedOwner extends ManagedShareOwner {
  policy: ManagedEmbedPolicy;
}

const embedProof = Symbol('managed-channel-approved-embed');
const verified = new WeakSet<VerifiedManagedEmbed>();

interface VerifiedManagedEmbed {
  readonly [embedProof]: true;
  readonly state: 'verified';
  readonly owner: Readonly<ManagedEmbedOwner>;
  readonly snippet: string;
}

export type ManagedChannelEmbedRead =
  | { state: 'checking' | 'unavailable' }
  | VerifiedManagedEmbed;

// Match the host's policy: a public path cannot contain a bootstrap or session credential.
const credentialText = /(?:iweb|iwes)_[A-Za-z0-9_-]{32,128}/;

const fields = [
  'kind', 'workspace_id', 'slug', 'provision_id', 'route_epoch', 'profile_key',
  'published_version', 'embed_policy_revision', 'parent_origin', 'embed_page_url',
] as const;

function key(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 &&
    value === value.trim();
}

function version(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0;
}

// This JavaScript is a convenience loader, not an enforcement boundary. The
// frame is a different origin; same-origin permission keeps its own API origin
// intact, not the parent's. Only its script runtime is otherwise permitted.
function buildSnippet(embedUrl: string, parentOrigin: string): string {
  return [
    '<div data-integral-web-chat></div>',
    '<script>',
    '(() => {',
    '  const mount = document.currentScript?.previousElementSibling;',
    `  if (!mount?.hasAttribute("data-integral-web-chat") || window.location.origin !== ${JSON.stringify(parentOrigin)}) return;`,
    '  const frame = document.createElement("iframe");',
    '  frame.title = "Integral Web Chat";',
    '  frame.setAttribute("loading", "lazy");',
    '  frame.setAttribute("referrerpolicy", "no-referrer");',
    '  frame.setAttribute("sandbox", "allow-scripts allow-same-origin");',
    '  frame.style.width = "100%";',
    '  frame.style.height = "600px";',
    '  frame.style.border = "0";',
    `  frame.src = ${JSON.stringify(embedUrl)};`,
    '  mount.replaceChildren(frame);',
    '})();',
    '</script>',
  ].join('\n');
}

/**
 * Feed only a separate authenticated host response for a currently approved
 * embed route and exact parent origin. This is a detached consumer contract,
 * not a request endpoint, origin editor, or issuer. Use a separate public
 * opaque embed reference. The backend must enforce frame-ancestors, exact
 * origin-bound session admission, current policy/revocation, and a browser
 * storage strategy that does not rely on third-party cookies being available.
 */
export function decodeApprovedManagedEmbed(raw: unknown, requestedFor: ManagedEmbedOwner): ManagedChannelEmbedRead {
  const unavailable = { state: 'unavailable' } as const;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) ||
      Object.keys(raw).length !== fields.length ||
      !fields.every(field => Object.prototype.hasOwnProperty.call(raw, field)) ||
      !requestedFor?.publication || !requestedFor.policy ||
      !key(requestedFor.principalId) || !key(requestedFor.sessionFence) || !key(requestedFor.workspaceId) ||
      !key(requestedFor.slug) || !key(requestedFor.provisionId) || !key(requestedFor.routeEpoch) ||
      !key(requestedFor.publication.profileKey) || !version(requestedFor.publication.publishedVersion) ||
      !version(requestedFor.policy.policyRevision)) return unavailable;
  const response = raw as Record<(typeof fields)[number], unknown>;
  if (response.kind !== 'approved-web-chat-embed-v1' || response.workspace_id !== requestedFor.workspaceId ||
      response.slug !== requestedFor.slug || response.provision_id !== requestedFor.provisionId ||
      response.route_epoch !== requestedFor.routeEpoch || response.profile_key !== requestedFor.publication.profileKey ||
      response.published_version !== requestedFor.publication.publishedVersion ||
      response.parent_origin !== requestedFor.policy.parentOrigin ||
      response.embed_policy_revision !== requestedFor.policy.policyRevision) return unavailable;

  const parentOrigin = response.parent_origin;
  const frame = canonicalManagedEmbedHttps(response.embed_page_url);
  if (!isCanonicalManagedEmbedOrigin(parentOrigin) ||
      !frame || frame.href !== response.embed_page_url ||
      !/^\/embed\/[A-Za-z0-9_-]{1,128}$/.test(frame.pathname) ||
      credentialText.test(frame.pathname) || frame.origin === parentOrigin) return unavailable;

  const owner = Object.freeze({
    principalId: requestedFor.principalId,
    sessionFence: requestedFor.sessionFence,
    workspaceId: requestedFor.workspaceId,
    slug: requestedFor.slug,
    provisionId: requestedFor.provisionId,
    routeEpoch: requestedFor.routeEpoch,
    publication: Object.freeze({
      profileKey: requestedFor.publication.profileKey,
      publishedVersion: requestedFor.publication.publishedVersion,
    }),
    policy: Object.freeze({
      parentOrigin,
      policyRevision: requestedFor.policy.policyRevision,
    }),
  });
  const proof: VerifiedManagedEmbed = Object.freeze({
    [embedProof]: true as const,
    state: 'verified' as const,
    owner,
    snippet: buildSnippet(frame.href, parentOrigin),
  });
  verified.add(proof);
  return proof;
}

export function currentManagedEmbedSnippet(read: ManagedChannelEmbedRead, current: ManagedEmbedOwner): string | null {
  if (read.state !== 'verified' || !verified.has(read)) return null;
  const owner = read.owner;
  if (owner.principalId !== current.principalId || owner.sessionFence !== current.sessionFence ||
      owner.workspaceId !== current.workspaceId || owner.slug !== current.slug ||
      owner.provisionId !== current.provisionId || owner.routeEpoch !== current.routeEpoch ||
      owner.publication.profileKey !== current.publication.profileKey ||
      owner.publication.publishedVersion !== current.publication.publishedVersion ||
      owner.policy.parentOrigin !== current.policy.parentOrigin ||
      owner.policy.policyRevision !== current.policy.policyRevision) return null;
  return read.snippet;
}
