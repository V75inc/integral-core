import { useLayoutEffect, useRef, useState } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';

import { managedChannelsApi, type ManagedChannel, type ManagedChannelChange } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { Text } from '../../../ui';
import { StatusPill } from '../components/Field';
import { currentManagedChannels } from './managedChannelQueries';

export function ManagedChannelStatus({ channel }: { channel: ManagedChannel }) {
  return (
    <StatusPill state={channel.state === 'active' ? 'ok' : channel.state === 'unavailable' ? 'warn' : 'idle'}>
      {channel.state === 'active' ? 'Active' : channel.state === 'unavailable' ? 'Unavailable' :
        channel.state === 'inactive' ? 'Inactive' : 'Not installed'}
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

export function ManagedChannelSettings({ selection, query, workspaceId, onClose, onInstalled, onAbandoned }: {
  selection: { slug: string; name: string; action: ManagedChannel['action'] };
  query: UseQueryResult<ManagedChannel[]>;
  workspaceId: string;
  onClose: () => void;
  onInstalled: () => void;
  onAbandoned: () => void;
}) {
  const submitting = useRef(false);
  const mounted = useRef(true);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Keep a deliberate remove confirmation bound to the observed generation.
  const [removeGeneration, setRemoveGeneration] = useState<string | null>(null);
  const channel = currentManagedChannels(query).find(row =>
    row.definition.slug === selection.slug && row.action === selection.action);
  const generation = channel?.connected ? { provision_id: channel.provision_id, route_epoch: channel.route_epoch } : null;
  const generationKey = generation ? JSON.stringify(generation) : null;
  const confirmingRemove = removeGeneration !== null && removeGeneration === generationKey;

  async function submit(change: ManagedChannelChange) {
    if (!channel || submitting.current || !mounted.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    setRemoveGeneration(null);
    let saved = false;
    try {
      try {
        await managedChannelsApi.change(workspaceId, selection.slug, change);
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
              <Text as="p" variant="body-sm" tone="muted">
                {!channel.connected
                  ? 'Adding installs this channel for the workspace. It does not turn on messaging.'
                  : channel.state === 'unavailable' || (channel.state === 'inactive' && !channel.can_enable)
                    ? 'This channel cannot be enabled right now.'
                    : 'Manage whether this channel can receive messages for the workspace.'}
              </Text>
              {confirmingRemove && (
                <Text as="p" variant="body-sm">Remove this channel from the workspace? It will no longer receive messages here.</Text>
              )}
            </>
          )}
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
              <Button type="button" size="sm" variant={channel.can_enable ? 'primary' : 'secondary'} disabled={busy}
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
