import { useState } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { MessageCircle, Terminal } from 'lucide-react';

import type { CatalogListResponse } from '../../../api/connectors';
import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { Surface, Text } from '../../../ui';
import { AsyncBoundary } from '../../../patterns';
import { StatusPill, TextInput } from '../components/Field';
import { ConnectorBrandIcon } from './ConnectorBrandIcon';
import { ConnectorInstallSheet } from './ConnectorInstallSheet';
import { ManagedChannelLoadNotice, ManagedChannelStatus } from './ManagedChannelSettings';
import { currentManagedChannels } from './managedChannelQueries';
import { CustomMcpMountModal } from './CustomMcpMountModal';
import { WhatsAppConnectorSettings } from './WhatsAppConnectorSettings';
import { useScopeOptional } from '../../../context/ScopeContext';
import {
  connectorDefinitions,
  type ConnectorCapability,
  type ConnectorDefinition,
} from './connectorDefinitions';

const FILTERS = { all: 'All', api: 'API', mcp: 'MCP', channel: 'Channels' } as const;
const CAPABILITY_LABELS = { api: 'API', mcp: 'MCP', channel: 'Channel' } as const;

export function ConnectorCatalogPanel({
  catalog,
  whatsappEnabled,
  whatsappStatus,
  onInstalled,
  managed,
  onManageChannel,
}: {
  catalog: UseQueryResult<CatalogListResponse>;
  whatsappEnabled: boolean;
  whatsappStatus: 'loading' | 'unavailable' | 'new' | 'existing';
  onInstalled: () => void;
  managed?: UseQueryResult<ManagedChannel[]>;
  onManageChannel?: (channel: ManagedChannel) => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | ConnectorCapability>('all');
  const [selected, setSelected] = useState<ConnectorDefinition | null>(null);

  const scope = useScopeOptional();
  const role = scope?.activeWorkspace?.your_role;
  const canManage = !role || role === 'admin' || role === 'owner';
  const sharingAllowed = !scope?.isPersonal && canManage;
  const entries = connectorDefinitions(catalog.data?.entries ?? [], {
    whatsapp: whatsappEnabled && (canManage || whatsappStatus === 'existing'),
    customMcp: canManage,
    managedChannels: managed ? currentManagedChannels(managed) : [],
  }).filter((entry: ConnectorDefinition) => {
    if (filter !== 'all' && !entry.capabilities.some(capability => capability === filter)) return false;
    const needle = query.trim().toLowerCase();
    return [entry.name, entry.description, entry.id, entry.provider].some(
      value => value.toLowerCase().includes(needle),
    );
  });

  const close = () => setSelected(null);
  const finish = () => { close(); onInstalled(); };

  return (
    <div className="flex flex-col gap-4">
      <form className="flex flex-col gap-2" onSubmit={e => e.preventDefault()}>
        <TextInput value={query} onChange={setQuery} placeholder="Search connectors…" />
        <div className="flex flex-wrap gap-1">
          {(Object.entries(FILTERS) as [keyof typeof FILTERS, string][]).map(([id, label]) => (
            <Button key={id} type="button" variant={filter === id ? 'secondary' : 'ghost'}
              size="xs" aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {label}
            </Button>
          ))}
        </div>
      </form>

      {managed && (filter === 'all' || filter === 'channel') && <ManagedChannelLoadNotice query={managed} />}
      <AsyncBoundary query={catalog} emptyFallback={null}>{() => null}</AsyncBoundary>
      {entries.length ? (
        <ul className="grid gap-2 sm:grid-cols-2">
          {entries.map(entry => {
            const isWhatsApp = entry.setup === 'whatsapp';
            const existing = isWhatsApp && whatsappStatus === 'existing';
            const channel = entry.setup === 'managed-channel' ? entry.channel : null;
            const action = channel?.connected ? 'Configure' : existing ? (canManage ? 'Configure' : 'View') : 'Add';
            const blocked = channel ? (!onManageChannel || (!channel.connected && !channel.can_install))
              : isWhatsApp && whatsappStatus !== 'new' && !existing;
            return (
              <Surface key={`${entry.setup}:${entry.id}`} as="li" tone="panel-2" border="subtle" radius="card"
                padding="md" className="flex flex-col gap-3">
                <div className="flex items-start gap-3">
                  {entry.setup === 'package' ? <ConnectorBrandIcon icon={entry.package.icon} label={entry.name} /> :
                    (isWhatsApp || channel) ? <MessageCircle size={24} className="m-1 shrink-0" aria-hidden /> :
                      <Terminal size={24} className="m-1 shrink-0" aria-hidden />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Text variant="body" weight="semibold" as="span">{entry.name}</Text>
                      {entry.capabilities.map(capability => (
                        <StatusPill key={capability} state="idle">{CAPABILITY_LABELS[capability]}</StatusPill>
                      ))}
                    </div>
                    <Text variant="body-sm" tone="subtle" as="p" className="mt-1">{entry.description}</Text>
                    {channel && (
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <ManagedChannelStatus channel={channel} />
                        {blocked && <Text variant="body-sm" tone="muted">Setup is not available right now.</Text>}
                      </div>
                    )}
                    {isWhatsApp && <Text variant="body-sm" tone="subtle" as="p" className="mt-1">
                      {existing ? 'Added to this workspace.' : whatsappStatus === 'new' ?
                        'One line per workspace.' : whatsappStatus === 'loading' ?
                          'Checking the workspace connection…' : 'Could not check the workspace connection.'}
                    </Text>}
                  </div>
                </div>
                <div className="mt-auto flex justify-end">
                  <Button type="button" variant="primary" size="xs" onClick={() => channel ? onManageChannel?.(channel) : setSelected(entry)}
                    disabled={blocked}
                    aria-label={entry.setup === 'package' ? undefined : `${action} ${entry.name}`}
                    data-testid={entry.setup === 'custom-mcp' ? 'catalog-add-custom-mcp-btn' : undefined}>
                    {action}
                  </Button>
                </div>
              </Surface>
            );
          })}
        </ul>
      ) : catalog.isSuccess && (!managed || filter === 'api' || filter === 'mcp' ||
          (managed.isSuccess && managed.fetchStatus === 'idle')) ? (
        <Text variant="body-sm" tone="subtle" as="p">No connectors match this filter.</Text>
      ) : null}

      {selected?.setup === 'package' && <ConnectorInstallSheet entry={selected.package}
        sharingAllowed={sharingAllowed} onClose={close} onInstalled={finish} />}
      {selected?.setup === 'custom-mcp' && <CustomMcpMountModal open sharingAllowed={sharingAllowed}
        onClose={close} onMounted={finish} />}
      {selected?.setup === 'whatsapp' && <WhatsAppConnectorSettings dialog={{
        mode: whatsappStatus === 'existing' ? (canManage ? 'Configure' : 'View') : 'Add',
        onClose: close,
      }} />}
    </div>
  );
}
