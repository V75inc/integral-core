import { useLayoutEffect, useRef, useState } from 'react';

import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Text } from '../../../ui';

import {
  currentManagedShareUrl,
  type ManagedChannelFullPageShareRead,
  type ManagedSharePublication,
  type ManagedShareScope,
} from './managedChannelShareProof';

/**
 * Detached presentation for the managed Configure dialog. An authorized host
 * must first supply a fresh share read for the exact current identity,
 * independently of catalogue/status data. This never fetches or derives a URL.
 */
export function ManagedChannelFullPageShare({ scope, channel, publication, read, canInteract }: {
  scope: ManagedShareScope;
  channel: ManagedChannel | null;
  publication: ManagedSharePublication | null;
  read: ManagedChannelFullPageShareRead;
  canInteract: boolean;
}) {
  if (canInteract !== true || !publication ||
      !channel?.connected || channel.action !== 'configure' || channel.state !== 'active') return null;
  const url = currentManagedShareUrl(read, {
    ...scope,
    slug: channel.definition.slug,
    provisionId: channel.provision_id,
    routeEpoch: channel.route_epoch,
    publication,
  });
  if (!url) return null;

  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      <Text as="p" variant="body-sm" tone="muted" className="break-all">{url}</Text>
      <div className="flex flex-wrap items-center gap-2">
        <a href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="underline">
          <Text as="span" variant="body-sm" weight="medium">Open full-page chat</Text>
        </a>
        <CopyFullPageLink key={JSON.stringify([scope.principalId, scope.workspaceId, scope.sessionFence,
          channel.provision_id, channel.route_epoch, publication.profileKey, publication.publishedVersion, url])} url={url} />
      </div>
    </div>
  );
}

function CopyFullPageLink({ url }: { url: string }) {
  const [status, setStatus] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle');
  const current = useRef(false);
  const copying = useRef(false);
  useLayoutEffect(() => {
    current.current = true;
    return () => { current.current = false; };
  }, []);

  async function copy() {
    if (!current.current || copying.current) return;
    copying.current = true;
    setStatus('copying');
    try {
      await navigator.clipboard.writeText(url);
      if (current.current) setStatus('copied');
    } catch {
      if (current.current) setStatus('failed');
    } finally {
      copying.current = false;
    }
  }

  return <>
    <Button type="button" variant="secondary" size="sm" disabled={status === 'copying'} onClick={() => void copy()}>
      Copy full-page link
    </Button>
    {status === 'copied' && <Text as="p" role="status" variant="body-sm">Full-page link copied.</Text>}
    {status === 'failed' && <Text as="p" role="status" variant="body-sm">Could not copy automatically. Select and copy the link above.</Text>}
  </>;
}
