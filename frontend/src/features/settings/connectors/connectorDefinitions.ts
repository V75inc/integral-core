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
}

export const WHATSAPP_CONNECTOR = {
  id: 'whatsapp_jvconnect',
  provider: 'JV Connect',
  name: 'WhatsApp (JV Connect)',
  description: 'Connect a workspace WhatsApp line for messaging with verified personal numbers.',
  scopes: { shared: 'one' },
  capabilities: ['channel'],
  setup: 'whatsapp',
} satisfies Definition & { setup: 'whatsapp' };

const CUSTOM_MCP_CONNECTOR = {
  id: 'custom_mcp',
  provider: 'Custom',
  name: 'Custom MCP Server',
  description: 'Connect your own MCP server over Streamable HTTP.',
  scopes: { per_user: 'many', shared: 'one-per-name' },
  capabilities: ['mcp'],
  setup: 'custom-mcp',
} satisfies Definition & { setup: 'custom-mcp' };

export type InformationalConnector = Definition & {
  setup: 'information';
  status: 'unavailable' | 'managed-elsewhere';
  detail: string;
};

export function webChatCatalogueInformation(ready: boolean): InformationalConnector {
  return {
    id: 'public_web_chat',
    provider: 'Integral',
    name: 'Web Chat',
    description: 'Offer a text chat powered by a published public agent profile.',
    scopes: { shared: 'one' },
    capabilities: ['channel'],
    setup: 'information',
    status: ready ? 'managed-elsewhere' : 'unavailable',
    detail: ready
      ? 'Manage setup from Settings → Connectors.'
      : 'This workspace has no ready Web Chat host. Ask your Integral administrator to check hosting.',
  };
}

type CatalogPackage = Definition & { setup: 'package'; package: CatalogEntry };
type ManagedChannelDefinition = Definition & { setup: 'managed-channel'; channel: ManagedChannel };
export type ConnectorDefinition =
  | typeof WHATSAPP_CONNECTOR
  | typeof CUSTOM_MCP_CONNECTOR
  | CatalogPackage
  | ManagedChannelDefinition
  | InformationalConnector;

const PACKAGE_PROVIDERS: Readonly<Record<string, string>> = {
  calendly: 'Calendly',
  cloudflare: 'Cloudflare',
  github_issues: 'GitHub',
  gmail: 'Google',
  google_gmail: 'Google',
  google_drive: 'Google',
  google_sheets: 'Google',
  notion: 'Notion',
  quickbooks: 'Intuit',
  quickbooks_mcp: 'Intuit',
  serper_web_search: 'Serper',
};

export function connectorDefinitions(
  packages: CatalogEntry[],
  { whatsapp, customMcp, managedChannels = [], information = [] }: {
    whatsapp: boolean;
    customMcp: boolean;
    managedChannels?: readonly ManagedChannel[];
    information?: readonly InformationalConnector[];
  },
): ConnectorDefinition[] {
  const entries: ConnectorDefinition[] = [];
  if (whatsapp) entries.push(WHATSAPP_CONNECTOR);
  if (customMcp) entries.push(CUSTOM_MCP_CONNECTOR);
  for (const entry of packages) {
    if (entry.hidden) continue;
    entries.push({
      id: entry.slug,
      provider: PACKAGE_PROVIDERS[entry.slug] ?? entry.display_name,
      name: entry.display_name,
      description: entry.description,
      scopes: { per_user: 'many', shared: 'one' },
      capabilities: [entry.kind === 'mcp' ? 'mcp' : 'api'],
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
