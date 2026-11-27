import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getWhatsAppChannelBinding,
  saveWhatsAppVoiceService,
  setWhatsAppCalling,
  type WhatsAppChannelBinding,
} from '../../../api/agentive';
import { managedChannelsApi, type ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Field } from '../../../patterns';
import { Input, Text } from '../../../ui';
import { StatusPill, ToggleRow } from '../components/Field';
import { ManagedChannelProfileAssignment } from './ManagedChannelProfileAssignment';

function message(error: unknown, fallback: string): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const text = (error as { response?: { data?: { message?: unknown } } }).response?.data?.message;
    if (typeof text === 'string' && text.trim()) return text;
  }
  return fallback;
}

function connectedWhatsApp(rows: ManagedChannel[] | undefined) {
  const channel = rows?.find(row => row.definition.slug === 'whatsapp' && row.connected);
  return channel?.connected ? channel : null;
}

export function WhatsAppLineSetup({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const line = useQuery({
    queryKey: ['connectors', 'whatsapp-line', workspaceId],
    queryFn: getWhatsAppChannelBinding,
  });
  const channels = useQuery({
    queryKey: ['connectors', 'whatsapp-managed', workspaceId],
    queryFn: ({ signal }) => managedChannelsApi.list(workspaceId, signal),
    retry: false,
  });
  const channel = connectedWhatsApp(channels.data);
  const generation = channel
    ? { provision_id: channel.provision_id, route_epoch: channel.route_epoch }
    : null;
  const profile = useQuery({
    queryKey: ['connectors', 'whatsapp-profile', workspaceId, generation?.provision_id, generation?.route_epoch],
    queryFn: ({ signal }) => managedChannelsApi.profileConfiguration(
      workspaceId, 'whatsapp', generation!, signal, false,
    ),
    enabled: Boolean(generation && channel?.configuration_kind === 'published-public-profile-v1'),
    retry: false,
  });
  const binding = line.data?.binding ?? null;
  const [voiceUrl, setVoiceUrl] = useState('');
  const [voiceKey, setVoiceKey] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    setVoiceUrl(binding?.jvvoice_base_url ?? '');
    setVoiceKey('');
  }, [binding?.jvvoice_base_url, binding?.id]);

  const refresh = async () => {
    await Promise.all([line.refetch(), channels.refetch(), profile.refetch()]);
  };
  const voice = useMutation({
    mutationFn: () => saveWhatsAppVoiceService({
      jvvoice_base_url: voiceUrl.trim(), api_key: voiceKey,
    }),
    onSuccess: async (saved) => {
      setVoiceKey('');
      setError('');
      setNotice('Voice service saved.');
      qc.setQueryData(['connectors', 'whatsapp-line', workspaceId], saved);
      await refresh();
    },
    onError: (err: unknown) => setError(message(err, 'Could not save the voice service.')),
  });
  const calling = useMutation({
    mutationFn: (enabled: boolean) => {
      if (!binding?.calling_grant_id) throw new Error('Calling is not available on this line.');
      return setWhatsAppCalling({
        calling_enabled: enabled,
        expected_grant_id: binding.calling_grant_id,
        expected_revision: binding.calling_revision ?? 0,
      });
    },
    onSuccess: async () => {
      setError('');
      await refresh();
    },
    onError: async (err: unknown) => {
      setError(message(err, 'Could not update voice calling.'));
      await line.refetch();
    },
  });
  const lineEnabled = useMutation({
    mutationFn: (enabled: boolean) => {
      if (!generation) throw new Error('The line is not ready to change.');
      return managedChannelsApi.change(workspaceId, 'whatsapp', {
        kind: 'enabled', enabled, generation,
      });
    },
    onSuccess: async (_result, enabled) => {
      setError('');
      setNotice(enabled ? 'Line enabled.' : 'Line disabled. You can choose a profile now.');
      await refresh();
    },
    onError: () => setError('Could not change the line. Refresh and try again.'),
  });
  const assign = useMutation({
    mutationFn: (profileKey: string | null) => {
      if (!profile.data || !generation) throw new Error('Profile assignment is not available.');
      return managedChannelsApi.assignProfile(
        workspaceId, 'whatsapp', generation, profile.data.profile_revision, profileKey,
      );
    },
    onSuccess: async () => {
      setError('');
      setNotice('Public profile saved.');
      await refresh();
    },
    onError: () => setError('Could not save the profile. The line must be disabled first.'),
  });

  const busy = voice.isPending || calling.isPending || lineEnabled.isPending || assign.isPending;

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-3">
      <Calls
        binding={binding}
        callingOn={Boolean(binding?.calling_enabled)}
        busy={busy}
        voiceUrl={voiceUrl}
        voiceKey={voiceKey}
        onVoiceUrl={setVoiceUrl}
        onVoiceKey={setVoiceKey}
        onCalling={enabled => calling.mutate(enabled)}
        onSaveVoice={() => { setNotice(''); voice.mutate(); }}
        loading={line.isPending}
        failed={line.isError}
        onRetry={() => void line.refetch()}
      />
      <PublicProfile
        channel={channel}
        loading={channels.isPending || profile.isFetching}
        failed={channels.isError || profile.isError}
        locked={profile.data ? !profile.data.editable : false}
        configuration={profile.data}
        busy={busy}
        onDisable={() => { setNotice(''); lineEnabled.mutate(false); }}
        onEnable={() => { setNotice(''); lineEnabled.mutate(true); }}
        canEnable={Boolean(channel?.connected && channel.can_enable)}
        onSave={key => { setNotice(''); assign.mutate(key); }}
        onRetry={() => void refresh()}
      />
      {notice ? <Text as="p" variant="body-sm" role="status">{notice}</Text> : null}
      {error ? <Text as="p" variant="body-sm" tone="danger" role="alert">{error}</Text> : null}
    </div>
  );
}

function Calls({
  binding, callingOn, busy, voiceUrl, voiceKey, onVoiceUrl, onVoiceKey, onCalling, onSaveVoice,
  loading, failed, onRetry,
}: {
  binding: WhatsAppChannelBinding | null;
  callingOn: boolean;
  busy: boolean;
  voiceUrl: string;
  voiceKey: string;
  onVoiceUrl: (value: string) => void;
  onVoiceKey: (value: string) => void;
  onCalling: (enabled: boolean) => void;
  onSaveVoice: () => void;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
}) {
  return (
    <section className="flex flex-col gap-2" aria-label="Voice service">
      <Text as="h3" variant="heading-sm">Voice service</Text>
      <Text as="p" variant="body-sm" tone="muted">
        Calls go through jvvoice. LiveKit stays on that host. A saved key is not shown again.
      </Text>
      {loading ? <Text as="p" variant="body-sm" role="status">Loading voice service…</Text> : null}
      {failed ? (
        <div className="flex items-center gap-2">
          <Text as="p" variant="body-sm" tone="danger" role="alert">Could not load the voice service.</Text>
          <Button type="button" size="sm" variant="ghost" onClick={onRetry}>Retry</Button>
        </div>
      ) : null}
      {binding ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {binding.display_phone ? <Text as="p" variant="body-sm">{binding.display_phone}</Text> : null}
            {binding.jvconnect_calling_status === 'ENABLED' ? <StatusPill state="ok">JV Connect calling on</StatusPill> : null}
            {binding.jvconnect_calling_status === 'DISABLED' ? <StatusPill state="warn">JV Connect calling off</StatusPill> : null}
            {binding.jvvoice_api_key_last4 ? (
              <Text as="p" variant="body-sm" tone="muted">jvvoice key ending {binding.jvvoice_api_key_last4}</Text>
            ) : null}
          </div>
          {binding.jvconnect_calling_error ? (
            <Text as="p" variant="body-sm" tone="danger">{binding.jvconnect_calling_error}</Text>
          ) : null}
          <ToggleRow
            label="Voice calling"
            hint="Text keeps working when calling is off. This also updates JV Connect."
            checked={callingOn}
            disabled={busy || !binding.calling_grant_id}
            onChange={onCalling}
          />
          <Field label="jvvoice base URL" hint="The public address of this workspace’s jvvoice service.">
            <Input
              type="url"
              value={voiceUrl}
              autoComplete="off"
              disabled={busy}
              placeholder="https://jvvoice.example"
              onChange={event => onVoiceUrl(event.target.value)}
            />
          </Field>
          <Field label="jvvoice API key" hint="Leave blank to keep the saved key.">
            <Input
              type="password"
              value={voiceKey}
              autoComplete="new-password"
              disabled={busy}
              placeholder={binding.jvvoice_api_key_last4
                ? `Saved key ending ${binding.jvvoice_api_key_last4}`
                : 'jvvoice API key'}
              onChange={event => onVoiceKey(event.target.value)}
            />
          </Field>
          <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onSaveVoice}>
            Save voice service
          </Button>
        </>
      ) : null}
    </section>
  );
}

function PublicProfile({
  channel, loading, failed, locked, configuration, busy, onDisable, onEnable, canEnable, onSave, onRetry,
}: {
  channel: ReturnType<typeof connectedWhatsApp>;
  loading: boolean;
  failed: boolean;
  locked: boolean;
  configuration: Awaited<ReturnType<typeof managedChannelsApi.profileConfiguration>> | undefined;
  busy: boolean;
  onDisable: () => void;
  onEnable: () => void;
  canEnable: boolean;
  onSave: (key: string | null) => void;
  onRetry: () => void;
}) {
  return (
    <section className="flex flex-col gap-2" aria-label="Public caller profile">
      <Text as="h3" variant="heading-sm">Public caller profile</Text>
      <Text as="p" variant="body-sm" tone="muted">
        Unknown callers hear this published profile. It has no tools and cannot change records.
        Choose it while the line is disabled, then enable the line again.
      </Text>
      {loading ? <Text as="p" variant="body-sm" role="status">Loading public profile…</Text> : null}
      {failed ? (
        <div className="flex items-center gap-2">
          <Text as="p" variant="body-sm" tone="danger" role="alert">Could not load the public profile.</Text>
          <Button type="button" size="sm" variant="ghost" onClick={onRetry}>Retry</Button>
        </div>
      ) : null}
      {!loading && !failed && !channel ? (
        <Text as="p" variant="body-sm" tone="muted">This line does not have a public profile assignment yet.</Text>
      ) : null}
      {configuration ? (
        <>
          {locked ? (
            <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onDisable}>
              Disable line to change profile
            </Button>
          ) : null}
          <ManagedChannelProfileAssignment
            configuration={configuration}
            locked={locked || busy}
            onSave={onSave}
          />
          {canEnable ? (
            <Button type="button" size="sm" disabled={busy} onClick={onEnable}>Enable line</Button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
