import { useLayoutEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';

import { managedChannelsApi, type ManagedChannel, type ManagedChannelChange } from '../../../api/managedChannels';
import { getSessionGeneration } from '../../../api/session';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { Text } from '../../../ui';
import { StatusPill } from '../components/Field';
import { ManagedChannelProfileAssignment } from './ManagedChannelProfileAssignment';
import { ManagedChannelShareControl, type ManagedChannelShareSource } from './ManagedChannelShareControl';
import { currentManagedChannels, managedProfileOptions } from './managedChannelQueries';

function isPendingWebChatSetup(channel: ManagedChannel): boolean {
  return channel.definition.slug === 'public_web_chat' && channel.connected &&
    channel.configuration_kind === 'published-public-profile-v1' &&
    channel.state === 'inactive' && !channel.can_enable;
}

export function ManagedChannelStatus({ channel }: { channel: ManagedChannel }) {
  const pending = isPendingWebChatSetup(channel);
  return (
    <StatusPill state={channel.state === 'active' ? 'ok' : channel.state === 'unavailable' || pending ? 'warn' : 'idle'}>
      {channel.state === 'active' ? 'Active' : channel.state === 'unavailable' ? 'Unavailable' :
        pending ? 'Setup pending' : channel.state === 'inactive' ? 'Inactive' : 'Not installed'}
    </StatusPill>
  );
}

export function ManagedChannelLoadNotice({ query }: { query: UseQueryResult<ManagedChannel[]> }) {
  if (query.isError && query.fetchStatus === 'idle') {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Text as="p" variant="body-sm" role="alert" tone="danger">Could not check managed channels.</Text>
        <Button type="button" variant="ghost" size="sm" onClick={() => void query.refetch()}>Retry channel check</Button>
      </div>
    );
  }
  if (query.fetchStatus === 'paused') {
    return <Text as="p" variant="body-sm" role="status" tone="muted">Waiting for a connection to check managed channels.</Text>;
  }
  if (query.isFetching) {
    return <Text as="p" variant="body-sm" role="status" tone="muted">Checking managed channels…</Text>;
  }
  return null;
}

export function ManagedChannelSettings({ selection, query, principalId, workspaceId, onClose, onInstalled, onAbandoned, shareSource }: {
  selection: { slug: string; name: string; action: ManagedChannel['action'] };
  query: UseQueryResult<ManagedChannel[]>;
  principalId: string;
  workspaceId: string;
  onClose: () => void;
  onInstalled: () => void;
  onAbandoned: () => void;
  shareSource?: ManagedChannelShareSource;
}) {
  const qc = useQueryClient();
  const submitting = useRef(false);
  const mounted = useRef(true);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingEnable, setPendingEnable] = useState<{ scope: string; generation: string; observedAt: number } | null>(null);
  // Keep a deliberate remove confirmation bound to the observed generation.
  const [removeGeneration, setRemoveGeneration] = useState<string | null>(null);
  const channel = currentManagedChannels(query).find(row =>
    row.definition.slug === selection.slug && row.action === selection.action);
  const generation = channel?.connected ? { provision_id: channel.provision_id, route_epoch: channel.route_epoch } : null;
  const generationKey = generation ? JSON.stringify(generation) : null;
  const confirmingRemove = removeGeneration !== null && removeGeneration === generationKey;
  const scopeKey = JSON.stringify([principalId, workspaceId, selection.slug, getSessionGeneration()]);
  const awaitingReadiness = pendingEnable?.scope === scopeKey && pendingEnable.generation === generationKey &&
    channel?.state === 'unavailable';
  // A settled read of another generation or state retires the local receipt.
  // A later outage on the old generation is not the original pending enable.
  if (pendingEnable && (pendingEnable.scope !== scopeKey ||
      (query.isSuccess && query.fetchStatus === 'idle' && query.dataUpdatedAt > pendingEnable.observedAt && !awaitingReadiness))) {
    setPendingEnable(null);
  }

  const webChatDraft = channel && isPendingWebChatSetup(channel);
  const hasProfileAssignment = channel?.connected && channel.configuration_kind === 'published-public-profile-v1';
  const profileQuery = useQuery(managedProfileOptions(principalId, workspaceId, selection.slug,
    hasProfileAssignment ? generation : null));
  const profile = hasProfileAssignment && profileQuery.isSuccess && profileQuery.fetchStatus === 'idle'
    ? profileQuery.data : null;
  const assignedProfile = profile?.eligible_profiles.find(choice => choice.key === profile.profile_key);
  const assignedEligible = assignedProfile !== undefined;
  const canEnable = channel?.connected && channel.can_enable && (!hasProfileAssignment || assignedEligible);
  const canEditProfile = profile?.editable === true && channel?.state !== 'active';
  const configuringWebChat = channel?.connected && channel.definition.slug === 'public_web_chat';
  const currentPublication = channel?.connected && channel.state === 'active' && profile &&
    profile.provision_id === channel.provision_id && profile.route_epoch === channel.route_epoch &&
    assignedEligible && profile.profile_key && profile.published_version &&
    Number.isSafeInteger(profile.published_version) && profile.published_version > 0
    ? { profileKey: profile.profile_key, publishedVersion: profile.published_version }
    : null;
  const currentShareSource = currentPublication && !busy && !confirmingRemove && shareSource &&
    shareSource.scope.principalId === principalId && shareSource.scope.workspaceId === workspaceId
    ? shareSource : null;

  async function retryProfileCheck() {
    try {
      // First refresh stale scope/provision evidence, then check the exact current configuration.
      const latest = await query.refetch();
      if (!mounted.current || !latest.isSuccess) return;
      const next = latest.data.find(row => row.definition.slug === selection.slug);
      if (next?.connected && next.configuration_kind === 'published-public-profile-v1') {
        await qc.fetchQuery(managedProfileOptions(principalId, workspaceId, selection.slug,
          { provision_id: next.provision_id, route_epoch: next.route_epoch }));
      }
    } catch {
      // Query panels render the read failure without leaking adapter diagnostics.
    }
  }

  type Change = ManagedChannelChange | { kind: 'profile'; profileKey: string | null };
  async function submit(change: Change) {
    if (!channel || submitting.current || !mounted.current) return;
    if (change.kind === 'profile' &&
        (!profile || !generation || !canEditProfile || confirmingRemove ||
          profile.profile_key === change.profileKey ||
          (change.profileKey !== null && !profile.eligible_profiles.some(choice => choice.key === change.profileKey)))) return;
    if (change.kind === 'enabled' && change.enabled && !canEnable) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    setProfileSaved(false);
    setPendingEnable(null);
    setRemoveGeneration(null);
    let saved = false;
    try {
      try {
        if (change.kind === 'profile' && profile && generation) {
          await managedChannelsApi.assignProfile(workspaceId, selection.slug, generation, profile.profile_revision, change.profileKey);
        } else if (change.kind !== 'profile') {
          await managedChannelsApi.change(workspaceId, selection.slug, change);
        }
        saved = true;
      } catch {
        // Do not divulge provider errors or retry mutations. Read back before retrying.
      }
      if (!mounted.current) {
        onAbandoned();
        return;
      }
      const fresh = await query.refetch();
      if (!mounted.current) return;
      if (change.kind === 'profile') {
        const next = fresh.isSuccess ? fresh.data.find(row => row.definition.slug === selection.slug) : null;
        const sameProvision = next?.connected && next.configuration_kind === 'published-public-profile-v1' &&
          next.provision_id === generation?.provision_id;
        let currentProfile;
        if (sameProvision) {
          // Request the exact post-write epoch. Never read back through a pre-write observer.
          const nextGeneration = { provision_id: next.provision_id, route_epoch: next.route_epoch };
          try {
            currentProfile = await qc.fetchQuery(managedProfileOptions(principalId, workspaceId, selection.slug, nextGeneration));
          } catch {
            // A failed readback cannot confirm persistence.
          }
        }
        if (!mounted.current) return;
        if (saved && profile && currentProfile &&
            currentProfile.profile_revision > profile.profile_revision &&
            currentProfile.profile_key === change.profileKey) {
          setProfileSaved(true);
        } else {
          setError('Could not verify the profile assignment. Review the current settings before trying again.');
        }
        return;
      }
      if (!saved) {
        setError('Could not confirm the change. Review the current status before trying again.');
        return;
      }
      if (!fresh.isSuccess) {
        setError('The request was accepted, but the current channel status could not be verified.');
        return;
      }
      const next = fresh.data.find(row => row.definition.slug === selection.slug);
      if (change.kind === 'add' && next?.connected === true) {
        onInstalled();
      } else if (change.kind === 'remove' && next?.connected !== true) {
        onClose();
      } else if (change.kind === 'enabled' && change.enabled && channel.connected && channel.configuration_kind === 'published-public-profile-v1' &&
          next?.connected && next.configuration_kind === 'published-public-profile-v1' &&
          next.state === 'unavailable' && next.provision_id === change.generation.provision_id &&
          next.route_epoch !== change.generation.route_epoch) {
        // Acceptance and the exact provision's new epoch confirm the request
        // returned, not that the host is admitting visitors or issuing links.
        setPendingEnable({ scope: scopeKey, observedAt: fresh.dataUpdatedAt, generation: JSON.stringify({
          provision_id: next.provision_id, route_epoch: next.route_epoch,
        }) });
      } else if (!next?.connected ||
          (change.kind === 'enabled' && (change.enabled ? next.state !== 'active' : next.state !== 'inactive'))) {
        setError('The requested channel status could not be confirmed. Review the current status.');
      }
    } catch {
      if (mounted.current) setError('Could not verify the current channel status. Check it again before trying another change.');
    } finally {
      submitting.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <Modal open title={`${selection.action === 'add' ? 'Add' : 'Configure'} ${selection.name}`}
      onClose={() => { if (!submitting.current) onClose(); }}>
      <Modal.Body>
        <div className="flex flex-col gap-3">
          {!channel && <ManagedChannelLoadNotice query={query} />}
          {!channel && query.isSuccess && query.fetchStatus === 'idle' && (
            <Text as="p" variant="body-sm" role="status">This setup is no longer available. Close to see the current connector list.</Text>
          )}
          {channel && (
            <>
              <Text as="p">{channel.definition.description}</Text>
              <div><ManagedChannelStatus channel={channel} /></div>
              {awaitingReadiness && !confirmingRemove ? (
                <div className="flex flex-wrap items-center gap-2">
                  <Text as="p" variant="body-sm" role="status" tone="muted">
                    Enable request accepted. Waiting for the host to be ready.
                  </Text>
                  <Button type="button" variant="ghost" size="sm" disabled={busy}
                    onClick={() => void retryProfileCheck()}>Refresh channel status</Button>
                </div>
              ) : webChatDraft && !confirmingRemove ? (
                <div className="flex flex-col items-start gap-2">
                  <Text as="p" variant="body-sm" tone="muted">
                    Web Chat is added but not live. You can save an eligible published public profile while hosting setup is pending.
                  </Text>
                  {profile && (
                    <Text as="p" variant="body-sm">
                      {assignedProfile ? `Saved profile: ${assignedProfile.name}` :
                        profile.profile_key ? 'The saved profile is no longer eligible.' : 'No profile saved yet.'}
                    </Text>
                  )}
                  <Button type="button" variant="ghost" size="sm" disabled={busy}
                    onClick={() => void retryProfileCheck()}>Refresh setup status</Button>
                </div>
              ) : (
                <Text as="p" variant="body-sm" tone="muted">
                  {!channel.connected
                    ? channel.definition.slug === 'public_web_chat'
                      ? 'Adding saves Web Chat to this workspace for setup. It does not publish a chat or turn on messaging.'
                      : 'Adding installs this channel for the workspace. It does not turn on messaging.'
                    : channel.state === 'unavailable' || (channel.state === 'inactive' && !channel.can_enable)
                      ? 'This channel cannot be enabled right now.'
                      : 'Manage whether this channel can receive messages for the workspace.'}
                </Text>
              )}
              {hasProfileAssignment && !confirmingRemove && (
                <>
                  {profile ? (
                    <ManagedChannelProfileAssignment
                      key={JSON.stringify([generationKey, profile.profile_revision])}
                      configuration={profile}
                      locked={busy || !canEditProfile}
                      onSave={profileKey => void submit({ kind: 'profile', profileKey })}
                    />
                  ) : profileQuery.isError && profileQuery.fetchStatus === 'idle' ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Text as="p" variant="body-sm" role="alert" tone="danger">Could not check the published profile assignment.</Text>
                      <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void retryProfileCheck()}>
                        Retry profile check
                      </Button>
                    </div>
                  ) : (
                    <Text as="p" variant="body-sm" role="status" tone="muted">Checking the published profile assignment…</Text>
                  )}
                </>
              )}
              {configuringWebChat && !confirmingRemove && (
                <div className="flex flex-col gap-2">
                  <Text as="h3" variant="body-sm" weight="semibold">Publishing and sharing</Text>
                  <Text as="p" variant="body-sm" tone="muted">
                    Hosting and publishing belong to this Web Chat connector. There is no separate connector to install.
                  </Text>
                  <Text as="p" variant="body-sm" tone="muted">
                    {channel.state !== 'active'
                      ? channel.can_enable
                        ? 'Hosting is ready, but Web Chat is not live. Full-page links, embed code and API access are not available yet.'
                        : 'Hosting readiness is not confirmed. No full-page link, embed code or API access is available here.'
                      : !currentPublication
                        ? 'Sharing details require a verified current published public profile.'
                        : currentShareSource
                          ? 'Check the full-page link below. No verified embed code or API access details are available here.'
                          : 'No verified full-page link, embed code or API access details are available here.'}
                  </Text>
                </div>
              )}
              {currentShareSource && currentPublication && profile && channel.connected && (
                <ManagedChannelShareControl channel={channel} source={currentShareSource}
                  profileRevision={profile.profile_revision} publication={currentPublication} />
              )}
              {confirmingRemove && (
                <Text as="p" variant="body-sm">Remove this channel from the workspace? It will no longer receive messages here.</Text>
              )}
            </>
          )}
          {profileSaved && <Text as="p" variant="body-sm" role="status">Profile assignment saved.</Text>}
          {error && <Text as="p" variant="body-sm" tone="danger" role="alert">{error}</Text>}
        </div>
      </Modal.Body>
      <Modal.Footer>
        {channel?.action === 'add' && (
          <Button type="button" variant="primary" size="sm" disabled={busy || !channel.can_install}
            onClick={() => void submit({ kind: 'add' })}>Add channel</Button>
        )}
        {channel?.action === 'configure' && generation && (
          <>
            {!confirmingRemove && (channel.can_enable || channel.state === 'active' || channel.state === 'unavailable') && (
              <Button type="button" size="sm" variant={channel.can_enable ? 'primary' : 'secondary'} disabled={busy || (channel.can_enable && !canEnable)}
                onClick={() => void submit({ kind: 'enabled', enabled: channel.can_enable, generation })}>
                {channel.can_enable ? 'Enable channel' : 'Disable channel'}
              </Button>
            )}
            <Button type="button" size="sm" variant="danger" disabled={busy}
              onClick={() => confirmingRemove
                ? void submit({ kind: 'remove', generation })
                : setRemoveGeneration(generationKey)}>
              {confirmingRemove ? 'Confirm remove channel' : 'Remove channel'}
            </Button>
          </>
        )}
        <Button type="button" variant="ghost" size="sm" disabled={busy}
          onClick={confirmingRemove ? () => setRemoveGeneration(null) : onClose}>
          {confirmingRemove ? 'Cancel removal' : 'Close'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
