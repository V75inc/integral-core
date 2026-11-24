import { useLayoutEffect, useRef, useState } from 'react';

import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Text } from '../../../ui';
import { ManagedChannelEmbedSnippet } from './ManagedChannelEmbedSnippet';
import {
  currentManagedEmbedSnippet,
  isCanonicalManagedEmbedOrigin,
  type ManagedChannelEmbedRead,
  type ManagedEmbedOwner,
  type ManagedEmbedPolicy,
} from './managedChannelEmbedProof';
import type { ManagedChannelEmbedShareReader } from './managedChannelEmbedShareReader';
import type { ManagedSharePublication, ManagedShareScope } from './managedChannelShareProof';

export interface ManagedChannelEmbedShareSource {
  scope: ManagedShareScope;
  read: ManagedChannelEmbedShareReader;
}

type ActiveChannel = Extract<ManagedChannel, { connected: true }>;

interface EmbedShareProps {
  scope: ManagedShareScope | null;
  source?: ManagedChannelEmbedShareSource;
  channel: ManagedChannel | null;
  publication: ManagedSharePublication | null;
  policy: ManagedEmbedPolicy | null;
  profileRevision: number;
  canInteract: boolean;
}

function identity(value: unknown, max = 256): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max &&
    value.trim() === value && !/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
}

function positive(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0;
}

/**
 * Detached Configure control. A reviewed caller must supply the current
 * authenticated manager scope, enabled publication, and server-approved policy
 * for the selected origin. Unavailable or dismissed callers must withdraw
 * interaction permission, not retain stale successful evidence.
 */
export function ManagedChannelEmbedShareControl({
  scope, source, channel, publication, policy, profileRevision, canInteract,
}: EmbedShareProps) {
  if (canInteract !== true || !scope || !source?.scope || typeof source.read !== 'function' ||
      !identity(scope.principalId) || !identity(scope.workspaceId) || !identity(scope.sessionFence) ||
      source.scope.principalId !== scope.principalId ||
      source.scope.workspaceId !== scope.workspaceId || source.scope.sessionFence !== scope.sessionFence ||
      channel?.connected !== true || channel.action !== 'configure' || channel.state !== 'active' ||
      channel.definition.visitor_surface !== true || channel.configuration_kind !== 'published-public-profile-v1' ||
      !identity(channel.provision_id) || !/^(?:0|[1-9][0-9]{0,19})$/.test(channel.route_epoch) ||
      !publication || !identity(publication.profileKey, 128) || !positive(publication.publishedVersion) ||
      !policy || !isCanonicalManagedEmbedOrigin(policy.parentOrigin) || !positive(policy.policyRevision) ||
      !Number.isSafeInteger(profileRevision) || profileRevision < 0) return null;

  const owner: ManagedEmbedOwner = Object.freeze({
    principalId: scope.principalId,
    sessionFence: scope.sessionFence,
    workspaceId: scope.workspaceId,
    slug: channel.definition.slug,
    provisionId: channel.provision_id,
    routeEpoch: channel.route_epoch,
    publication: Object.freeze({ profileKey: publication.profileKey, publishedVersion: publication.publishedVersion }),
    policy: Object.freeze({ parentOrigin: policy.parentOrigin, policyRevision: policy.policyRevision }),
  });
  const key = JSON.stringify([
    owner.principalId, owner.workspaceId, owner.sessionFence, owner.slug, owner.provisionId, owner.routeEpoch,
    owner.publication.profileKey, owner.publication.publishedVersion, profileRevision, owner.policy.parentOrigin,
    owner.policy.policyRevision,
  ]);
  return <CurrentEmbedShare key={key} owner={owner} channel={channel} reader={source.read} />;
}

function CurrentEmbedShare({ owner, channel, reader }: {
  owner: ManagedEmbedOwner;
  channel: ActiveChannel;
  reader: ManagedChannelEmbedShareReader;
}) {
  const pending = useRef<AbortController | null>(null);
  const [read, setRead] = useState<ManagedChannelEmbedRead | null>(null);
  const [previousReader, setPreviousReader] = useState(() => reader);
  if (previousReader !== reader) {
    setPreviousReader(() => reader);
    setRead(null);
  }

  useLayoutEffect(() => {
    function abort() {
      const request = pending.current;
      pending.current = null;
      request?.abort();
    }
    function invalidate() {
      abort();
      setRead(null);
    }
    function storage(event: StorageEvent) {
      if (!event.key || ['t75_user', 't75_token', 't75_refresh_token', 'integral.scope'].includes(event.key)) {
        invalidate();
      }
    }
    function visibility() {
      if (document.hidden) invalidate();
    }
    const events = ['integral:tokens-updated', 'integral:logout', 'integral:records-changed', 'offline', 'pagehide'];
    events.forEach(event => window.addEventListener(event, invalidate));
    window.addEventListener('storage', storage);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      abort();
      events.forEach(event => window.removeEventListener(event, invalidate));
      window.removeEventListener('storage', storage);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [reader]);

  async function refresh() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setRead({ state: 'checking' });
    let proof: ManagedChannelEmbedRead = { state: 'unavailable' };
    try {
      const result = await reader(owner, controller.signal);
      if (result && currentManagedEmbedSnippet(result, owner)) proof = result;
    } catch {
      // Never display diagnostics or an unverified snippet.
    }
    if (controller.signal.aborted || pending.current !== controller) return;
    pending.current = null;
    setRead(proof);
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-2">
      <Button type="button" variant="secondary" size="sm" disabled={read?.state === 'checking'} onClick={() => void refresh()}>
        Check embed code
      </Button>
      {read?.state === 'checking' && (
        <Text as="p" variant="body-sm" role="status" tone="muted">Checking embed sharing…</Text>
      )}
      {read?.state === 'unavailable' && (
        <Text as="p" variant="body-sm" role="status" tone="muted">No verified embed code is available.</Text>
      )}
      {read?.state === 'verified' && (
        <ManagedChannelEmbedSnippet scope={owner} channel={channel} publication={owner.publication}
          policy={owner.policy} read={read} canInteract />
      )}
    </div>
  );
}
