import './managedChannelSettings.css';
import { WebChatAppearance } from './WebChatAppearance';
import { type VisitorTheme } from '../../public-web-chat/visitorTheme';
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Braces, ChevronDown, Code2, ExternalLink, MessageCircle, Palette, type LucideIcon } from 'lucide-react';
import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';

import { managedChannelsApi, type ManagedChannel, type ManagedChannelChange } from '../../../api/managedChannels';
import { getSessionGeneration } from '../../../api/session';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { Surface, Text, Textarea } from '../../../ui';
import { StatusPill } from '../components/Field';
import { ManagedChannelProfileAssignment } from './ManagedChannelProfileAssignment';
import { ManagedWebChatPrivateTest } from './ManagedWebChatPrivateTest';
import { ManagedChannelApprovedEmbedSharing } from './ManagedChannelApprovedEmbedSharing';
import { ManagedChannelShareControl, type ManagedChannelShareSource } from './ManagedChannelShareControl';
import { readManagedChannelShare } from './managedChannelShareReader';
import { currentManagedChannels, managedProfileOptions } from './managedChannelQueries';

function isPendingWebChatSetup(channel: ManagedChannel): boolean {
  return channel.definition.visitor_surface === true && channel.connected &&
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

export function ManagedChannelSettings({ selection, query, principalId, workspaceId, onClose, onInstalled, onAbandoned, shareSource, canReadPublishedShare = false }: {
  selection: { slug: string; name: string; action: ManagedChannel['action'] };
  query: UseQueryResult<ManagedChannel[]>;
  principalId: string;
  workspaceId: string;
  onClose: () => void;
  onInstalled: () => void;
  onAbandoned: () => void;
  shareSource?: ManagedChannelShareSource;
  canReadPublishedShare?: boolean;
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
  const sessionFence = String(getSessionGeneration());
  const scopeKey = JSON.stringify([principalId, workspaceId, selection.slug, sessionFence]);
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
  const visitorSurface = channel?.definition.visitor_surface === true;
  const profileQuery = useQuery(managedProfileOptions(principalId, workspaceId, selection.slug,
    hasProfileAssignment ? generation : null, visitorSurface));
  const profile = hasProfileAssignment && profileQuery.isSuccess && profileQuery.fetchStatus === 'idle'
    ? profileQuery.data : null;
  const assignedProfile = profile?.eligible_profiles.find(choice => choice.key === profile.profile_key);
  const assignedEligible = assignedProfile !== undefined;
  const canEnable = channel?.connected && channel.can_enable && (!hasProfileAssignment || assignedEligible);
  const canEditProfile = profile?.editable === true && channel?.state !== 'active';
  const configuringWebChat = channel?.connected && channel.definition.visitor_surface === true;
  const draftKey = JSON.stringify([scopeKey, generationKey, profile?.profile_revision]);
  const [draft, setDraft] = useState<{key: string; value: string} | null>(null);
  const chosenProfile = draft?.key === draftKey ? draft.value : profile?.profile_key ?? '';
  const [appearanceDraft, setAppearanceDraft] = useState<{ key: string; theme: VisitorTheme }>();
  const savedTheme = profile?.theme ?? 'system';
  const selectedTheme = appearanceDraft?.key === draftKey ? appearanceDraft.theme : savedTheme;
  const appearanceDirty = Boolean(profile && selectedTheme !== savedTheme);
  const dirty = Boolean(profile && (chosenProfile || null) !== profile.profile_key);
  const canSaveConfiguration = !busy && (dirty ? canEditProfile &&
    (!chosenProfile || profile?.eligible_profiles.some(choice => choice.key === chosenProfile)) : appearanceDirty);
  const [savingConfiguration, setSavingConfiguration] = useState(false);
  const assignedPublication = channel?.connected && profile &&
    profile.provision_id === channel.provision_id && profile.route_epoch === channel.route_epoch &&
    assignedEligible && profile.profile_key && profile.published_version &&
    Number.isSafeInteger(profile.published_version) && profile.published_version > 0
    ? { profileKey: profile.profile_key, publishedVersion: profile.published_version }
    : null;
  const currentPublication = channel?.state === 'active' ? assignedPublication : null;
  const privateTestOwner = configuringWebChat && channel?.connected && assignedPublication &&
    canReadPublishedShare && principalId && workspaceId && !busy && !confirmingRemove && profile?.private_test_available === true
    ? { principalId, workspaceId, sessionFence, slug: channel.definition.slug,
      provisionId: channel.provision_id, routeEpoch: channel.route_epoch,
      profileKey: assignedPublication.profileKey, publishedVersion: assignedPublication.publishedVersion } : null;
  // For Web Chat the authenticated, current Configure projection is the sole
  // authority to offer a check; an injected generic source cannot bypass it.
  const currentManagerScope = configuringWebChat && currentPublication && !busy && !confirmingRemove &&
    canReadPublishedShare && principalId && workspaceId && profile?.editable === false
    ? { principalId, workspaceId, sessionFence } : null;
  const currentShareSource: ManagedChannelShareSource | null = !currentPublication || busy || confirmingRemove
    ? null
    : configuringWebChat
      ? currentManagerScope && profile?.full_page_share_check_available === true
        ? { scope: currentManagerScope, read: readManagedChannelShare }
        : null
      : shareSource && shareSource.scope.principalId === principalId && shareSource.scope.workspaceId === workspaceId
        ? shareSource : null;
  const currentEmbedPolicies = currentManagerScope && profile?.approved_embed_policies?.length
    ? profile.approved_embed_policies : null;

  async function retryProfileCheck() {
    try {
      // First refresh stale scope/provision evidence, then check the exact current configuration.
      const latest = await query.refetch();
      if (!mounted.current || !latest.isSuccess) return;
      const next = latest.data.find(row => row.definition.slug === selection.slug);
      if (next?.connected && next.configuration_kind === 'published-public-profile-v1') {
        await qc.fetchQuery(managedProfileOptions(principalId, workspaceId, selection.slug,
          { provision_id: next.provision_id, route_epoch: next.route_epoch },
          next.definition.visitor_surface === true));
      }
    } catch {
      // Query panels render the read failure without leaking adapter diagnostics.
    }
  }

  type Change = ManagedChannelChange | { kind: 'profile'; profileKey: string | null; theme?: VisitorTheme } | { kind: 'appearance'; theme: VisitorTheme };
  async function submit(change: Change) {
    if (!channel || submitting.current || !mounted.current) return;
    if (change.kind === 'profile' &&
        (!profile || !generation || !canEditProfile || confirmingRemove ||
          profile.profile_key === change.profileKey ||
          (change.profileKey !== null && !profile.eligible_profiles.some(choice => choice.key === change.profileKey)))) return;
    if (change.kind === 'enabled' && change.enabled && !canEnable) return;
    submitting.current = true;
    const configuring = change.kind === 'profile' || change.kind === 'appearance';
    setSavingConfiguration(configuring);
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
        } else if (change.kind === 'appearance' && profile) {
          await managedChannelsApi.saveAppearance(workspaceId, profile, change.theme);
        } else if (change.kind !== 'profile' && change.kind !== 'appearance') {
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
      if (change.kind === 'appearance') {
        const readback = await profileQuery.refetch();
        if (mounted.current && saved && readback.isSuccess && readback.data.theme === change.theme) setProfileSaved(true);
        else if (mounted.current) setError('Could not verify the saved appearance.');
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
            currentProfile = await qc.fetchQuery(managedProfileOptions(
              principalId, workspaceId, selection.slug, nextGeneration,
              next.definition.visitor_surface === true,
            ));
            if (saved && change.theme && currentProfile.profile_key === change.profileKey && mounted.current) {
              await managedChannelsApi.saveAppearance(workspaceId, currentProfile, change.theme);
              currentProfile = await qc.fetchQuery(managedProfileOptions(
                principalId, workspaceId, selection.slug, nextGeneration, true,
              ));
            }
          } catch {
            // A failed readback cannot confirm persistence.
          }
        }
        if (!mounted.current) return;
        if (saved && profile && currentProfile &&
            currentProfile.profile_revision > profile.profile_revision &&
            currentProfile.profile_key === change.profileKey &&
            (!change.theme || currentProfile.theme === change.theme)) {
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
      if (mounted.current) { setBusy(false); setSavingConfiguration(false); }
    }
  }

  return (
    <Modal open title={`${selection.action === 'add' ? 'Add' : 'Configure'} ${selection.name}`}
      titleIcon={configuringWebChat ? <MessageCircle size={19} aria-hidden /> : undefined}
      headerActions={configuringWebChat && channel ? <ManagedChannelStatus channel={channel} /> : undefined}
      onClose={() => { if (!submitting.current) onClose(); }}>
      <Modal.Body>
        <div className="flex flex-col gap-3">
          {!channel && <ManagedChannelLoadNotice query={query} />}
          {!channel && query.isSuccess && query.fetchStatus === 'idle' && (
            <Text as="p" variant="body-sm" role="status">This setup is no longer available. Close to see the current connector list.</Text>
          )}
          {channel && (
            <>
              {configuringWebChat && !confirmingRemove ? (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Text as="p" variant="body-sm" tone="muted" className="min-w-0 flex-1">{channel.definition.description}</Text>
                </div>
              ) : <>
                <Text as="p">{channel.definition.description}</Text>
                <Text as="p" variant="body-sm" tone="muted">
                  Connection scope: Workspace. This channel is configured for the selected workspace.
                </Text>
                <div><ManagedChannelStatus channel={channel} /></div>
              </>}
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
                    {assignedPublication ? 'Your profile is saved. Ask the hosting operator to approve the current installation generation and verify its visitor host, then reopen Configure.' : 'Web Chat is added but not live. Save an eligible published public profile to continue setup.'}
                  </Text>
                  {profile && (
                    <Text as="p" variant="body-sm">
                      {assignedProfile ? `Saved profile: ${assignedProfile.name}` :
                        profile.profile_key ? 'The saved profile is no longer eligible.' : 'No profile saved yet.'}
                    </Text>
                  )}
                  {profile?.editable && profile.eligible_profiles.length === 0 && (
                    <Text as="p" variant="body-sm" tone="muted">
                      In Agent Profiles, publish an eligible public profile with a service identity.
                      Setting up the identity does not enable Web Chat or grant access to workspace data.
                    </Text>
                  )}
                </div>
              ) : configuringWebChat ? null : (
                <Text as="p" variant="body-sm" tone="muted">
                  {!channel.connected
                    ? channel.definition.visitor_surface === true
                      ? 'Adding saves Web Chat to this workspace for setup. It does not publish a chat or turn on messaging.'
                      : 'Adding installs this channel for the workspace. It does not turn on messaging.'
                    : channel.state === 'unavailable' || (channel.state === 'inactive' && !channel.can_enable)
                      ? 'This channel cannot be enabled right now.'
                      : 'Manage whether this channel can receive messages for the workspace.'}
                </Text>
              )}
            {channel.connected && !configuringWebChat && generation && !confirmingRemove && (channel.can_enable || channel.state === 'active' || channel.state === 'unavailable') && (
              <Button type="button" size="sm" variant={channel.can_enable ? 'primary' : 'secondary'} disabled={busy || (channel.can_enable && !canEnable)}
                onClick={() => void submit({ kind: 'enabled', enabled: channel.can_enable, generation })}>
                {channel.can_enable ? 'Enable channel' : 'Disable channel'}
              </Button>
            )}
              {hasProfileAssignment && !confirmingRemove && (
                <Surface tone={configuringWebChat ? "panel-2" : "panel"} border="subtle" radius="card" padding="md">
                  {configuringWebChat && <Text as="h3" variant="heading-sm" weight="semibold" className="mb-3">Agent setup</Text>}
                  {profile ? (
                    <ManagedChannelProfileAssignment
                      key={JSON.stringify([generationKey, profile.profile_revision])}
                      configuration={profile}
                      compact={Boolean(configuringWebChat)}
                      active={channel.state === 'active'}
                      value={configuringWebChat ? chosenProfile : undefined}
                      onDraft={configuringWebChat ? value => {
                        setDraft({key: draftKey, value}); setProfileSaved(false); setError(null);
                      } : undefined}
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
                  {configuringWebChat && dirty && <Text as="p" role="status" variant="body-sm" tone="muted" className="mt-2">Save configuration to test the selected profile.</Text>}
                  {!dirty && privateTestOwner && profile && <div className="mt-3">
                    <ManagedWebChatPrivateTest owner={privateTestOwner} profileRevision={profile.profile_revision} modelId={profile.private_test_model} />
                  </div>}
                  {configuringWebChat && !dirty && !privateTestOwner && <Text as="p" variant="body-sm" tone="muted" className="mt-2">
                    {assignedPublication ? 'Private test requires an approved test model and capacity for this installation.'
                      : 'Save an eligible published agent profile with a dedicated service identity to test privately.'}
                  </Text>}
                </Surface>
              )}
              {configuringWebChat && !confirmingRemove && (
                <SharingGroup title="Appearance" description="Choose the visitor chat theme." icon={Palette} available mountOnOpen>
                <WebChatAppearance value={selectedTheme} disabled={!profile || busy}
                  onChange={theme => {
                    setAppearanceDraft({ key: draftKey, theme });
                    setProfileSaved(false);
                    setError(null);
                  }} />
                </SharingGroup>
              )}
              {configuringWebChat && !confirmingRemove && (
                <section aria-label="Sharing and publishing">
                  <Text as="h3" variant="heading-sm" weight="semibold" className="mb-1">Sharing and publishing</Text>
                  {channel.state !== 'active' && <Text as="p" variant="body-sm" tone="muted">Publishing requires an enabled channel with a saved profile and approved hosting.</Text>}
                  <div className="divide-y divide-[var(--border-subtle)]">
                  <SharingGroup icon={Braces} description="Connect your own chat interface through a server." title="Developer API" available={Boolean(currentManagerScope && profile?.client_api_origin)}>
                  {currentManagerScope && profile?.client_api_origin
                    ? <ClientEndpoint key={JSON.stringify([scopeKey, generationKey, currentPublication, profile.client_api_origin])}
                        origin={profile.client_api_origin} />
                    : <Text as="p" variant="body-sm" tone="muted">A copyable endpoint requires an enabled installation and a separately approved HTTPS client API host for this publication.</Text>}
                  <div>
                    <Text as="h4" variant="body-sm" weight="medium">Developer specification</Text>
                    <Text as="p" variant="body-sm">Call from your application's server. Exchange a scoped grant using POST /api/v1/web-chat/sessions with bearer authentication and an empty body. Keep the grant and returned session_token on the server.</Text>
                    <Text as="p" variant="body-sm">With the session bearer, GET /api/v1/web-chat/session reads history. POST /api/v1/web-chat/session/messages accepts JSON with id (1–128 letters, digits, underscores or hyphens) and text (up to 1,000 characters). Reuse the same id and text to retry a pending turn. Handle 202 as pending, 409 as conflicting reuse and 401 as expired or revoked access.</Text>
                  </div>
                  </SharingGroup>
                  <SharingGroup icon={Code2} description="Add a popup launcher to an approved website." title="Website embed" available={Boolean(currentEmbedPolicies)}>
                    {!currentEmbedPolicies && <Text as="p" variant="body-sm" tone="muted">Approve the website’s exact HTTPS origin and a distinct embed host for the enabled publication to get its launcher code.</Text>}
              {currentManagerScope && currentPublication && currentEmbedPolicies && profile && channel.connected && (
                <ManagedChannelApprovedEmbedSharing scope={currentManagerScope} channel={channel}
                  publication={currentPublication} profileRevision={profile.profile_revision} policies={currentEmbedPolicies} />
              )}
                  </SharingGroup>
                  <SharingGroup mountOnOpen icon={ExternalLink} description="Open or share a standalone visitor page." title="Full-page chat" available={Boolean(currentShareSource)}>
                    {!currentShareSource && <Text as="p" variant="body-sm" tone="muted">Enable Web Chat with a verified visitor host and an issued full-page link for the saved publication.</Text>}
              {currentShareSource && currentPublication && profile && channel.connected && (
                <ManagedChannelShareControl readOnOpen channel={channel} source={currentShareSource}
                  profileRevision={profile.profile_revision} publication={currentPublication} />
              )}
                  </SharingGroup>
                  </div>
                </section>
              )}
              {!configuringWebChat && currentShareSource && currentPublication && profile && channel.connected && (
                <ManagedChannelShareControl channel={channel} source={currentShareSource}
                  profileRevision={profile.profile_revision} publication={currentPublication} />
              )}
              {confirmingRemove && (
                <Text as="p" variant="body-sm">Remove this channel from the workspace? It will no longer receive messages here.</Text>
              )}
            </>
          )}
          {profileSaved && <Text as="p" variant="body-sm" role="status">{configuringWebChat ? 'Configuration saved.' : 'Profile assignment saved.'}</Text>}
          {error && <Text as="p" variant="body-sm" tone="danger" role="alert">{error}</Text>}
        </div>
      </Modal.Body>
      <Modal.Footer>
        {channel?.action === 'add' && (
          <Button type="button" variant="primary" size="sm" disabled={busy || !channel.can_install}
            onClick={() => void submit({ kind: 'add' })}>Add channel</Button>
        )}
        {channel?.action === 'configure' && generation && (!configuringWebChat || confirmingRemove) && (
          <>
            <Button type="button" size="sm" variant={configuringWebChat && !confirmingRemove ? "ghost" : "danger"} disabled={busy}
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
        {configuringWebChat && !confirmingRemove && <Button type="button" variant="primary" size="sm"
          disabled={!canSaveConfiguration} aria-busy={savingConfiguration}
          onClick={() => void submit(dirty
            ? { kind: 'profile', profileKey: chosenProfile || null, theme: appearanceDirty ? selectedTheme : undefined }
            : { kind: 'appearance', theme: selectedTheme })}>
          {savingConfiguration ? 'Saving…' : 'Save configuration'}
        </Button>}
      </Modal.Footer>
    </Modal>
  );
}

function SharingGroup({ title, description, icon: Icon, available, mountOnOpen, children }: {
  title: string; description: string; icon: LucideIcon; available: boolean; mountOnOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return <details className="group" onToggle={mountOnOpen ? event => setOpen(event.currentTarget.open) : undefined}>
    <summary className="flex cursor-pointer list-none items-center gap-3 rounded-[var(--radius-input)] px-2 py-3 managed-channel-summary focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)] [&::-webkit-details-marker]:hidden">
      <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-input)] managed-channel-summary-icon"><Icon size={17} /></span>
      <span className="min-w-0 flex-1">
        <Text as="span" variant="body-sm" weight="semibold" className="block">{title}</Text>
        <Text as="span" variant="body-sm" tone="muted" className="block">{description}</Text>
      </span>
      {!available && <Text as="span" variant="meta" tone="muted">Setup required</Text>}
      <ChevronDown size={15} aria-hidden className="shrink-0 managed-channel-summary-chevron group-open:rotate-180" />
    </summary>
    <div className="flex min-w-0 flex-col items-stretch gap-3 px-2 pb-4 sm:pl-14">{(!mountOnOpen || open) && children}</div>
  </details>;
}

function ClientEndpoint({ origin }: { origin: string }) {
  const url = origin + '/api/v1/web-chat/sessions';
  const [message, setMessage] = useState('');
  return <>
    <Text as="p" variant="body-sm">Start a session for this publication using an authorized server-side grant.</Text>
    <Textarea aria-label="Client session endpoint" value={url} readOnly rows={1} />
    <Button type="button" variant="secondary" size="sm" onClick={async () => {
      try {
        await navigator.clipboard.writeText(url);
        setMessage('Endpoint copied.');
      } catch {
        setMessage('Select the endpoint above to copy it.');
      }
    }}>Copy endpoint</Button>
    {message && <Text as="p" variant="body-sm" role="status">{message}</Text>}
  </>;
}
