import type { CatalogEntry, ConnectionMode } from '../../../api/connectors';
import type { ManagedChannel } from '../../../api/managedChannels';

export type ConnectorCapability = 'api' | 'mcp' | 'channel';
type Cardinality = 'one' | 'many' | 'one-per-name';

interface Definition {
  id: string;
  provider: string;
  name: string;
  description: string;
  scopes: Partial<Record<ConnectionMode, Cardinality>>;
  capabilities: readonly ConnectorCapability[];
  host_managed?: boolean;
}

export const WHATSAPP_CONNECTOR = {
  id: 'whatsapp_jvconnect',
  provider: 'JV Connect',
  name: 'WhatsApp (JV Connect)',
  description: 'Connect a workspace WhatsApp line for messaging with verified personal numbers.',
  scopes: { shared: 'one' },
  capabilities: ['channel'],
  host_managed: true,
  setup: 'whatsapp',
} satisfies Definition & { setup: 'whatsapp'; host_managed: true };

const CUSTOM_MCP_CONNECTOR = {
  id: 'custom_mcp',
  provider: 'Custom',
  name: 'Custom MCP Server',
  description: 'Connect your own MCP server over Streamable HTTP.',
  scopes: { per_user: 'many', shared: 'one-per-name' },
  capabilities: ['mcp'],
  host_managed: false,
  setup: 'custom-mcp',
} satisfies Definition & { setup: 'custom-mcp' };

export type InformationalConnector = Definition & {
  setup: 'information';
  status: 'unavailable' | 'managed-elsewhere';
  detail: string;
};

type CatalogPackage = Definition & { setup: 'package'; package: CatalogEntry };
type ManagedChannelDefinition = Definition & { setup: 'managed-channel'; channel: ManagedChannel };
type HostManagedDefinition = Definition & { setup: 'host-managed'; host_managed: true };
export type ConnectorDefinition =
  | typeof WHATSAPP_CONNECTOR
  | typeof CUSTOM_MCP_CONNECTOR
  | CatalogPackage
  | ManagedChannelDefinition
  | HostManagedDefinition
  | InformationalConnector;

export function connectorDefinitions(
  packages: CatalogEntry[],
  { includeHostManaged, hostManaged = [], customMcp, managedChannels = [], information = [] }: {
    includeHostManaged: boolean;
    hostManaged?: readonly { id: string; name: string; description: string }[];
    customMcp: boolean;
    managedChannels?: readonly ManagedChannel[];
    information?: readonly InformationalConnector[];
  },
): ConnectorDefinition[] {
  const entries: ConnectorDefinition[] = [];
  if (includeHostManaged) {
    for (const pkg of hostManaged) {
      entries.push({
        id: pkg.id,
        provider: pkg.name,
        name: pkg.name,
        description: pkg.description,
        scopes: { shared: 'one' },
        capabilities: ['channel'],
        host_managed: true,
        setup: 'host-managed',
      });
    }
  }
  if (customMcp) entries.push(CUSTOM_MCP_CONNECTOR);
  for (const entry of packages) {
    if (entry.hidden) continue;
    entries.push({
      id: entry.slug,
      provider: entry.display_name,
      name: entry.display_name,
      description: entry.description,
      scopes: { per_user: 'many', shared: 'one' },
      capabilities: [entry.kind === 'mcp' ? 'mcp' : 'api'],
      host_managed: entry.host_managed === true,
      setup: 'package',
      package: entry,
    });
  }
  for (const channel of managedChannels) {
    const d = channel.definition;
    entries.push({
      id: d.slug,
      provider: d.provider,
      name: d.display_name,
      description: d.description,
      scopes: { [d.install_scope ?? 'shared']: 'one' },
      capabilities: ['channel'],
      setup: 'managed-channel',
      channel,
    });
  }
  for (const notice of information) {
    if (!entries.some(entry => entry.id === notice.id)) entries.push(notice);
  }
  return entries;
}
