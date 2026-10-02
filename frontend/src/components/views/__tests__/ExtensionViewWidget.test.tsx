import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../api/extensions', () => ({
  extensionsApi: {
    handshake: vi.fn(async () => ({
      handshake_token: 'tok',
      package_version: '1.0.0',
      theme: {},
    })),
  },
}));

vi.mock('../../../context/ScopeContext', () => ({
  useScope: () => ({ scope: { workspaceId: 'ws-1' } }),
}));

vi.mock('../../extensions/AppExtensionViewHost', () => ({
  AppExtensionViewHost: (props: {
    appId: string;
    viewKey: string;
    onDraftPatch?: unknown;
  }) => (
    <div
      data-testid="ext-host"
      data-has-draft-patch={props.onDraftPatch ? '1' : '0'}
    >
      {props.appId}:{props.viewKey}
    </div>
  ),
}));

import { ExtensionViewWidget } from '../ExtensionViewWidget';
import {
  ContributionLifecycleContext,
  type ContributionLifecycleApi,
} from '../../entries/contributionLifecycle';
import type { SavedView } from '../../../types';

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe('ExtensionViewWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('falls back to __bindings.appId when track.app is missing (document shell)', async () => {
    const view = {
      id: 'v1',
      type: 'extension_view',
      track_id: 'track-1',
      name: 'Payments',
      config: {
        extension_view_key: 'invoice_payment_panel',
        __bindings: { appId: 'app-finance', entryId: 'entry-1', trackId: 'track-1' },
      },
    } as unknown as SavedView;

    wrap(
      <ExtensionViewWidget
        view={view}
        entries={[]}
        isLoading={false}
        onEntryOpen={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('ext-host').textContent).toBe(
        'app-finance:invoice_payment_panel',
      );
    });
  });

  it('forwards lifecycle onDraftPatch into AppExtensionViewHost', async () => {
    const onDraftPatch = vi.fn();
    const lifecycle: ContributionLifecycleApi = {
      mode: 'detail',
      placement: 'entry_detail',
      entryId: 'entry-1',
      trackId: 'track-1',
      appId: 'app-finance',
      customFields: { balance: 100, status: 'open' },
      onDraftPatch,
      register: () => () => undefined,
    };
    const view = {
      id: 'v1',
      type: 'extension_view',
      track_id: 'track-1',
      name: 'Payments',
      config: {
        extension_view_key: 'invoice_payment_panel',
        __bindings: { appId: 'app-finance', entryId: 'entry-1' },
      },
    } as unknown as SavedView;

    wrap(
      <ContributionLifecycleContext.Provider value={lifecycle}>
        <ExtensionViewWidget
          view={view}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => undefined}
        />
      </ContributionLifecycleContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('ext-host').getAttribute('data-has-draft-patch')).toBe(
        '1',
      );
    });
  });

  it('shows fallback when app and view key are both missing', () => {
    const view = {
      id: 'v1',
      type: 'extension_view',
      track_id: 'track-1',
      name: 'Broken',
      config: {},
    } as unknown as SavedView;

    wrap(
      <ExtensionViewWidget
        view={view}
        entries={[]}
        isLoading={false}
        onEntryOpen={() => undefined}
      />,
    );

    expect(
      screen.getByText(/missing app or view key metadata/i),
    ).toBeInTheDocument();
  });
});
