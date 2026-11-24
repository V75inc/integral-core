import { render, screen, within } from '@testing-library/react';
import type { UseQueryResult } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import type { CatalogEntry, CatalogListResponse } from '../../../api/connectors';
import { ConnectorCatalogPanel } from './ConnectorCatalogPanel';

function entry(slug: string, name: string, hostManaged: boolean): CatalogEntry {
  return {
    slug,
    display_name: name,
    description: 'A catalog connector.',
    category: 'native',
    kind: 'sync',
    icon: 'generic',
    vetted: true,
    auth: { type: 'api_key', fields: [] },
    host_managed: hostManaged,
  };
}

function catalog(entries: CatalogEntry[]): UseQueryResult<CatalogListResponse> {
  return {
    data: { entries, total: entries.length },
    isLoading: false,
    isError: false,
    isSuccess: true,
    status: 'success',
    fetchStatus: 'idle',
    error: null,
  } as UseQueryResult<CatalogListResponse>;
}

describe('ConnectorCatalogPanel', () => {
  it('points host-managed packages at Dropped-in packages', () => {
    render(
      <ConnectorCatalogPanel
        catalog={catalog([
          entry('acme_line', 'Acme line', true),
          entry('whatsapp', 'WhatsApp', false),
        ])}
        whatsappEnabled={false}
        whatsappStatus="new"
        onInstalled={() => undefined}
      />,
    );
    const acme = screen.getByText('Acme line').closest('li')!;
    expect(within(acme).getByText('Configure this line in Dropped-in packages.')).toBeVisible();
    expect(within(acme).queryByRole('button')).toBeNull();
    const named = screen.getByText('WhatsApp').closest('li')!;
    expect(within(named).queryByText('Configure this line in Dropped-in packages.')).toBeNull();
    expect(within(named).getByRole('button', { name: 'Add' })).toBeVisible();
  });
});
