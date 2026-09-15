import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Store } from 'lucide-react';

import { connectorsApi, type CatalogEntry } from '../../../api/connectors';
import { Button } from '../../../components/ui/Button';
import { EmptyState } from '../../../components/ui/EmptyState';
import { IconWell, LINE_ICON_STROKE } from '../../../components/ui/IconWell';
import { Surface, Text } from '../../../ui';
import { AsyncBoundary } from '../../../patterns';
import { StatusPill, TextInput } from '../components/Field';
import { ConnectorBrandIcon } from './ConnectorBrandIcon';
import { ConnectorInstallSheet } from './ConnectorInstallSheet';

const CATALOG_QUERY_KEY = ['connectors', 'catalog'] as const;

function categoryLabel(category: CatalogEntry['category']): string {
  switch (category) {
    case 'mcp_server':
      return 'MCP server';
    case 'mcp_package':
      return 'MCP package';
    default:
      return 'Native';
  }
}

function authLabel(type: CatalogEntry['auth']['type']): string {
  switch (type) {
    case 'oauth2':
      return 'OAuth';
    case 'headers':
      return 'API headers';
    case 'api_key':
      return 'API key';
    case 'env':
      return 'Configuration';
    default:
      return 'No extra credentials';
  }
}

export function ConnectorCatalogPanel({
  onInstalled,
}: {
  onInstalled: () => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | CatalogEntry['category']>('all');
  const [selected, setSelected] = useState<CatalogEntry | null>(null);

  const catalog = useQuery({
    queryKey: CATALOG_QUERY_KEY,
    queryFn: () => connectorsApi.listCatalog(),
  });

  const entries = useMemo(() => {
    const rows = catalog.data?.entries ?? [];
    const q = query.trim().toLowerCase();
    return rows.filter(entry => {
      if (filter !== 'all' && entry.category !== filter) return false;
      if (!q) return true;
      return (
        entry.display_name.toLowerCase().includes(q) ||
        entry.description.toLowerCase().includes(q) ||
        entry.slug.toLowerCase().includes(q)
      );
    });
  }, [catalog.data?.entries, filter, query]);

  return (
    <div className="flex flex-col gap-4">
      <form
        className="flex flex-col gap-2"
        onSubmit={e => e.preventDefault()}
      >
        <TextInput
          value={query}
          onChange={setQuery}
          placeholder="Search vetted connectors…"
        />
        <div className="flex flex-wrap gap-1">
          {(['all', 'native', 'mcp_server', 'mcp_package'] as const).map(id => (
            <Button
              key={id}
              type="button"
              variant={filter === id ? 'secondary' : 'ghost'}
              size="xs"
              onClick={() => setFilter(id)}
            >
              {id === 'all' ? 'All' : categoryLabel(id)}
            </Button>
          ))}
        </div>
      </form>

      <AsyncBoundary
        query={catalog}
        isEmpty={data => (data.entries ?? []).length === 0}
        emptyFallback={
          <EmptyState
            icon={
              <IconWell size="lg" aria-hidden>
                <Store size={22} strokeWidth={LINE_ICON_STROKE} />
              </IconWell>
            }
            title="No vetted connectors match."
            description="Catalog entries are curated in-repo. Try a different search."
          />
        }
      >
        {() => (
          <ul className="grid gap-2 sm:grid-cols-2">
            {entries.length === 0 ? (
              <li>
                <Text variant="body-sm" tone="subtle" as="p">
                  No connectors match this filter.
                </Text>
              </li>
            ) : (
              entries.map(entry => (
              <Surface
                key={entry.slug}
                as="li"
                tone="panel-2"
                border="subtle"
                radius="card"
                padding="md"
                className="flex flex-col gap-3"
              >
                <div className="flex items-start gap-3">
                  <ConnectorBrandIcon icon={entry.icon} label={entry.display_name} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Text variant="body" weight="semibold" as="span">
                        {entry.display_name}
                      </Text>
                      <StatusPill state="idle">{categoryLabel(entry.category)}</StatusPill>
                      <StatusPill state="idle">{authLabel(entry.auth.type)}</StatusPill>
                    </div>
                    <Text
                      variant="body-sm"
                      tone="subtle"
                      as="p"
                      className="mt-1 line-clamp-2"
                    >
                      {entry.description}
                    </Text>
                  </div>
                </div>
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="primary"
                    size="xs"
                    onClick={() => setSelected(entry)}
                  >
                    Add
                  </Button>
                </div>
              </Surface>
              ))
            )}
          </ul>
        )}
      </AsyncBoundary>

      {selected ? (
        <ConnectorInstallSheet
          entry={selected}
          onClose={() => setSelected(null)}
          onInstalled={() => {
            setSelected(null);
            onInstalled();
          }}
        />
      ) : null}
    </div>
  );
}
