import type { CatalogEntry, ConnectionMode } from '../../../api/connectors';

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

type CatalogPackage = Definition & { setup: 'package'; package: CatalogEntry };
export type ConnectorDefinition =
  | typeof WHATSAPP_CONNECTOR
  | typeof CUSTOM_MCP_CONNECTOR
  | CatalogPackage;

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
  { whatsapp, customMcp }: { whatsapp: boolean; customMcp: boolean },
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
  return entries;
}
