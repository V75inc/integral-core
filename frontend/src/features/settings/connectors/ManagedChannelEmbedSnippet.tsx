import { useId, useLayoutEffect, useRef, useState } from 'react';

import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Text, Textarea } from '../../../ui';
import {
  currentManagedEmbedSnippet,
  type ManagedChannelEmbedRead,
  type ManagedEmbedPolicy,
} from './managedChannelEmbedProof';
import type { ManagedSharePublication, ManagedShareScope } from './managedChannelShareProof';

/** Optional Configure presentation. It never requests, issues, or mounts an embed. */
export function ManagedChannelEmbedSnippet({ scope, channel, publication, policy, read, canInteract }: {
  scope: ManagedShareScope;
  channel: ManagedChannel | null;
  publication: ManagedSharePublication | null;
  policy: ManagedEmbedPolicy | null;
  read: ManagedChannelEmbedRead;
  canInteract: boolean;
}) {
  if (canInteract !== true || !publication || !policy ||
      !channel?.connected || channel.action !== 'configure' || channel.state !== 'active') return null;
  const code = currentManagedEmbedSnippet(read, {
    ...scope,
    slug: channel.definition.slug,
    provisionId: channel.provision_id,
    routeEpoch: channel.route_epoch,
    publication,
    policy,
  });
  if (!code) return null;
  const key = JSON.stringify([
    scope.principalId, scope.workspaceId, scope.sessionFence, channel.definition.slug, channel.provision_id,
    channel.route_epoch, publication.profileKey, publication.publishedVersion, policy.parentOrigin, policy.policyRevision,
    code,
  ]);
  return <CopyCode key={key} code={code} parentOrigin={policy.parentOrigin} />;
}

function CopyCode({ code, parentOrigin }: { code: string; parentOrigin: string }) {
  const id = useId();
  const [status, setStatus] = useState<'idle' | 'copying' | 'copied' | 'error'>('idle');
  const copying = useRef(false);
  const current = useRef(true);

  useLayoutEffect(() => {
    current.current = true;
    return () => { current.current = false; };
  }, []);

  async function copy() {
    if (!current.current || copying.current) return;
    copying.current = true;
    setStatus('copying');
    try {
      await navigator.clipboard.writeText(code);
      if (current.current) setStatus('copied');
    } catch {
      if (current.current) setStatus('error');
    } finally {
      copying.current = false;
    }
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-2">
      <Text as="h3" variant="body-sm" weight="semibold">Embed sharing</Text>
      <Text as="p" variant="body-sm" tone="muted" className="break-all">Approved site: {parentOrigin}</Text>
      <Text as="label" htmlFor={id} variant="body-sm" weight="medium">Embed code</Text>
      <Textarea id={id} value={code} readOnly rows={5} spellCheck={false} />
      <Button type="button" size="sm" variant="secondary" disabled={status === 'copying'} onClick={() => void copy()}>
        Copy embed code
      </Button>
      {status === 'copied' && <Text as="p" role="status" variant="body-sm">Embed code copied.</Text>}
      {status === 'error' && <Text as="p" role="status" variant="body-sm">Could not copy automatically. Select and copy the code above.</Text>}
    </div>
  );
}
