import type { ManagedChannel } from '../../../api/managedChannels';
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
      <Text as="h3" variant="body-sm" weight="semibold">Full-page sharing</Text>
      <Text as="p" variant="body-sm" tone="muted" className="break-all">{url}</Text>
      <a href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="underline">
        <Text as="span" variant="body-sm" weight="medium">Open full-page chat</Text>
      </a>
    </div>
  );
}
