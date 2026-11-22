import type { ManagedChannel } from '../../../api/managedChannels';
import { Text } from '../../../ui';

interface ShareScope {
  principalId: string;
  workspaceId: string;
}

interface ShareOwner extends ShareScope {
  slug: string;
  provision_id: string;
  route_epoch: string;
}

/** Local ownership of an independently authorized, current server read. Never cache as catalogue data. */
export type ManagedChannelFullPageShareRead =
  | { state: 'unavailable' | 'checking' }
  | { state: 'verified'; owner: ShareOwner; fullPageUrl: unknown };

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

/**
 * Detached presentation for the managed Configure dialog. An authorized host
 * must first supply a fresh share read for the exact current identity,
 * independently of catalogue/status data. This never fetches or derives a URL.
 */
export function ManagedChannelFullPageShare({ scope, channel, read, canInteract }: {
  scope: ShareScope;
  channel: ManagedChannel | null;
  read: ManagedChannelFullPageShareRead;
  canInteract: boolean;
}) {
  if (canInteract !== true || read.state !== 'verified' ||
      !scope.principalId || !scope.workspaceId ||
      !channel?.connected || channel.action !== 'configure' || channel.state !== 'active') return null;
  const { owner } = read;
  if (!owner || owner.principalId !== scope.principalId || owner.workspaceId !== scope.workspaceId ||
      owner.slug !== channel.definition.slug || owner.provision_id !== channel.provision_id ||
      owner.route_epoch !== channel.route_epoch) return null;
  const url = safeFullPageUrl(read.fullPageUrl);
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
