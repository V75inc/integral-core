import { useState } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { MessageCircle, RefreshCw, Terminal } from 'lucide-react';

import type { ConnectorBindingScope, ConnectorPackageCatalogue } from '../../../api/connectorPackages';
import type { CatalogListResponse } from '../../../api/connectors';
import type { ManagedChannel } from '../../../api/managedChannels';
import { Button } from '../../../components/ui/Button';
import { HoverTooltip } from '../../../components/ui/HoverTooltip';
import { IconButton, Surface, Text } from '../../../ui';
import { AsyncBoundary } from '../../../patterns';
import { StatusPill, TextInput } from '../components/Field';
import { ConnectorBrandIcon } from './ConnectorBrandIcon';
import { ConnectorInstallSheet } from './ConnectorInstallSheet';
import { ManagedChannelLoadNotice, ManagedChannelStatus } from './ManagedChannelSettings';
import { currentManagedChannels } from './managedChannelQueries';
import { CustomMcpMountModal } from './CustomMcpMountModal';
import { useScopeOptional } from '../../../context/ScopeContext';
import {
  connectorDefinitions,
  type ConnectorCapability,
  type ConnectorDefinition,
  type InformationalConnector,
} from './connectorDefinitions';

const FILTERS = { all: 'All', api: 'API', mcp: 'MCP', channel: 'Channels' } as const;
const CAPABILITY_LABELS = { api: 'API', mcp: 'MCP', channel: 'Channel' } as const;

export function ConnectorCatalogPanel({
  catalog,
  whatsappEnabled,
  whatsappStatus,
  hostManaged = [],
  information,
  onInstalled,
  managed,
  onManageChannel,
  packages,
  onOpenManifest,
  directoryAdmin, permittedBindings,
}: {
  catalog: UseQueryResult<CatalogListResponse>;
  whatsappEnabled: boolean;
  whatsappStatus: 'loading' | 'unavailable' | 'new' | 'existing';
  hostManaged?: readonly { id: string; name: string; description: string }[];
  information?: readonly InformationalConnector[];
  onInstalled: () => void;
  managed?: UseQueryResult<ManagedChannel[]>;
  onManageChannel?: (channel: ManagedChannel) => void;
  packages?: ConnectorPackageCatalogue;
  onOpenManifest?: (id: string) => void;
  permittedBindings?: Partial<Record<ConnectorBindingScope, string>>;
  directoryAdmin?: {
    rescan: () => void;
    pending: boolean;
    failed: boolean;
    rejected: ConnectorPackageCatalogue['rejected'];
    dismiss: () => void;
  };
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | ConnectorCapability>('all');
  const [selected, setSelected] = useState<ConnectorDefinition | null>(null);

  const scope = useScopeOptional();
  const role = scope?.activeWorkspace?.your_role;
  const canManage = !role || role === 'admin' || role === 'owner';
  const sharingAllowed = !scope?.isPersonal && canManage;
  const entries = connectorDefinitions(catalog.data?.entries ?? [], {
    includeHostManaged: whatsappEnabled && (canManage || whatsappStatus === 'existing'),
    hostManaged,
    customMcp: canManage,
    managedChannels: managed ? currentManagedChannels(managed) : [],
    information,
    manifests: packages?.packages,
  }).filter((entry: ConnectorDefinition) => {
    const manifest = packages?.packages.find(row => row.id === entry.id);
    if (permittedBindings && manifest?.binding_scopes && !manifest.binding_scopes.some(binding => permittedBindings[binding])) return false;
    if (filter !== 'all' && !entry.capabilities.some(capability => capability === filter)) return false;
    const needle = query.trim().toLowerCase();
    return [entry.name, entry.description, entry.id, entry.provider].some(
      value => value.toLowerCase().includes(needle),
    );
  });

  const installedIds = new Set(packages?.installations?.map(row => row.package_id));
  const catalogIcons = new Map(catalog.data?.entries.map(entry => [entry.slug, entry.icon]));
  const close = () => setSelected(null);
  const finish = () => { close(); onInstalled(); };

  return (
    <div className="flex flex-col gap-4">
      {(directoryAdmin?.failed || Boolean(directoryAdmin?.rejected.length)) && (
        <Surface border="subtle" radius="card" padding="sm" className="flex items-start justify-between gap-3">
          <div role={directoryAdmin?.failed ? 'alert' : 'status'}>
            {directoryAdmin?.failed ? (
              <Text as="p" variant="body-sm" tone="danger">Could not rescan connectors. Try again.</Text>
            ) : (
              <>
                <Text as="p" variant="body-sm" weight="semibold">Some packages could not be added</Text>
                <ul>
                  {directoryAdmin?.rejected.map((row, i) => (
                    <li key={`${row.id}:${i}`}><Text variant="body-sm" tone="muted">{row.id}: {row.reason}</Text></li>
                  ))}
                </ul>
                <Text as="p" variant="body-sm" tone="muted">Fix the package files and rescan again.</Text>
              </>
            )}
          </div>
          <Button variant="ghost" size="xs" onClick={directoryAdmin?.dismiss}>Dismiss</Button>
        </Surface>
      )}
      <form className="flex flex-col gap-2" onSubmit={e => e.preventDefault()}>
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1"><TextInput value={query} onChange={setQuery} placeholder="Search connectors…" /></div>
          {directoryAdmin && (
            <HoverTooltip label="Rescan connectors">
              <IconButton label="Rescan connectors" size="md" disabled={directoryAdmin.pending}
                aria-busy={directoryAdmin.pending} onClick={directoryAdmin.rescan}>
                <RefreshCw size={16} aria-hidden className={directoryAdmin.pending ? 'animate-spin' : undefined} />
              </IconButton>
            </HoverTooltip>
          )}
        </div>
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
            const hostManaged = entry.host_managed === true;
            const channel = entry.setup === 'managed-channel' ? entry.channel : null;
            const manifest = entry.setup === 'manifest' ? entry.manifest : null;
            const icon = entry.setup === 'package' ? entry.package.icon : catalogIcons.get(entry.id);
            const installed = manifest && (installedIds.has(entry.id) || manifest.grant?.present);
            const action = channel?.connected || installed ? 'Configure' : 'Add';
            const blocked = Boolean(channel && (!onManageChannel || (!channel.connected && !channel.can_install)));
            return (
              <Surface key={`${entry.setup}:${entry.id}`} as="li" tone="panel-2" border="subtle" radius="card"
                padding="md" className="flex flex-col gap-3">
                <div className="flex items-start gap-3">
                  {icon ? <ConnectorBrandIcon icon={icon} label={entry.name} /> :
                    (hostManaged || channel || manifest?.channel_mode || entry.setup === 'information') ? <MessageCircle size={24} className="m-1 shrink-0" aria-hidden /> :
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
                    {entry.setup === 'information' && (
                      <div className="mt-2 flex flex-col items-start gap-1">
                        <StatusPill state={entry.status === 'unavailable' ? 'warn' : 'idle'}>
                          {entry.status === 'unavailable' ? 'Unavailable' : 'Setup in Connectors'}
                        </StatusPill>
                        <Text variant="body-sm" tone="subtle" as="p">{entry.detail}</Text>
                      </div>
                    )}
                    {installed && <StatusPill state="idle">Installed</StatusPill>}
                  </div>
                </div>
                {entry.setup !== 'information' && !hostManaged && <div className="mt-auto flex justify-end">
                  <Button type="button" variant="primary" size="xs" onClick={() => manifest ? onOpenManifest?.(entry.id) : channel ? onManageChannel?.(channel) : setSelected(entry)}
                    disabled={blocked}
                    aria-label={entry.setup === 'package' ? undefined : `${action} ${entry.name}`}
                    data-testid={entry.setup === 'custom-mcp' ? 'catalog-add-custom-mcp-btn' : undefined}>
                    {action}
                  </Button>
                </div>}
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
    </div>
  );
}
