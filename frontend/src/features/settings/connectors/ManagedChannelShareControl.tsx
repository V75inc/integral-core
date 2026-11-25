import { useLayoutEffect, useRef, useState } from 'react';

import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Text } from '../../../ui';
import { ManagedChannelFullPageShare } from './ManagedChannelFullPageShare';
import {
  decodeAuthorizedManagedShare,
  type ManagedChannelFullPageShareRead,
  type ManagedShareOwner,
  type ManagedSharePublication,
  type ManagedShareScope,
} from './managedChannelShareProof';
import type { ManagedChannelShareReader } from './managedChannelShareReader';

export interface ManagedChannelShareSource {
  scope: ManagedShareScope;
  read: ManagedChannelShareReader;
}

type ConnectedChannel = Extract<ManagedChannel, { connected: true }>;

/** Optional in-dialog seam; an unapproved host never instantiates this control. */
export function ManagedChannelShareControl({ channel, source, publication, profileRevision, readOnOpen = false }: {
  channel: ConnectedChannel;
  source: ManagedChannelShareSource;
  publication: ManagedSharePublication;
  profileRevision: number;
  readOnOpen?: boolean;
}) {
  const { scope } = source;
  if (channel.state !== 'active' || !scope.principalId || !scope.sessionFence ||
      !scope.workspaceId || !publication?.profileKey ||
      !Number.isSafeInteger(publication.publishedVersion) || publication.publishedVersion < 1) return null;
  const owner: ManagedShareOwner = {
    ...scope,
    slug: channel.definition.slug,
    provisionId: channel.provision_id,
    routeEpoch: channel.route_epoch,
    publication,
  };
  const key = JSON.stringify([
    owner.principalId, owner.workspaceId, owner.sessionFence, owner.slug, owner.provisionId,
    owner.routeEpoch, publication.profileKey, publication.publishedVersion, profileRevision,
  ]);
  return <CurrentShareRead readOnOpen={readOnOpen} key={key} owner={owner} channel={channel} reader={source.read} />;
}

function CurrentShareRead({ owner, channel, reader, readOnOpen }: {
  owner: ManagedShareOwner;
  channel: ConnectedChannel;
  reader: ManagedChannelShareReader;
  readOnOpen: boolean;
}) {
  const pending = useRef<AbortController | null>(null);
  const [read, setRead] = useState<ManagedChannelFullPageShareRead | null>(null);
  const [previousReader, setPreviousReader] = useState(() => reader);
  if (previousReader !== reader) {
    setPreviousReader(() => reader);
    setRead(null);
  }

  useLayoutEffect(() => {
    function abort() {
      pending.current?.abort();
      pending.current = null;
    }
    function invalidate() {
      abort();
      setRead(null);
    }
    function storage(event: StorageEvent) {
      if (!event.key || ['t75_user', 't75_token', 't75_refresh_token'].includes(event.key)) invalidate();
    }
    function visibility() {
      if (document.hidden) invalidate();
    }
    // Opening the group reads an already-issued URL for this exact owner.
    if (readOnOpen) void refresh();
    window.addEventListener('integral:tokens-updated', invalidate);
    window.addEventListener('integral:logout', invalidate);
    window.addEventListener('integral:records-changed', invalidate);
    window.addEventListener('storage', storage);
    window.addEventListener('offline', invalidate);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      abort();
      window.removeEventListener('integral:tokens-updated', invalidate);
      window.removeEventListener('integral:logout', invalidate);
      window.removeEventListener('integral:records-changed', invalidate);
      window.removeEventListener('storage', storage);
      window.removeEventListener('offline', invalidate);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [reader, readOnOpen]);

  async function refresh() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setRead({ state: 'checking' });
    let data: ManagedChannelFullPageShareRead;
    try {
      data = decodeAuthorizedManagedShare(await reader(owner, controller.signal), owner);
    } catch {
      data = { state: 'unavailable' };
    }
    if (controller.signal.aborted || pending.current !== controller) return;
    pending.current = null;
    setRead(data);
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-2">
      {!readOnOpen && <Button type="button" size="sm" variant="secondary" disabled={read?.state === 'checking'}
        onClick={() => void refresh()}>
        Check full-page link
      </Button>}
      {read?.state === 'checking' && (
        <Text as="p" variant="body-sm" role="status" tone="muted">Checking full-page sharing…</Text>
      )}
      {(read?.state === 'unavailable' || (readOnOpen && !read)) && (
        <Text as="p" variant="body-sm" role="status" tone="muted">Reopen Full-page chat to verify the current link.</Text>
      )}
      {read && (
        <ManagedChannelFullPageShare scope={owner} channel={channel} publication={owner.publication} read={read} canInteract />
      )}
    </div>
  );
}
