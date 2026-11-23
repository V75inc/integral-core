import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type Ref, type MutableRefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import {
  connectionProfilesApi as api,
  type Audience,
  type ProfileAppearance,
  type ProfileDraft,
  type ProfileSettings,
} from '../../../api/connectionProfiles';
import { WORKSPACE_ASSISTANT, type ChatAgentIdentity } from '../../../api/chatAssistantIdentity';
import { getSessionGeneration } from '../../../api/session';
import { useScopeOptional } from '../../../context/ScopeContext';
import { Modal, ViewTabs } from '../../../components/ui';
import { AgentAvatar } from '../../../components/ui/AgentAvatar';
import { Button } from '../../../components/ui/Button';
import { Input, Select, StatusBadge, Surface, Text, Textarea } from '../../../ui';
import { Field } from '../../../patterns';
import { AgentAvatarEditor } from '../components/AgentAvatarEditor';
import { SettingsPageHeader } from '../components/SettingsPageHeader';

type Profile = ProfileSettings['profiles'][string];
type Catalogue = ProfileSettings['catalogue'][Audience];
type EditorScreen = { kind: 'create' | 'base' } | { kind: 'edit'; key: string };
type Screen = { kind: 'index' } | EditorScreen;
type Step = 'identity' | 'capabilities' | 'publish';
type Action = 'save' | 'publish' | 'native-chat';
const EMPTY_DRAFT: ProfileDraft = { key: '', audience: 'staff', name: '', description: '', persona: '', tools: [], omissions: [] };

const STEPS: { id: Step; label: string }[] = [
  { id: 'identity', label: 'Identity' },
  { id: 'capabilities', label: 'Capabilities' },
  { id: 'publish', label: 'Publish' },
];

function canSelectConnection(
  connection: ProfileSettings['connections'][number], audience: Audience, bindings: ProfileSettings['bindings'],
) {
  return connection.audience === audience && connection.publish_assignable === true &&
    !connection.verification_path && !bindings[connection.id];
}

function audienceOf(draft: ProfileDraft): Audience {
  return draft.audience ?? (draft.key === 'public' ? 'public' : 'staff');
}

function draftSignature(draft: ProfileDraft) {
  return JSON.stringify([
    audienceOf(draft), draft.name, draft.description ?? '', draft.icon ?? 'integral', draft.avatar ?? null, draft.persona,
    [...draft.tools].sort(), [...draft.omissions].sort(),
  ]);
}

function hasUnpublishedChanges(profile: Profile) {
  return !profile.published || draftSignature(profile.draft) !== draftSignature(profile.published);
}

function profileStatus(profile: Profile) {
  if (!profile.published) return 'Draft';
  return hasUnpublishedChanges(profile) ? 'Unpublished changes' : 'Published';
}

function errorMessage(error: unknown, fallback: string) {
  const failure = error as { response?: { data?: { message?: string; detail?: unknown } } };
  const data = failure?.response?.data;
  return data?.message || (typeof data?.detail === 'string' ? data.detail : fallback);
}

export function AgentProfilesSection() {
  const workspace = useScopeOptional()?.activeWorkspace;
  if (!workspace) return <Text as="p">Select a workspace to configure agent profiles.</Text>;
  if (workspace.your_role !== 'owner' && workspace.your_role !== 'admin') {
    return <Text as="p">Only workspace admins and the owner can manage agent profiles.</Text>;
  }
  return <ProfileControl key={workspace.id} workspaceId={workspace.id} workspaceName={workspace.name} />;
}

function ProfileControl({ workspaceId, workspaceName }: { workspaceId: string; workspaceName: string }) {
  const cache = useQueryClient();
  const queryKey = ['connection-profiles', workspaceId];
  const query = useQuery({ queryKey, queryFn: () => api.get(workspaceId) });
  const [screen, setScreen] = useState<Screen>({ kind: 'index' });
  const heading = useRef<HTMLSpanElement>(null);
  const restoreFocus = useRef(false);
  const returnKey = useRef<string | null>(null);
  const returnRow = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (screen.kind !== 'index' || !restoreFocus.current) return;
    restoreFocus.current = false;
    (returnRow.current ?? heading.current)?.focus();
  }, [screen.kind]);
  const navigate = (next: EditorScreen) => {
    returnKey.current = next.kind === 'edit' ? next.key : next.kind;
    setScreen(next);
  };
  const back = () => {
    restoreFocus.current = true;
    setScreen({ kind: 'index' });
  };
  const refreshSettings = (next: ProfileSettings) => cache.setQueryData(queryKey, next);

  if (query.isPending) return <Text as="p" role="status">Loading agent profiles…</Text>;
  if (!query.data) return <div role="alert"><Text as="p">Agent profiles could not be loaded.</Text><Button onClick={() => void query.refetch()}>Retry</Button></div>;

  const settings = query.data;
  const base = settings.workspace_assistant ?? WORKSPACE_ASSISTANT;
  const profile = screen.kind === 'edit' ? settings.profiles[screen.key] : undefined;
  const title = screen.kind === 'index' ? 'Agent profiles'
    : screen.kind === 'create' ? 'New profile'
    : screen.kind === 'base' ? base.name
    : profile?.draft.name ?? 'Profile unavailable';

  const workspaceLabel = `Workspace: ${workspaceName}`;
  const editor = screen.kind === 'create' || (screen.kind === 'edit' && profile)
    ? <ProfileEditor title={title} workspaceLabel={workspaceLabel}
        workspaceId={workspaceId} settings={settings} profile={profile} onUpdate={refreshSettings} back={back}
        onCreated={key => setScreen({ kind: 'edit', key })} />
    : null;

  // Dialogs stay local to this Settings route. Keep the list mounted so
  // creating a profile can update it without losing the original opener.
  return <>
    <div className="flex min-w-0 flex-col gap-5">
      <SettingsPageHeader
        title={<span ref={heading} tabIndex={-1}>Agent profiles</span>}
        description={workspaceLabel}
        actions={<Button ref={returnKey.current === 'create' ? returnRow : undefined} size="sm" onClick={() => navigate({ kind: 'create' })}>New profile</Button>}
      />
      <Surface border="subtle" radius="card" padding="lg">
        <ProfileIndex settings={settings} base={base} navigate={navigate} returnKey={returnKey.current} returnRow={returnRow} />
      </Surface>
    </div>
    {screen.kind === 'base' && <WorkspaceAssistantEditor workspaceId={workspaceId} revision={settings.revision}
      identity={base} title={title} workspaceLabel={workspaceLabel} back={back} />}
    {editor}
    {screen.kind === 'edit' && !profile && <ProfileScreen title={title} workspaceLabel={workspaceLabel} back={back}>
      <Text as="p">This profile is no longer available in this workspace.</Text>
    </ProfileScreen>}
  </>;
}

function ProfileScreen({ title, workspaceLabel, back, busy, backRef, children }: {
  title: ReactNode; workspaceLabel: string; back: () => void; busy?: boolean;
  backRef?: MutableRefObject<HTMLElement | null>; children: ReactNode;
}) {
  const dialogHeading = useRef<HTMLSpanElement | null>(null);
  return <Modal open title={<span ref={element => {
    dialogHeading.current = element;
    if (backRef) backRef.current = element;
  }} tabIndex={-1}>{title}</span>}
    initialFocusRef={dialogHeading} onClose={() => { if (!busy) back(); }}>
    <Modal.Body>
      <Text as="p" variant="body-sm" tone="muted">{workspaceLabel}</Text>
      {children}
    </Modal.Body>
  </Modal>;
}

function ProfileIdentityFields({ appearance, name, description, disabled, onAvatarChange, onReadingAvatar, onNameChange, onDescriptionChange }: {
  appearance: { icon?: string; avatar?: string | null };
  name: string;
  description: string;
  disabled: boolean;
  onAvatarChange: (appearance: ProfileAppearance) => void;
  onReadingAvatar: (reading: boolean) => void;
  onNameChange: (name: string) => void;
  onDescriptionChange?: (description: string) => void;
}) {
  return <>
    <AgentAvatarEditor appearance={appearance} disabled={disabled} onChange={onAvatarChange} onReading={onReadingAvatar} />
    <Field label="Profile name" htmlFor="profile-name">
      <Input id="profile-name" maxLength={80} value={name} disabled={disabled} onChange={event => onNameChange(event.target.value)} />
    </Field>
    {onDescriptionChange
      ? <Field label="Brief description" htmlFor="profile-description">
        <Input id="profile-description" maxLength={120} value={description} disabled={disabled} onChange={event => onDescriptionChange(event.target.value)} />
      </Field>
      : <dl className="flex flex-col gap-1.5">
        <Text as="dt" variant="body" weight="medium">Brief description</Text>
        <Text as="dd" variant="body-sm" tone="muted">{description}</Text>
      </dl>}
  </>;
}

function ProfileActions({ message, children }: { message: string; children: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-end gap-2">
    {message && <Text as="p" variant="body-sm" role="status" className="mr-auto">{message}</Text>}
    {children}
  </div>;
}

function ProfilePersonaField({ persona, disabled, onChange }: {
  persona: string; disabled: boolean; onChange: (persona: string) => void;
}) {
  return <Field label="Persona" htmlFor="profile-persona" hint="Describe how the assistant should respond. Workspace permissions still apply.">
    <Textarea id="profile-persona" maxLength={3000} value={persona} disabled={disabled} onChange={event => onChange(event.target.value)} />
  </Field>;
}

function ProfileIndex({ settings, base, navigate, returnKey, returnRow }: {
  settings: ProfileSettings; base: ChatAgentIdentity; navigate: (screen: EditorScreen) => void;
  returnKey: string | null; returnRow: Ref<HTMLButtonElement>;
}) {
  const profiles = Object.entries(settings.profiles);
  return <ul aria-label="Agent profiles" className="grid gap-2 sm:grid-cols-2">
    <ProfileCard name={base.name} description={base.description} icon={base.icon} avatar={base.avatar}
      audience="Default" buttonRef={returnKey === 'base' ? returnRow : undefined} onOpen={() => navigate({ kind: 'base' })} />
    {profiles.map(([key, profile]) => <ProfileCard key={key} name={profile.draft.name} description={profile.draft.description}
      icon={profile.draft.icon} avatar={profile.draft.avatar} audience={audienceOf(profile.draft) === 'public' ? 'Public' : 'Specialist'} status={profileStatus(profile)}
      buttonRef={returnKey === key ? returnRow : undefined} onOpen={() => navigate({ kind: 'edit', key })} />)}
  </ul>;
}

function ProfileCard({ name, description, icon, avatar, audience, status, onOpen, buttonRef }: {
  name: string; description?: string; icon?: string; avatar?: string | null; audience: string; status?: string; onOpen: () => void; buttonRef?: Ref<HTMLButtonElement>;
}) {
  return <Surface as="li" tone="panel-2" border="subtle" radius="card" padding="md" className="flex flex-col gap-3">
    <div className="flex items-start gap-3">
      <AgentAvatar icon={icon} avatar={avatar} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Text as="h4" variant="body" weight="semibold" className="break-words">{name}</Text>
          <StatusBadge>{audience}</StatusBadge>
          {status && <StatusBadge tone={status === 'Published' ? 'success' : 'neutral'}>{status}</StatusBadge>}
        </div>
        {description && <Text as="p" variant="body-sm" tone="subtle" className="mt-1">{description}</Text>}
      </div>
    </div>
    <div className="mt-auto flex justify-end">
      <Button ref={buttonRef} type="button" aria-label={`Configure ${name}`} size="xs" variant="primary" onClick={onOpen}>Configure</Button>
    </div>
  </Surface>;
}

function ProfileEditor({ title, workspaceLabel, workspaceId, settings, profile, onUpdate, onCreated, back }: {
  title: ReactNode; workspaceLabel: string; workspaceId: string; settings: ProfileSettings; profile?: Profile;
  onUpdate: (next: ProfileSettings) => void; onCreated: (key: string) => void; back: () => void;
}) {
  const cache = useQueryClient();
  const [step, setStep] = useState<Step>('identity');
  const tabsId = useId();
  const backButton = useRef<HTMLElement | null>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const [edit, setEdit] = useState<{ draft: ProfileDraft; revision: number } | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [readingAvatar, setReadingAvatar] = useState(false);
  const [leave, setLeave] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [offerInChat, setOfferInChat] = useState(false);
  const [selectedConnections, setSelectedConnections] = useState<string[]>([]);
  const [serviceIdentityIntent, setServiceIdentityIntent] = useState<string | null>(null);
  const savedDraft = profile?.draft ?? EMPTY_DRAFT;
  const savedSignature = draftSignature(savedDraft);
  const draft = edit?.draft ?? savedDraft;
  const profileKey = draft.key;
  const dirty = Boolean(edit && draftSignature(draft) !== savedSignature);
  const audience = audienceOf(draft);
  const published = profile?.published;
  const publishedAudience = published ? audienceOf(published) : undefined;
  const audienceChanged = Boolean(publishedAudience && publishedAudience !== audience);
  const catalogue = settings.catalogue[audience];
  const hasCapability = (name: string) => Object.prototype.hasOwnProperty.call(catalogue, name);
  const removed = [...new Set([...draft.tools, ...draft.omissions])].filter(name => !hasCapability(name));
  const offeredInChat = settings.native_chat_profiles?.includes(profileKey);
  const hasBinding = Object.values(settings.bindings).includes(profileKey);
  const audienceConflict = audienceChanged && (hasBinding || (audience === 'public' && offeredInChat));
  const status = profile ? profileStatus(profile) : 'New draft';
  const canSave = dirty && !busy && !readingAvatar && !removed.length && Boolean(draft.name.trim());
  const eligibleConnections = settings.connections.filter(connection =>
    canSelectConnection(connection, audience, settings.bindings));
  const hasRouteSelection = offerInChat || selectedConnections.length > 0;
  const canUpdateChat = !dirty && !busy && !readingAvatar && !hasRouteSelection &&
    (Boolean(offeredInChat) || (publishedAudience === 'staff' && audience === 'staff'));
  const routeSelectionStale = (offerInChat && (audience !== 'staff' || offeredInChat)) ||
    selectedConnections.some(id => !eligibleConnections.some(connection => connection.id === id));
  const publicationChanges = Boolean(profile && hasUnpublishedChanges(profile));
  const currentServiceIdentity = publishedAudience === 'public' ? profile?.service_identity_ready : undefined;
  const canSetUpServiceIdentity = Boolean(profile && !dirty && audience === 'public' && draft.tools.length === 0 &&
    (!published || publishedAudience !== 'public' || typeof currentServiceIdentity === 'boolean') &&
    (currentServiceIdentity !== true || publicationChanges));
  const sessionGeneration = getSessionGeneration();
  const identityIntentKey = JSON.stringify([workspaceId, profileKey, sessionGeneration, settings.revision, published?.version, savedSignature]);
  const setUpServiceIdentity = canSetUpServiceIdentity && serviceIdentityIntent === identityIntentKey;
  const canPublish = Boolean(profile && !dirty && !readingAvatar && !removed.length && !audienceConflict && !routeSelectionStale &&
    (publicationChanges || hasRouteSelection || setUpServiceIdentity));

  useEffect(() => {
    if (leave) keepEditing.current?.focus();
  }, [leave]);

  function selectStep(next: Step) {
    setStep(next);
    if (next !== 'publish') setServiceIdentityIntent(null);
    setMessage('');
    setError('');
  }

  function goToStep(next: Step) {
    selectStep(next);
    document.getElementById(`${tabsId}-tab-${next}`)?.focus();
  }

  function removeUnavailableTasks() {
    change({ tools: draft.tools.filter(hasCapability), omissions: draft.omissions.filter(hasCapability) });
    setMessage('Unavailable tasks removed from the draft. Save to keep this change.');
    document.getElementById(`${tabsId}-tab-capabilities`)?.focus();
  }

  function clearRouteSelection() {
    setOfferInChat(false);
    setSelectedConnections([]);
  }

  function change(update: Partial<ProfileDraft>) {
    setServiceIdentityIntent(null);
    if (update.audience && update.audience !== audience) clearRouteSelection();
    setEdit(previous => {
      const current = previous && draftSignature(previous.draft) !== savedSignature ? previous : null;
      const next = { ...(current?.draft ?? savedDraft), ...update };
      return draftSignature(next) === savedSignature ? null : { draft: next, revision: current?.revision ?? settings.revision };
    });
    setLeave(false);
    setMessage('');
    setError('');
  }

  async function act(action: Action) {
    if (action === 'publish' && (!canPublish || busy)) return;
    const requestedIdentity = action === 'publish' && setUpServiceIdentity;
    if (requestedIdentity && getSessionGeneration() !== sessionGeneration) {
      setServiceIdentityIntent(null);
      setError('Your session changed. Reopen this profile and review the identity setup again.');
      return;
    }
    if (action === 'publish') setServiceIdentityIntent(null);
    setBusy(action);
    setMessage('');
    setError('');
    try {
      const revision = edit?.revision ?? settings.revision;
      const next = action === 'save' ? (profile ? await api.save(workspaceId, revision, draft) : await api.create(workspaceId, revision, draft))
        : action === 'native-chat' ? await api.setNativeChatProfiles(workspaceId, settings.revision,
          offeredInChat ? settings.native_chat_profiles.filter(key => key !== profileKey) : [...(settings.native_chat_profiles ?? []), profileKey])
        : await api.publish(workspaceId, settings.revision, profileKey, offerInChat, selectedConnections,
          ...(requestedIdentity ? [true] as const : []));
      if (action === 'publish' || action === 'native-chat') void cache.invalidateQueries({ queryKey: ['native-chat-profiles', workspaceId] });
      if (!profile && !next.created_profile) throw new Error('Missing profile key');
      onUpdate(next);
      if (!profile && next.created_profile) onCreated(next.created_profile);
      setEdit(null);
      if (action === 'publish') clearRouteSelection();
      setLeave(false);
      const publishedResult = next.profiles[profileKey];
      const verifiedIdentity = publishedResult?.published?.version === settings.revision + 1 &&
        audienceOf(publishedResult.published) === 'public' && publishedResult.service_identity_ready === true;
      setMessage({
        save: 'Draft saved.',
        publish: !requestedIdentity ? 'Profile published.' : verifiedIdentity
          ? 'Profile published. Service identity is ready with no workspace data access.'
          : 'Profile published. Service identity setup could not be verified.',
        'native-chat': 'Workspace chat availability updated.',
      }[action]);
    } catch (failure) {
      setError(errorMessage(failure, 'Could not update this profile. Your edits are still here.'));
      if (action === 'native-chat') void cache.invalidateQueries({ queryKey: ['connection-profiles', workspaceId] });
    } finally {
      setBusy(null);
    }
  }

  return <ProfileScreen title={title} workspaceLabel={workspaceLabel} busy={Boolean(busy) || readingAvatar} backRef={backButton}
    back={() => dirty || hasRouteSelection ? setLeave(true) : back()}>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge>Specialist Assistant</StatusBadge>
        <StatusBadge tone={dirty ? 'warn' : status === 'Published' ? 'success' : 'neutral'}>{dirty ? 'Unsaved changes' : status}</StatusBadge>
      </div>
      {leave && <div className="flex flex-wrap items-center gap-2" role="status">
        <Text as="span" variant="body-sm">Discard unsaved changes?</Text>
        <Button ref={keepEditing} variant="secondary" size="sm" onClick={() => { setLeave(false); backButton.current?.focus(); }}>Keep editing</Button>
        <Button variant="ghost" size="sm" onClick={back}>Discard and go back</Button>
      </div>}
      <ViewTabs<Step> idPrefix={tabsId} ariaLabel="Profile editor" size="sm" stretch
        value={step} onChange={selectStep}
        options={STEPS.map(item => ({ value: item.id, label: item.label, disabled: Boolean(busy) }))} />
      {removed.length > 0 && step !== 'capabilities' && <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Text as="p" variant="body-sm" tone="warn" className="min-w-[min(100%,20rem)] flex-1">Review unavailable tasks before saving or publishing this draft.</Text>
        <Button variant="secondary" size="sm" onClick={() => goToStep('capabilities')} disabled={Boolean(busy)}>Review unavailable tasks</Button>
      </div>}
      {STEPS.map(({ id }) => <div key={id} id={`${tabsId}-panel-${id}`} role="tabpanel"
        aria-labelledby={`${tabsId}-tab-${id}`} tabIndex={0} hidden={step !== id}>
        {step === id && <div className="flex flex-col gap-4">
          {step === 'identity' && <>
            <ProfileIdentityFields appearance={draft} name={draft.name} description={draft.description ?? ''} disabled={Boolean(busy)}
              onAvatarChange={change} onReadingAvatar={setReadingAvatar}
              onNameChange={name => change({ name })} onDescriptionChange={description => change({ description })} />
            <ProfilePersonaField persona={draft.persona} disabled={Boolean(busy)} onChange={persona => change({ persona })} />
            <div className="flex items-center gap-2">
              <button type="button" role="switch" aria-checked={audience === 'public'}
                aria-labelledby="profile-public-label" aria-describedby="profile-public-hint" disabled={Boolean(busy)}
                className="flex h-10 w-11 shrink-0 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)] disabled:opacity-50"
                onClick={() => change({ audience: audience === 'public' ? 'staff' : 'public' })}>
                <span aria-hidden="true" className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${audience === 'public' ? 'bg-[var(--brand-accent)]' : 'bg-[var(--badge-muted-bg)]'}`}>
                  <span className={`h-4 w-4 rounded-full bg-white transition-transform ${audience === 'public' ? 'translate-x-[18px]' : 'translate-x-[2px]'}`} />
                </span>
              </button>
              <div>
                <Text as="p" id="profile-public-label" variant="body-sm" weight="medium">Public</Text>
                <Text as="p" id="profile-public-hint" variant="meta" tone="muted">Sets the draft audience. Connections are managed separately.</Text>
              </div>
            </div>
          </>}
          {step === 'capabilities' && <>
            {removed.length > 0 && <UnavailableTasks keys={removed} draft={draft}
              knownCapabilities={settings.catalogue[audience === 'public' ? 'staff' : 'public']}
              busy={Boolean(busy)} onRemove={removeUnavailableTasks} />}
            <CapabilityGroups catalogue={catalogue} draft={draft} audience={audience} disabled={Boolean(busy)} change={change} />
          </>}
          {step === 'publish' && <>
            <div className="flex flex-col gap-1" aria-label="Publication status">
              <Text as="h4" weight="semibold">{published ? `Published version ${published.version} · ${publishedAudience === 'public' ? 'Public' : 'Workspace'}` : 'Not published'}</Text>
              {audienceConflict ? <Text as="p" variant="body-sm" tone="warn">Resolve the existing connection or workspace chat availability before changing the published audience.</Text>
                : removed.length ? <Text as="p" variant="body-sm" tone="warn">Remove unavailable tasks in Capabilities before publishing.</Text>
                : dirty || !profile ? <Text as="p" variant="body-sm" tone="muted">Save the draft before publishing.</Text> : null}
              {routeSelectionStale && <div className="flex flex-wrap items-center gap-2">
                <Text as="p" variant="body-sm" tone="warn">Channel availability changed. Clear your selection and review the current options.</Text>
                <Button size="sm" variant="secondary" onClick={clearRouteSelection}>Clear channel selection</Button>
              </div>}
            </div>
            {(audience === 'public' || publishedAudience === 'public') && (
              <section aria-label="Service identity" className="flex flex-col gap-2 border-t border-[var(--border-subtle)] pt-4">
                <Text as="h4" weight="semibold">Service identity</Text>
                <Text as="p" variant="body-sm" tone="muted">
                  {publishedAudience !== 'public'
                    ? 'No public version is published yet.'
                    : currentServiceIdentity === true
                      ? 'Service identity ready for the published version. No workspace data access.'
                      : currentServiceIdentity === false
                        ? 'No service identity is ready for the published version.'
                        : 'Service identity status is not verified for the published version.'}
                </Text>
                {audience === 'public' && draft.tools.length > 0 && (
                  <Text as="p" variant="body-sm" tone="muted">Remove tools and save the draft before setting up a service identity.</Text>
                )}
                {audience === 'public' && draft.tools.length === 0 && (!profile || dirty) && (
                  <Text as="p" variant="body-sm" tone="muted">Save the draft before choosing identity setup.</Text>
                )}
                {canSetUpServiceIdentity && <>
                  <label className="flex items-center gap-2 self-start">
                    <input type="checkbox" aria-describedby={`${tabsId}-service-identity-impact`}
                      checked={setUpServiceIdentity} disabled={Boolean(busy) || readingAvatar}
                      onChange={event => setServiceIdentityIntent(event.target.checked ? identityIntentKey : null)} />
                    <Text as="span" variant="body-sm" weight="medium">Set up a service identity when publishing</Text>
                  </label>
                  <Text id={`${tabsId}-service-identity-impact`} as="p" variant="body-sm" tone="muted">
                    This binds the publication to its own service identity for Web Chat. It does not grant workspace data access or make Web Chat live.
                  </Text>
                </>}
                {currentServiceIdentity === true && publicationChanges && audience === 'public' && (
                  <Text as="p" variant="body-sm" tone="muted">A new version does not inherit the current version’s identity readiness.</Text>
                )}
              </section>
            )}
            <ProfileAvailability draftAudience={audience} publishedAudience={publishedAudience} profileKey={profileKey}
              connections={settings.connections} bindings={settings.bindings} offeredInChat={Boolean(offeredInChat)}
              offerInChat={offerInChat} onToggleChat={() => {
                if (offeredInChat || publishedAudience === 'staff') void act('native-chat');
                else setOfferInChat(!offerInChat);
              }}
              selectedConnections={selectedConnections} setSelectedConnections={setSelectedConnections}
              disabled={Boolean(busy)} canUpdateChat={canUpdateChat} />
          </>}
        </div>}
      </div>)}
      <ProfileActions message={message}>
        <Button variant="secondary" disabled={!canSave} onClick={() => void act('save')}>{busy === 'save' ? 'Saving…' : 'Save draft'}</Button>
        {step === 'publish'
          ? <Button disabled={!canPublish || Boolean(busy)} onClick={() => void act('publish')}>{busy === 'publish' ? 'Publishing…' :
            setUpServiceIdentity ? 'Publish and set up identity' : 'Publish profile'}</Button>
          : <Button variant="primary" disabled={Boolean(busy)} onClick={() => goToStep(step === 'identity' ? 'capabilities' : 'publish')}>
            Next
            <ChevronRight size={16} aria-hidden="true" />
          </Button>}
      </ProfileActions>
      {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
  </ProfileScreen>;
}

function UnavailableTasks({ keys, draft, knownCapabilities, busy, onRemove }: {
  keys: string[]; draft: ProfileDraft; knownCapabilities: Catalogue; busy: boolean; onRemove: () => void;
}) {
  return <div className="flex flex-col gap-2 rounded-[var(--radius-card)] border border-[var(--border-subtle)] p-3">
    <Text as="h4" weight="semibold">Unavailable tasks in this draft</Text>
    <Text as="p" variant="body-sm">These saved tasks are no longer offered for this profile. Remove them from the draft, then save before publishing.</Text>
    <ul className="list-disc space-y-1 pl-5">
      {keys.map(key => {
        const allowed = draft.tools.includes(key);
        const blocked = draft.omissions.includes(key);
        return <li key={key}>
          <Text as="span" variant="body-sm" className="[overflow-wrap:anywhere]">{capabilityName(knownCapabilities[key]?.label ?? key)}</Text>
          <Text as="span" variant="meta" tone="muted"> · {allowed && blocked ? 'Marked allowed and blocked' : allowed ? 'Marked allowed' : 'Marked blocked'}</Text>
        </li>;
      })}
    </ul>
    <div><Button variant="secondary" size="sm" disabled={busy} onClick={onRemove}>Remove unavailable tasks from draft</Button></div>
    <Text as="p" variant="meta" tone="muted">This edits the draft only. Save draft to keep the change.</Text>
  </div>;
}

function Disclosure({ title, children }: { title: string; children: React.ReactNode }) {
  return <details className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] px-3 py-2">
    <summary className="cursor-pointer text-sm font-medium">{title}</summary>
    <div className="mt-3 flex flex-col gap-3">{children}</div>
  </details>;
}

function capabilityName(label?: string) {
  const name = label?.split(' · ').slice(-1)[0] ?? '';
  if (!name.includes('_')) return name;
  const plain = name.split('__').slice(-1)[0].replace(/_/g, ' ');
  return plain.charAt(0).toUpperCase() + plain.slice(1);
}

function ProfileAvailability({
  draftAudience, publishedAudience, profileKey, connections, bindings, offeredInChat,
  offerInChat, onToggleChat, selectedConnections, setSelectedConnections, disabled,
  canUpdateChat,
}: {
  draftAudience: Audience; publishedAudience?: Audience; profileKey: string;
  connections: ProfileSettings['connections']; bindings: ProfileSettings['bindings'];
  offeredInChat: boolean; offerInChat: boolean; onToggleChat: () => void;
  selectedConnections: string[]; setSelectedConnections: (value: string[]) => void;
  disabled: boolean; canUpdateChat: boolean;
}) {
  const shownConnections = connections.filter(connection =>
    !connection.verification_path &&
    (connection.audience === draftAudience || (profileKey && bindings[connection.id] === profileKey)));
  const missingAssignments = Object.entries(bindings).filter(([id, key]) =>
    key === profileKey && !shownConnections.some(connection => connection.id === id)).length;
  const showWorkspaceChat = draftAudience === 'staff' || offeredInChat;
  const chatStatus = offeredInChat ? 'Offered' : offerInChat ? 'On publish' : 'Not offered';

  return <section aria-label="Availability" className="flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-4">
    <div className="flex flex-col gap-1">
      <Text as="h4" weight="semibold">Availability</Text>
      <Text as="p" variant="body-sm" tone="muted">Publish uses the saved draft and adds only the channels you select. Existing assignments stay in place. You can publish without adding a channel.</Text>
      {publishedAudience && publishedAudience !== draftAudience &&
        <Text as="p" variant="meta" tone="muted">Active routes still use the published audience. New selections must match the draft audience.</Text>}
    </div>
    {showWorkspaceChat && <div className="flex flex-col gap-2 rounded-[var(--radius-card)] border border-[var(--border-subtle)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Text as="h5" variant="body-sm" weight="semibold">Workspace chat</Text>
        <StatusBadge tone={offeredInChat ? 'success' : 'neutral'}>{chatStatus}</StatusBadge>
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" role="switch" checked={offeredInChat || offerInChat}
          disabled={disabled || ((offeredInChat || publishedAudience === 'staff') && !canUpdateChat)}
          onChange={onToggleChat} />
        <Text as="span" variant="body-sm" weight="medium">Show in workspace chat</Text>
      </label>
      <Text as="p" variant="body-sm" tone="muted">
        {publishedAudience === 'staff' || offeredInChat
          ? 'Eligible workspace members can choose this profile in the assistant picker. Existing conversations keep their assigned profile.'
          : 'This setting takes effect when you publish a workspace profile.'}
      </Text>
      <a href="/agent" className="self-start text-sm underline">Open workspace chat</a>
    </div>}
    {(shownConnections.length > 0 || missingAssignments > 0) && <div className="flex flex-col gap-2 rounded-[var(--radius-card)] border border-[var(--border-subtle)] p-3">
      <Text as="h5" variant="body-sm" weight="semibold">External connections</Text>
      {shownConnections.length > 0 && <ul className="divide-y divide-[var(--border-subtle)]">
        {shownConnections.map(connection => {
          const assigned = bindings[connection.id];
          const selectable = canSelectConnection(connection, draftAudience, bindings);
          const reason = assigned === profileKey ? 'Existing assignment retained when you publish.'
            : assigned ? 'Already in use by another profile.'
            : !selectable ? 'This connection does not support assignment during publishing.' : null;
          return <li key={connection.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <div className="min-w-0 [overflow-wrap:anywhere]">
              {selectable ? <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={selectedConnections.includes(connection.id)} disabled={disabled}
                  onChange={event => setSelectedConnections(event.target.checked
                    ? [...selectedConnections, connection.id] : selectedConnections.filter(id => id !== connection.id))} />
                {connection.label}
              </label> : <Text as="p" variant="body-sm" weight="medium">{connection.label}</Text>}
              {reason && <Text as="p" variant="meta" tone="muted">{reason}</Text>}
            </div>
            <StatusBadge tone={assigned && assigned === profileKey ? 'success' : 'neutral'}>
              {assigned && assigned === profileKey ? 'Assigned to this profile'
                : assigned ? 'Assigned to another profile' : selectable && selectedConnections.includes(connection.id)
                  ? 'Selected for publishing' : selectable ? 'Eligible · Unassigned' : 'Unassigned'}
            </StatusBadge>
          </li>;
        })}
      </ul>}
      {missingAssignments > 0 && <Text as="p" variant="body-sm" tone="warn">
        {missingAssignments === 1 ? 'One stored assignment is unavailable here.' : `${missingAssignments} stored assignments are unavailable here.`}
      </Text>}
    </div>}
  </section>;
}

const SOURCES = [
  ['core', 'Integral built-ins'],
  ['app', 'Installed apps'],
  ['workspace', 'Custom / uploaded'],
] as const;

function CapabilityGroups({ catalogue, draft, audience, disabled, change }: {
  catalogue: Catalogue; draft: ProfileDraft; audience: Audience; disabled: boolean; change: (update: Partial<ProfileDraft>) => void;
}) {
  const entries = Object.entries(catalogue);
  const selected = entries.filter(([key]) => draft.tools.includes(key) && !draft.omissions.includes(key)).length;
  const blocked = entries.filter(([key]) => draft.omissions.includes(key)).length;

  return <>
    <div className="flex flex-wrap items-center gap-2">
      <Text as="h4" weight="semibold">Profile capabilities</Text>
      <StatusBadge>{entries.length} available</StatusBadge>
      <StatusBadge tone={selected ? 'success' : 'neutral'}>{selected} selected</StatusBadge>
      {blocked > 0 && <StatusBadge>{blocked} blocked</StatusBadge>}
    </div>
    <Text as="p" variant="body-sm" tone="muted">
      {audience === 'public' && !entries.length ? 'No capabilities are registered for this public audience.' : 'Allow eligible skills and tools for this profile. Each caller’s permissions still apply.'}
      {' '}<a className="underline" href="/settings#skills" target="_blank" rel="noreferrer">Browse Agent Skills</a>
    </Text>
    {SOURCES.map(([source, label]) => {
      const rows = entries.filter(([, capability]) => (capability.source ?? 'app') === source);
      return <Disclosure key={source} title={`${label} (${rows.length})`}>
        {source === 'workspace' && <Text as="p" variant="meta" tone="muted">Custom skills are opt-in.</Text>}
        {!rows.length ? <Text as="p" variant="body-sm" tone="muted">None available for this profile.</Text>
          : <ul className="divide-y divide-[var(--border-subtle)]">
            {rows.map(([key, capability]) => {
              const name = capabilityName(capability.label);
              const access = draft.omissions.includes(key) ? 'blocked' : draft.tools.includes(key) ? 'allowed' : 'unselected';
              return <li key={key} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                <div className="min-w-[min(100%,18rem)] flex-[2_1_0%] [overflow-wrap:anywhere]">
                  <Text as="p" variant="body-sm" weight="medium">{name}</Text>
                  <Text as="p" variant="meta" tone="muted">
                    {capability.app_name ? `${capability.app_name} · ` : ''}
                    {capability.kind === 'skill' ? 'Skill · ' : ''}
                    {capability.human_only ? 'Needs human confirmation · ' : ''}{capability.description}
                  </Text>
                </div>
                <Select aria-label={`Access for ${name}`} className="min-w-[min(100%,10rem)] flex-1" value={access} disabled={disabled}
                  onChange={event => change({
                    tools: [...draft.tools.filter(item => item !== key), ...(event.target.value === 'allowed' ? [key] : [])],
                    omissions: [...draft.omissions.filter(item => item !== key), ...(event.target.value === 'blocked' ? [key] : [])],
                  })}>
                  <option value="unselected">Not selected</option><option value="allowed">Allowed</option><option value="blocked">Blocked</option>
                </Select>
              </li>;
            })}
          </ul>}
      </Disclosure>;
    })}
  </>;
}

function WorkspaceAssistantEditor({ workspaceId, revision, identity, title, workspaceLabel, back }: {
  workspaceId: string; revision: number; identity: NonNullable<ProfileSettings['workspace_assistant']>; title: ReactNode;
  workspaceLabel: string; back: () => void;
}) {
  const cache = useQueryClient();
  const [edit, setEdit] = useState<{ name?: string; appearance?: ProfileAppearance; persona?: string; revision: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [readingAvatar, setReadingAvatar] = useState(false);
  const [leave, setLeave] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const backButton = useRef<HTMLElement | null>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const value = edit?.name ?? identity.name;
  const originalPersona = identity.persona ?? '';
  const persona = edit?.persona ?? originalPersona;
  const appearance = { icon: identity.icon, avatar: identity.avatar, ...edit?.appearance };
  const dirty = Boolean(edit) && (value.trim() !== identity.name ||
    appearance.icon !== identity.icon || (appearance.avatar ?? null) !== (identity.avatar ?? null) ||
    persona.trim() !== originalPersona);

  useEffect(() => {
    if (leave) keepEditing.current?.focus();
  }, [leave]);

  function change(update: { name?: string; appearance?: ProfileAppearance; persona?: string }) {
    setMessage('');
    setError('');
    setLeave(false);
    setEdit(previous => ({
      ...previous, ...update, appearance: { ...previous?.appearance, ...update.appearance },
      revision: dirty ? previous?.revision ?? revision : revision,
    }));
  }

  async function save() {
    if (!edit) return;
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const next = await api.renameWorkspaceAssistant(workspaceId, edit.revision, value.trim(), {
        ...edit.appearance, ...(edit.persona === undefined ? {} : { persona: persona.trim() }),
      });
      cache.setQueryData(['connection-profiles', workspaceId], next);
      void cache.invalidateQueries({ queryKey: ['native-chat-profiles', workspaceId] });
      setEdit(null);
      setLeave(false);
      setMessage('Workspace Assistant saved.');
    } catch (failure) {
      setError(errorMessage(failure, 'Could not save the Workspace Assistant. Your edits are still here.'));
    } finally {
      setBusy(false);
    }
  }

  return <ProfileScreen title={title} workspaceLabel={workspaceLabel} busy={busy || readingAvatar} backRef={backButton}
    back={() => dirty ? setLeave(true) : back()}>
    <div><StatusBadge>Default Workspace Assistant</StatusBadge></div>
    {leave && <div className="flex flex-wrap items-center gap-2" role="status">
      <Text as="span" variant="body-sm">Discard unsaved changes?</Text>
      <Button ref={keepEditing} variant="secondary" size="sm" onClick={() => { setLeave(false); backButton.current?.focus(); }}>Keep editing</Button>
      <Button variant="ghost" size="sm" onClick={back}>Discard and go back</Button>
    </div>}
    <div className="flex flex-col gap-4">
      <ProfileIdentityFields appearance={appearance} name={value} description={identity.description} disabled={busy}
        onAvatarChange={appearance => change({ appearance })} onReadingAvatar={setReadingAvatar} onNameChange={name => change({ name })} />
      <ProfilePersonaField persona={persona} disabled={busy} onChange={persona => change({ persona })} />
    </div>
    <ProfileActions message={message}>
      <Button disabled={busy || readingAvatar || !value.trim() || !dirty} onClick={save}>{busy ? 'Saving…' : 'Save assistant'}</Button>
    </ProfileActions>
    {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
  </ProfileScreen>;
}
