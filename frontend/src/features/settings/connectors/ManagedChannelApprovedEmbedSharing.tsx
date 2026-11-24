import { useId, useState } from 'react';

import { isCanonicalManagedEmbedOrigin } from '../../../api/managedChannelEmbedAuthority';
import type { ManagedChannel, ManagedChannelApprovedEmbedPolicy } from '../../../api/managedChannels';
import { Select, Text } from '../../../ui';
import { ManagedChannelEmbedShareControl } from './ManagedChannelEmbedShareControl';
import { readManagedChannelEmbedShare } from './managedChannelEmbedShareReader';
import type { ManagedSharePublication, ManagedShareScope } from './managedChannelShareProof';

type ConnectedChannel = Extract<ManagedChannel, { connected: true }>;

export function ManagedChannelApprovedEmbedSharing({ scope, channel, publication, profileRevision, policies }: {
  scope: ManagedShareScope;
  channel: ConnectedChannel;
  publication: ManagedSharePublication;
  profileRevision: number;
  policies: readonly ManagedChannelApprovedEmbedPolicy[];
}) {
  // The viewer is controlled by a fresh exact Configure read. A changed
  // policy read, even with the same origin, retires selection and any proof.
  const key = JSON.stringify([scope.principalId, scope.workspaceId, scope.sessionFence,
    channel.provision_id, channel.route_epoch, publication.profileKey, publication.publishedVersion, profileRevision,
    policies.map(policy => [policy.parent_origin, policy.embed_policy_revision])]);
  if (channel.state !== 'active' || channel.definition.visitor_surface !== true || !policies.length) return null;
  return <ApprovedPolicySelection key={key} scope={scope} channel={channel} publication={publication}
    profileRevision={profileRevision} policies={policies} />;
}

function ApprovedPolicySelection({ scope, channel, publication, profileRevision, policies }: {
  scope: ManagedShareScope;
  channel: ConnectedChannel;
  publication: ManagedSharePublication;
  profileRevision: number;
  policies: readonly ManagedChannelApprovedEmbedPolicy[];
}) {
  const id = useId();
  const [origin, setOrigin] = useState('');
  const policy = policies.find(choice => choice.parent_origin === origin && isCanonicalManagedEmbedOrigin(choice.parent_origin));
  return (
    <div className="flex min-w-0 flex-col items-start gap-2">
      <Text as="label" htmlFor={id} variant="body-sm" weight="medium">Approved embedding site</Text>
      <Select id={id} value={origin} onChange={event => setOrigin(event.target.value)}>
        <option value="">Choose an approved site</option>
        {policies.map(choice => <option key={choice.parent_origin} value={choice.parent_origin}>{choice.parent_origin}</option>)}
      </Select>
      {policy && (
        <ManagedChannelEmbedShareControl scope={scope} channel={channel} publication={publication} profileRevision={profileRevision}
          policy={{ parentOrigin: policy.parent_origin, policyRevision: policy.embed_policy_revision }}
          source={{ scope, read: readManagedChannelEmbedShare }} canInteract />
      )}
    </div>
  );
}
