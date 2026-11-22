/**
 * Phase 8 Plan 08-02 Task 2 — Vitest coverage for ConnectorsSection.
 *
 * Covers (per plan):
 *   1. renders empty state when list is empty
 *   2. renders one row per connector with derived conflict_policy pill
 *   3. clicking sync button calls connectorsApi.sync(id) and toasts stats
 *   4. clicking edit opens ConnectorEditModal pre-filled
 *   5. clicking delete opens confirm and calls connectorsApi.delete
 *   6. Add Connector opens the vetted catalog library; Back returns to Connected
 *   7. Gmail/QuickBooks wizards are not on home until a connected row is configured
 */
import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
} from 'vitest';
import {
  act,
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  within,
} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../../api/connectors', () => ({
  MCP_OAUTH_MESSAGE_TYPE: 'integral:mcp-oauth',
  connectorsApi: {
    list: vi.fn(),
    listCatalog: vi.fn(),
    getCatalogEntry: vi.fn(),
    installFromCatalog: vi.fn(),
    create: vi.fn(),
    mountMcp: vi.fn(),
    searchMcpRegistry: vi.fn(),
    previewMcpRegistryMount: vi.fn(),
    mountFromRegistry: vi.fn(),
    refreshMcp: vi.fn(),
    reauthorizeMcp: vi.fn(),
    health: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sync: vi.fn(),
    listBindings: vi.fn(),
    createBinding: vi.fn(),
    deleteBinding: vi.fn(),
  },
}));

vi.mock('../../../../components/settings/connectors/GmailConnectorSetup', () => ({
  GmailConnectorSetup: ({ initialConnectorId }: { initialConnectorId?: string }) => (
    <div data-testid="gmail-connector-setup">gmail-setup-{initialConnectorId}</div>
  ),
}));

vi.mock('../../../../components/settings/QuickBooksConnectorSettings', () => ({
  QuickBooksConnectorSettings: () => (
    <div data-testid="quickbooks-connector-settings">qb-settings</div>
  ),
}));

vi.mock('../../../../api/connectionProfiles', () => ({
  connectionProfilesApi: { publicWebChat: vi.fn(), assignPublicWebChat: vi.fn(), setPublicWebChatEnabled: vi.fn() },
  publicWebChatSettingsKey: (id: string) => ['public-web-chat-settings', id],
}));

vi.mock('../../../../api/managedChannels', () => ({
  managedChannelsApi: { list: vi.fn(), change: vi.fn(), profileConfiguration: vi.fn(), assignProfile: vi.fn() },
}));

vi.mock('../../../../api/agentive', () => ({
  getWhatsAppChannelBinding: vi.fn(),
  listChannelIdentities: vi.fn(),
  saveWhatsAppChannelBinding: vi.fn(),
}));

vi.mock('../../../../context/AgentiveContext', () => ({
  useAgentive: () => ({ enabled: true }),
}));

vi.mock('../../hooks/useAgentiveCapability', () => ({
  useAgentiveCapability: vi.fn(),
}));

vi.mock('../../../../context/ScopeContext', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../context/ScopeContext')>(),
  useScopeOptional: vi.fn(),
}));

import {
  connectorsApi,
  type ConnectorResponse,
} from '../../../../api/connectors';
import { connectionProfilesApi } from '../../../../api/connectionProfiles';
import { managedChannelsApi, type ManagedChannel, type ManagedChannelProfileConfiguration } from '../../../../api/managedChannels';
import { useAgentiveCapability } from '../../hooks/useAgentiveCapability';
import { ConnectorsSection } from '../ConnectorsSection';
import { ToastProvider } from '../../../../context/ToastContext';
import { ConfirmProvider } from '../../../../context/ConfirmContext';
import { getWhatsAppChannelBinding, listChannelIdentities, saveWhatsAppChannelBinding } from '../../../../api/agentive';
import { useScopeOptional } from '../../../../context/ScopeContext';

const mockedList = connectorsApi.list as unknown as ReturnType<typeof vi.fn>;
const mockedCatalog = connectorsApi.listCatalog as unknown as ReturnType<
  typeof vi.fn
>;
const mockedSync = connectorsApi.sync as unknown as ReturnType<typeof vi.fn>;
const mockedDelete = connectorsApi.delete as unknown as ReturnType<typeof vi.fn>;
const mockedSearchRegistry = connectorsApi.searchMcpRegistry as unknown as ReturnType<
  typeof vi.fn
>;
const mockedCapability = useAgentiveCapability as unknown as ReturnType<
  typeof vi.fn
>;
const mockedScope = vi.mocked(useScopeOptional);

function makeConnector(
  overrides: Partial<ConnectorResponse> = {},
): ConnectorResponse {
  return {
    id: 'con-1',
    kind: 'custom',
    owner: 'u-1',
    auth_state: {},
    sync_cursor: null,
    mapping_profile: null,
    permissions: [],
    capabilities: [],
    conflict_policy: 'last_write_wins',
    subclass_slug: 'github_issues',
    sync_interval_seconds: 300,
    last_synced_at: null,
    created_at: '2026-05-17T00:00:00Z',
    updated_at: '2026-05-17T00:00:00Z',
    ...overrides,
  };
}

function CurrentSettingsHash() {
  return <span data-testid="settings-hash" hidden>{useLocation().hash}</span>;
}

function panelWithProviders(qc: QueryClient) {
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <ConfirmProvider>
            <ConnectorsSection />
            <CurrentSettingsHash />
          </ConfirmProvider>
        </ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function renderPanel(qc = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})) {
  return render(panelWithProviders(qc));
}

beforeEach(() => {
  vi.clearAllMocks();
  // Portal placement needs measurable geometry, which jsdom does not lay out.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 240, bottom: 40,
    width: 240, height: 40, toJSON: () => ({}),
  });
  mockedScope.mockReturnValue(null);
  mockedList.mockReset().mockResolvedValue({ connectors: [], total: 0 });
  vi.mocked(managedChannelsApi.list).mockReset().mockResolvedValue([]);
  vi.mocked(managedChannelsApi.change).mockReset().mockResolvedValue(undefined);
  vi.mocked(managedChannelsApi.profileConfiguration).mockReset();
  vi.mocked(managedChannelsApi.assignProfile).mockReset().mockResolvedValue(undefined);
  vi.mocked(connectionProfilesApi.publicWebChat).mockResolvedValue({
    revision: 1, profile_key: null, enabled: false, transport_ready: false,
    active: false, eligible_profiles: [],
  });
  vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding: null });
  vi.mocked(listChannelIdentities).mockResolvedValue({ identities: [] });
  // Default: agentive layer ON
  mockedCapability.mockReturnValue({ enabled: true, isLoading: false });
  mockedSync.mockResolvedValue({
    created: 2,
    updated: 1,
    conflict: 0,
    archived: 0,
    errors: [],
  });
  mockedDelete.mockResolvedValue(undefined);
  mockedSearchRegistry.mockResolvedValue({ entries: [], count: 0, next_cursor: null });
  (connectorsApi.listBindings as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    bindings: [],
    total: 0,
  });
  mockedCatalog.mockResolvedValue({
    entries: [
      {
        slug: 'gmail',
        display_name: 'Gmail',
        description: 'Mirror Gmail labels',
        category: 'native',
        kind: 'sync',
        icon: 'gmail',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
          ],
        },
      },
      {
        slug: 'quickbooks',
        display_name: 'QuickBooks',
        description: 'Mirror QuickBooks Online',
        category: 'native',
        kind: 'sync',
        icon: 'quickbooks',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
            {
              name: 'environment',
              label: 'Environment (sandbox or production)',
              secret: false,
              required: false,
            },
          ],
        },
      },
      {
        slug: 'github_issues',
        display_name: 'GitHub Issues',
        description: 'Mirror GitHub issues',
        category: 'native',
        kind: 'sync',
        icon: 'github',
        vetted: true,
        auth: {
          type: 'env',
          fields: [
            { name: 'owner', label: 'Repository owner', secret: false, required: true },
            { name: 'repo', label: 'Repository name', secret: false, required: true },
          ],
        },
      },
      {
        slug: 'calendly',
        display_name: 'Calendly',
        description: 'Book Calendly events',
        category: 'mcp_server',
        kind: 'mcp',
        icon: 'calendly',
        vetted: true,
        auth: { type: 'none', fields: [] },
      },
      {
        slug: 'notion',
        display_name: 'Notion',
        description: 'Read and update Notion pages',
        category: 'mcp_server',
        kind: 'mcp',
        icon: 'notion',
        vetted: true,
        auth: { type: 'none', fields: [] },
      },
      {
        slug: 'google_drive',
        display_name: 'Google Drive',
        description: 'Search and edit Google Drive files',
        category: 'mcp_server',
        kind: 'mcp',
        icon: 'google_drive',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
          ],
        },
      },
      {
        slug: 'google_gmail',
        display_name: 'Gmail MCP',
        description: 'Search Gmail threads and create drafts',
        category: 'mcp_server',
        kind: 'mcp',
        icon: 'gmail',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
          ],
        },
      },
      {
        slug: 'google_sheets',
        display_name: 'Google Sheets',
        description: 'Read and update spreadsheets',
        category: 'mcp_server',
        kind: 'mcp',
        icon: 'google_sheets',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
          ],
        },
      },
      {
        slug: 'quickbooks_mcp',
        display_name: 'QuickBooks MCP',
        description: 'Live QuickBooks Online tools',
        category: 'mcp_package',
        kind: 'mcp',
        icon: 'quickbooks_mcp',
        transport: 'stdio',
        vetted: true,
        auth: {
          type: 'oauth2',
          fields: [
            { name: 'client_id', label: 'Client ID', secret: false, required: false },
            { name: 'client_secret', label: 'Client secret', secret: true, required: false },
            {
              name: 'environment',
              label: 'Environment (sandbox or production)',
              secret: false,
              required: false,
            },
            {
              name: 'QUICKBOOKS_DISABLE_WRITE',
              label: 'Disable create tools (true/false)',
              secret: false,
              required: false,
            },
            {
              name: 'QUICKBOOKS_DISABLE_UPDATE',
              label: 'Disable update tools (true/false)',
              secret: false,
              required: false,
            },
            {
              name: 'QUICKBOOKS_DISABLE_DELETE',
              label: 'Disable delete tools (true/false)',
              secret: false,
              required: false,
            },
          ],
        },
      },
    ],
    total: 9,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('<ConnectorsSection />', () => {
  function selectWorkspace(workspaceId: string) {
    mockedScope.mockReturnValue({
      scope: { workspaceId },
      activeWorkspace: { your_role: 'owner' },
    } as NonNullable<ReturnType<typeof useScopeOptional>>);
  }

  const channelDefinition = {
    slug: 'outside_text_fixture', provider: 'Third-party fixture', display_name: 'Outside Text',
    description: 'A synthetic external text adapter.', setup_kind: 'managed-text-v1', version: 1,
  } as const;
  const available: ManagedChannel = {
    definition: channelDefinition, connected: false, state: 'registered', action: 'add', can_install: true,
  };
  function installed(state: 'inactive' | 'unavailable' | 'active', routeEpoch: string): ManagedChannel {
    return {
      definition: channelDefinition, connected: true, state, action: 'configure',
      can_enable: state === 'inactive', provision_id: 'provision-1', route_epoch: routeEpoch,
    };
  }

  it('adds an unknown descriptor disabled, then configures enable, disable, and remove with fresh generations', async () => {
    selectWorkspace('shared');
    let channel: ManagedChannel = available;
    vi.mocked(managedChannelsApi.list).mockImplementation(async () => [channel]);
    vi.mocked(managedChannelsApi.change).mockImplementation(async (_workspace, _slug, change) => {
      channel = change.kind === 'add' ? installed('inactive', 'r1')
        : change.kind === 'remove' ? available : change.enabled ? installed('active', 'r2') : installed('inactive', 'r3');
    });

    renderPanel();
    expect(screen.queryByText('Outside Text')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    fireEvent.click(screen.getByRole('button', { name: 'Channels' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add Outside Text' }));
    const setup = within(screen.getByRole('dialog', { name: 'Add Outside Text' }));
    expect(setup.getByText(/does not turn on messaging/)).toBeVisible();
    fireEvent.click(setup.getByRole('button', { name: 'Add channel' }));
    expect(await screen.findByRole('heading', { name: 'Connectors' })).toBeVisible();
    expect(screen.getByText('Inactive')).toBeVisible();
    expect(managedChannelsApi.profileConfiguration).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Configure Outside Text' }));
    const configure = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    fireEvent.click(configure.getByRole('button', { name: 'Enable channel' }));
    fireEvent.click(await configure.findByRole('button', { name: 'Disable channel' }));
    await configure.findByRole('button', { name: 'Enable channel' });
    fireEvent.click(configure.getByRole('button', { name: 'Remove channel' }));
    fireEvent.click(configure.getByRole('button', { name: 'Cancel removal' }));
    expect(managedChannelsApi.change).toHaveBeenCalledTimes(3);
    fireEvent.click(configure.getByRole('button', { name: 'Remove channel' }));
    fireEvent.click(configure.getByRole('button', { name: 'Confirm remove channel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: 'Outside Text' })).not.toBeInTheDocument();
    expect(vi.mocked(managedChannelsApi.change).mock.calls).toEqual([
      ['shared', 'outside_text_fixture', { kind: 'add' }],
      ['shared', 'outside_text_fixture', { kind: 'enabled', enabled: true, generation: { provision_id: 'provision-1', route_epoch: 'r1' } }],
      ['shared', 'outside_text_fixture', { kind: 'enabled', enabled: false, generation: { provision_id: 'provision-1', route_epoch: 'r2' } }],
      ['shared', 'outside_text_fixture', { kind: 'remove', generation: { provision_id: 'provision-1', route_epoch: 'r3' } }],
    ]);
    expect(managedChannelsApi.list).toHaveBeenCalledTimes(5);
  });


  function configurable(state: 'inactive' | 'unavailable' | 'active', epoch: string): ManagedChannel {
    return {
      ...installed(state, epoch), connected: true, state, action: 'configure',
      can_enable: state === 'inactive', provision_id: 'provision-1', route_epoch: epoch,
      configuration_kind: 'published-public-profile-v1',
    };
  }
  function profileConfig(revision: number, epoch: string, key: string | null, editable = true): ManagedChannelProfileConfiguration {
    return {
      kind: 'published-public-profile-v1', profile_revision: revision, profile_key: key, editable,
      eligible_profiles: [{ key: 'greeter', name: 'Public greeter' }],
      provision_id: 'provision-1', route_epoch: epoch,
    };
  }

  it('configures a generic installed channel without a profile, verifies its new revision, and enables separately', async () => {
    selectWorkspace('shared');
    let channel = configurable('unavailable', 'r1');
    let config = profileConfig(4, 'r1', null);
    vi.mocked(managedChannelsApi.list).mockImplementation(async () => [channel]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockImplementation(async () => config);
    vi.mocked(managedChannelsApi.assignProfile).mockImplementation(async () => {
      channel = configurable('inactive', 'r2');
      config = profileConfig(5, 'r2', 'greeter');
    });
    vi.mocked(managedChannelsApi.change).mockImplementation(async () => {
      channel = configurable('active', 'r3');
      config = profileConfig(6, 'r3', 'greeter', false);
    });

    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    const picker = await dialog.findByRole('combobox', { name: 'Published public profile' });
    expect(picker).toBeEnabled();
    expect(dialog.queryByRole('button', { name: 'Enable channel' })).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    fireEvent.change(picker, { target: { value: 'greeter' } });
    const save = dialog.getByRole('button', { name: 'Save profile' });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(await dialog.findByText('Profile assignment saved.')).toBeVisible();
    expect(managedChannelsApi.assignProfile).toHaveBeenCalledExactlyOnceWith(
      'shared', 'outside_text_fixture', { provision_id: 'provision-1', route_epoch: 'r1' }, 4, 'greeter');
    expect(managedChannelsApi.profileConfiguration).toHaveBeenCalledWith(
      'shared', 'outside_text_fixture', { provision_id: 'provision-1', route_epoch: 'r2' }, expect.any(AbortSignal));
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
    const enable = dialog.getByRole('button', { name: 'Enable channel' });
    await waitFor(() => expect(enable).toBeEnabled());
    fireEvent.click(enable);
    await waitFor(() => {
      expect(dialog.getByRole('combobox')).toBeDisabled();
      expect(dialog.getByRole('button', { name: 'Disable channel' })).toBeEnabled();
    });
    expect(managedChannelsApi.change).toHaveBeenCalledExactlyOnceWith(
      'shared', 'outside_text_fixture', { kind: 'enabled', enabled: true, generation: { provision_id: 'provision-1', route_epoch: 'r2' } });
    expect(dialog.getByRole('button', { name: 'Disable channel' })).toBeEnabled();
    expect(dialog.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(dialog.queryByRole('button', { name: /share|copy link/i })).not.toBeInTheDocument();
  });

  it('fails closed for a missing configuration, then refreshes and does not claim an unverified assignment', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list).mockResolvedValue([configurable('inactive', 'r1')]);
    vi.mocked(managedChannelsApi.profileConfiguration)
      .mockRejectedValueOnce(new Error('sensitive diagnostics'))
      .mockResolvedValue(profileConfig(3, 'r1', 'no_longer_public'));
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    expect(await dialog.findByText(/Could not check the published profile assignment/)).toBeVisible();
    expect(dialog.getByRole('button', { name: 'Enable channel' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Remove channel' })).toBeEnabled();
    expect(dialog.queryByRole('combobox')).not.toBeInTheDocument();
    expect(dialog.queryByText(/sensitive diagnostics/)).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Retry profile check' }));
    const picker = await dialog.findByRole('combobox');
    expect(picker).toHaveDisplayValue('Current profile is no longer eligible');
    expect(dialog.getByRole('button', { name: 'Enable channel' })).toBeDisabled();
    fireEvent.change(picker, { target: { value: '' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Save profile' }));
    expect(await dialog.findByText(/Could not verify the profile assignment/)).toBeVisible();
    expect(managedChannelsApi.assignProfile).toHaveBeenCalledExactlyOnceWith(
      'shared', 'outside_text_fixture', { provision_id: 'provision-1', route_epoch: 'r1' }, 3, null);
    expect(dialog.queryByText('Profile assignment saved.')).not.toBeInTheDocument();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it('does not apply an in-flight profile result after leaving the workspace', async () => {
    selectWorkspace('original');
    let finishWrite: () => void = () => { throw new Error('Write did not start'); };
    vi.mocked(managedChannelsApi.list).mockImplementation(async workspace =>
      workspace === 'original' ? [configurable('unavailable', 'r1')] : []);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue(profileConfig(4, 'r1', null));
    vi.mocked(managedChannelsApi.assignProfile).mockImplementation(() => new Promise<void>(resolve => { finishWrite = resolve; }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const panel = renderPanel(qc);
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'greeter' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(managedChannelsApi.assignProfile).toHaveBeenCalledTimes(1);
    selectWorkspace('another');
    panel.rerender(panelWithProviders(qc));
    await waitFor(() => expect(managedChannelsApi.list).toHaveBeenCalledWith('another', expect.any(AbortSignal)));
    await act(async () => finishWrite());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('Profile assignment saved.')).not.toBeInTheDocument();
    expect(managedChannelsApi.profileConfiguration).toHaveBeenCalledTimes(1);
  });

  it('discards old setup on workspace switch without replaying an in-flight change or changing the new route', async () => {
    selectWorkspace('original');
    let finishWrite: () => void = () => { throw new Error('Write did not start'); };
    vi.mocked(managedChannelsApi.list).mockImplementation(async workspace => workspace === 'original' ? [available] : []);
    vi.mocked(managedChannelsApi.change).mockImplementation(() => new Promise<void>(resolve => { finishWrite = resolve; }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const panel = renderPanel(qc);
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add Outside Text' }));
    const button = screen.getByRole('button', { name: 'Add channel' });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    selectWorkspace('another');
    panel.rerender(panelWithProviders(qc));
    await waitFor(() => expect(managedChannelsApi.list).toHaveBeenCalledWith('another', expect.any(AbortSignal)));
    await act(async () => finishWrite());
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connector-library');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(managedChannelsApi.change).toHaveBeenCalledTimes(1);
    expect(managedChannelsApi.list).toHaveBeenCalledTimes(2);
  });

  it('shows a registered but unavailable adapter without an enabled Add action', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...available, can_install: false }]);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('button', { name: 'Add Outside Text' })).toBeDisabled();
    expect(screen.getByText('Setup is not available right now.')).toBeVisible();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it('keeps an installed outage configurable without enable or sharing; a failed reread locks stale actions', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list)
      .mockResolvedValueOnce([installed('unavailable', 'r8')])
      .mockRejectedValueOnce(new Error('provider secrets must not be shown'))
      .mockResolvedValueOnce([installed('inactive', 'r9')]);
    vi.mocked(managedChannelsApi.change).mockRejectedValue(new Error('another sensitive failure'));
    renderPanel();
    expect(await screen.findByText('Unavailable')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Configure Outside Text' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    expect(dialog.queryByRole('button', { name: /enable|share|copy link/i })).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Disable channel' }));
    expect(await dialog.findByText('Could not check managed channels.')).toBeVisible();
    expect(await dialog.findByText(/Could not confirm the change/)).toBeVisible();
    expect(dialog.queryByRole('button', { name: /enable|disable|remove/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sensitive|provider secrets/)).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Retry channel check' }));
    expect(await dialog.findByRole('button', { name: 'Enable channel' })).toBeVisible();
    expect(managedChannelsApi.change).toHaveBeenCalledTimes(1);
  });

  it('shows a manager setup status only when the server confirms readiness', async () => {
    selectWorkspace('shared');
    vi.mocked(connectionProfilesApi.publicWebChat).mockResolvedValue({
      revision: 2, profile_key: 'public', enabled: true, transport_ready: true,
      active: true, eligible_profiles: [{ key: 'public', name: 'Visitor guide' }],
    });
    renderPanel();
    expect(await screen.findByText('Assigned profile: Visitor guide')).toBeVisible();
    expect(screen.getByText('Active')).toBeVisible();
    expect(screen.queryByRole('button', { name: /enable|publish|share/i })).not.toBeInTheDocument();
    expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledWith('shared');
    fireEvent.click(screen.getByRole('button', { name: 'Open Agent profiles' }));
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#agent-profiles');
  });

  it('saves and enables separately with the server revisions and refetches', async () => {
    selectWorkspace('shared');
    const initial = {
      revision: 2, profile_key: null, enabled: false, transport_ready: true,
      active: false, eligible_profiles: [{ key: 'public', name: 'Visitor guide' }],
    };
    const assigned = { ...initial, revision: 3, profile_key: 'public' };
    const enabled = { ...assigned, revision: 4, enabled: true, active: true };
    vi.mocked(connectionProfilesApi.publicWebChat)
      .mockResolvedValueOnce(initial).mockResolvedValueOnce(assigned).mockResolvedValue(enabled);
    vi.mocked(connectionProfilesApi.assignPublicWebChat).mockResolvedValue(assigned);
    vi.mocked(connectionProfilesApi.setPublicWebChatEnabled).mockResolvedValue(enabled);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
    expect(dialog.getByRole('button', { name: 'Enable Web Chat' })).toBeDisabled();
    fireEvent.change(dialog.getByRole('combobox', { name: 'Published public profile' }), { target: { value: 'public' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Enable Web Chat' })).toBeEnabled());
    expect(connectionProfilesApi.assignPublicWebChat).toHaveBeenCalledWith('shared', 2, 'public');
    expect(connectionProfilesApi.setPublicWebChatEnabled).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Enable Web Chat' }));
    expect(await dialog.findByRole('button', { name: 'Disable Web Chat' })).toBeVisible();
    expect(connectionProfilesApi.setPublicWebChatEnabled).toHaveBeenCalledWith('shared', 3, true);
    await waitFor(() => expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledTimes(3));
    expect(dialog.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(dialog.queryByRole('button', { name: /share|copy link/i })).not.toBeInTheDocument();
  });

  it('hides Web Chat when the server cannot serve it, including from the library', async () => {
    selectWorkspace('shared');
    renderPanel();
    await waitFor(() => expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledWith('shared'));
    expect(screen.queryByRole('heading', { name: 'Web Chat' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('heading', { name: 'Connector library' })).toBeVisible();
    expect(screen.queryByText('Web Chat')).not.toBeInTheDocument();
  });

  it('does not query or show manager setup for a workspace member', async () => {
    mockedScope.mockReturnValue({
      scope: { workspaceId: 'shared' },
      activeWorkspace: { your_role: 'member' },
    } as NonNullable<ReturnType<typeof useScopeOptional>>);
    renderPanel();
    expect(await screen.findByRole('heading', { name: 'Connectors' })).toBeVisible();
    expect(connectionProfilesApi.publicWebChat).not.toHaveBeenCalled();
    expect(managedChannelsApi.list).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: 'Web Chat' })).not.toBeInTheDocument();
  });

  it('replaces mounted rows and closes the old inspector on workspace switch', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const oldConnector = makeConnector({
      label: 'Personal prototype', subclass_slug: 'mcp', kind: 'mcp',
      auth_state: { discovered_tools: [{ name: 'prototype_read' }] },
    });
    mockedList.mockResolvedValueOnce({ connectors: [oldConnector], total: 1 });
    selectWorkspace('personal');
    const panel = renderPanel(qc);
    await screen.findByText('Personal prototype');
    fireEvent.click(screen.getByRole('button', { name: 'View tools for Personal prototype' }));
    expect(screen.getByText('prototype_read')).toBeInTheDocument();

    let finishNew!: (value: { connectors: ConnectorResponse[]; total: number }) => void;
    mockedList.mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve; }));
    selectWorkspace('shared');
    panel.rerender(panelWithProviders(qc));
    expect(screen.queryByText('Personal prototype')).not.toBeInTheDocument();
    expect(screen.queryByText('prototype_read')).not.toBeInTheDocument();
    finishNew({ connectors: [makeConnector({ label: 'Shared research' })], total: 1 });
    await screen.findByText('Shared research');
    expect(mockedList).toHaveBeenCalledTimes(2);
  });

  it('ignores a delayed response from the previous workspace', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let finishOld!: (value: { connectors: ConnectorResponse[]; total: number }) => void;
    mockedList.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    selectWorkspace('personal');
    const panel = renderPanel(qc);
    mockedList.mockResolvedValueOnce({ connectors: [makeConnector({ label: 'Shared research' })], total: 1 });
    selectWorkspace('shared');
    panel.rerender(panelWithProviders(qc));
    await screen.findByText('Shared research');
    finishOld({ connectors: [makeConnector({ label: 'Late personal result' })], total: 1 });
    await waitFor(() => expect(qc.getQueryData(['connectors', 'list', '', 'personal'])).toBeDefined());
    expect(screen.queryByText('Late personal result')).not.toBeInTheDocument();
    expect(screen.getByText('Shared research')).toBeInTheDocument();
  });

  it('does not open a delayed authorization popup after switching workspace', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    let finishAuthorization!: (value: { connector_id: string; consent_url: string; state: string }) => void;
    vi.mocked(connectorsApi.reauthorizeMcp).mockImplementationOnce(() => new Promise(resolve => { finishAuthorization = resolve; }));
    mockedList.mockResolvedValueOnce({ connectors: [makeConnector({
      id: 'mcp-old', label: 'Personal prototype', subclass_slug: 'mcp', kind: 'mcp',
      health_status: 'error', auth_state: { oauth: { status: 'pending' } },
    })], total: 1 });
    selectWorkspace('personal');
    const panel = renderPanel(qc);
    fireEvent.click(await screen.findByRole('button', { name: 'Manage Personal prototype' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Reconnect' }));
    await waitFor(() => expect(connectorsApi.reauthorizeMcp).toHaveBeenCalledOnce());
    mockedList.mockResolvedValueOnce({ connectors: [], total: 0 });
    selectWorkspace('shared');
    panel.rerender(panelWithProviders(qc));
    await screen.findByText(/No connectors yet\./i);
    finishAuthorization({ connector_id: 'mcp-old', consent_url: 'https://example.com/consent', state: 'test-state' });
    await waitFor(() => expect(qc.getMutationCache().getAll().find(m => m.state.variables === 'mcp-old')?.state.status).toBe('success'));
    expect(open).not.toHaveBeenCalled();
  });

  it('renders empty state when list is empty', async () => {
    mockedList.mockResolvedValueOnce({ connectors: [], total: 0 });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/No connectors yet\./i)).toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: 'Connectors', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Connected', level: 3 })).toBeInTheDocument();
    expect(screen.queryByText('Setup wizards')).not.toBeInTheDocument();
    expect(screen.queryByText('All connectors')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Search MCP servers/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('gmail-connector-setup')).not.toBeInTheDocument();
    expect(screen.queryByTestId('quickbooks-connector-card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('quickbooks-connector-settings')).not.toBeInTheDocument();
  });

  it('renders one row per connector with derived conflict_policy pill', async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({ id: 'con-1', conflict_policy: 'last_write_wins' }),
        makeConnector({
          id: 'con-2',
          kind: 'mcp',
          subclass_slug: null,
          conflict_policy: null,
        }),
      ],
      total: 2,
    });
    renderPanel();
    // Two rows render — verify by counting the per-row Sync buttons.
    await waitFor(() => {
      expect(screen.getByLabelText('Sync connector con-1')).toBeInTheDocument();
    });
    expect(screen.getByLabelText('Sync connector con-2')).toBeInTheDocument();
    // Derived conflict_policy pill (A5).
    expect(screen.getByText('last_write_wins')).toBeInTheDocument();
    // None-slug row renders the idle "no policy" pill.
    expect(screen.getByText('no conflict rule')).toBeInTheDocument();
  });

  it('shows MCP connector name and a compact tool inspector', async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [
        makeConnector({
          id: 'mcp-1',
          kind: 'mcp',
          subclass_slug: 'mcp',
          conflict_policy: null,
          health_status: 'ok',
          auth_state: {
            display_name: 'Firecrawl MCP Server',
            transport: 'streamable_http',
            url: 'https://mcp.firecrawl.dev/mcp',
            registry: {
              name: 'io.github.firecrawl/firecrawl-mcp-server',
              version: '3.24.0',
            },
            discovered_tools: [
              { name: 'scrape', description: 'Scrape a URL' },
              { name: 'crawl', description: 'Crawl a site' },
            ],
          },
        }),
      ],
      total: 1,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText('Firecrawl MCP Server')).toBeInTheDocument();
    });
    expect(
      screen.getByText('io.github.firecrawl/firecrawl-mcp-server'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View tools for Firecrawl MCP Server' })).toHaveTextContent(
      'View 2 tools',
    );
    expect(screen.queryByText('scrape')).not.toBeInTheDocument();
    expect(screen.queryByText('crawl')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'View tools for Firecrawl MCP Server' }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Firecrawl MCP Server tools' });
    expect(within(dialog).getByText('scrape')).toBeInTheDocument();
    expect(within(dialog).getByText('crawl')).toBeInTheDocument();
    expect(within(dialog).getByText('Scrape a URL')).toBeInTheDocument();
    expect(screen.queryByText('MCP mount')).not.toBeInTheDocument();
  });

  it('Add Connector opens the vetted catalog library and Back returns home', async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Connected' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Connector library', level: 2 }),
      ).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText(/Search connectors/i)).toBeInTheDocument();
    expect(await screen.findByText('Gmail', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('QuickBooks', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('GitHub Issues', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('Calendly', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('Notion', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('Google Drive', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('Gmail MCP', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('Google Sheets', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.getByText('QuickBooks MCP', { selector: ':not(title)' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Connected' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Search MCP servers/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back to connectors' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Connected' })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Add Connector' })).toHaveFocus();
  });

  it('opens WhatsApp Add over the filtered library and restores focus on Cancel', async () => {
    selectWorkspace('shared');
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    mockedCatalog.mockResolvedValue({ entries: [], total: 0 });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Connector' }));
    fireEvent.click(screen.getByRole('button', { name: 'Channels' }));
    const search = screen.getByPlaceholderText('Search connectors…');
    fireEvent.change(search, { target: { value: 'whatsapp' } });
    const add = await screen.findByRole('button', { name: 'Add WhatsApp (JV Connect)' });
    add.focus();
    fireEvent.click(add);
    const dialog = await screen.findByRole('dialog', { name: 'Add WhatsApp (JV Connect)' });
    expect(await within(dialog).findByRole('button', { name: 'Add connection' })).toBeDisabled();
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connector-library');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(dialog).not.toBeInTheDocument();
    expect(add).toHaveFocus();
    expect(search).toHaveValue('whatsapp');
    expect(screen.getByRole('button', { name: 'Channels' })).toHaveAttribute('aria-pressed', 'true');
    expect(connectorsApi.installFromCatalog).not.toHaveBeenCalled();
    expect(saveWhatsAppChannelBinding).not.toHaveBeenCalled();
  });

  it('configures an installed WhatsApp line in the connected list and the library', async () => {
    selectWorkspace('shared');
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: { id: 'wa-existing', enabled: false, jvconnect_base_url: 'https://local.invalid' },
    });
    mockedList.mockResolvedValue({ connectors: [makeConnector({ label: 'Workspace API', connection_mode: 'shared' })], total: 1 });
    renderPanel();
    const heading = await screen.findByRole('heading', { name: 'WhatsApp (JV Connect)' });
    const list = heading.closest('ul');
    expect(list).toContainElement(screen.getByText('Workspace API'));
    expect(within(heading.closest('li')!).getByText('Disabled')).toBeVisible();
    fireEvent.click(screen.getByRole('radio', { name: 'Per-user' }));
    expect(heading).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Workspace' }));
    const configure = screen.getByRole('button', { name: 'Configure WhatsApp' });
    configure.focus();
    fireEvent.click(configure);
    const dialog = await screen.findByRole('dialog', { name: 'Configure WhatsApp (JV Connect)' });
    expect(await within(dialog).findByRole('button', { name: 'Disconnect' })).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(configure).toHaveFocus();
    expect(screen.getByTestId('settings-hash')).toBeEmptyDOMElement();

    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(screen.queryByRole('button', { name: 'Add WhatsApp (JV Connect)' })).not.toBeInTheDocument();
    const libraryConfigure = await screen.findByRole('button', { name: 'Configure WhatsApp (JV Connect)' });
    libraryConfigure.focus();
    fireEvent.click(libraryConfigure);
    expect(screen.getByRole('dialog', { name: 'Configure WhatsApp (JV Connect)' })).toBeVisible();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(libraryConfigure).toHaveFocus();
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connector-library');
  });

  it('keeps GitHub Issues in the library and restores focus on Cancel', async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    const card = (await screen.findByText('GitHub Issues', { selector: ':not(title)' })).closest('li')!;
    const add = within(card).getByRole('button', { name: 'Add' });
    add.focus();
    fireEvent.click(add);
    const dialog = screen.getByRole('dialog', { name: 'Install GitHub Issues' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(add).toHaveFocus();
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connector-library');
  });

  it('opens Add for another custom MCP instance while keeping existing rows individually managed', async () => {
    mockedList.mockResolvedValue({ connectors: [makeConnector({
      subclass_slug: 'mcp', kind: 'mcp', label: 'Existing MCP fixture',
    })], total: 1 });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Manage Existing MCP fixture' }));
    expect(screen.getByRole('menuitem', { name: 'Configure connection' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: 'Remove connection' })).toBeVisible();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    const add = screen.getByRole('button', { name: 'Add Custom MCP Server' });
    add.focus();
    fireEvent.click(add);
    const dialog = screen.getByRole('dialog', { name: 'Add custom MCP server' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(add).toHaveFocus();
    expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connector-library');
    expect(connectorsApi.mountMcp).not.toHaveBeenCalled();
  });

  it('QuickBooks install sheet lets the operator enter Client ID and secret', async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Connector' }));
    const qbName = await screen.findByText('QuickBooks', { selector: ':not(title)' });
    const card = qbName.closest('li');
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Add' }));
    expect(
      await screen.findByRole('heading', { name: 'Install QuickBooks' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Client ID')).toBeInTheDocument();
    expect(screen.getByText('Client secret')).toBeInTheDocument();
    expect(
      screen.getByText(/Leave a field blank to use the matching server/i),
    ).toBeInTheDocument();
  });

  it('QuickBooks MCP install sheet starts Intuit OAuth, not a token paste', async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Connector' }));
    const qbName = await screen.findByText('QuickBooks MCP', { selector: ':not(title)' });
    const card = qbName.closest('li');
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Add' }));
    expect(
      await screen.findByRole('heading', { name: 'Install QuickBooks MCP' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Client ID')).toBeInTheDocument();
    expect(screen.getByText('Client secret')).toBeInTheDocument();
    expect(screen.queryByText('Refresh token')).not.toBeInTheDocument();
    expect(
      screen.getByText(/Connect opens Intuit sign-in/i),
    ).toBeInTheDocument();
    expect(screen.queryByText('Disable create tools (true/false)')).not.toBeInTheDocument();
    const disableCreate = screen.getByRole('button', { name: 'Disable create tools' });
    const disableUpdate = screen.getByRole('button', { name: 'Disable update tools' });
    const disableDelete = screen.getByRole('button', { name: 'Disable delete tools' });
    expect(disableCreate).toHaveAttribute('aria-pressed', 'false');
    expect(disableUpdate).toHaveAttribute('aria-pressed', 'false');
    expect(disableDelete).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(disableCreate);
    expect(disableCreate).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument();
  });

  it('Google Drive install sheet starts OAuth, not a silent MCP mount', async () => {
    mockedList.mockResolvedValue({ connectors: [], total: 0 });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Connector' }));
    const driveName = await screen.findByText('Google Drive', { selector: ':not(title)' });
    const card = driveName.closest('li');
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Add' }));
    expect(
      await screen.findByRole('heading', { name: 'Install Google Drive' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Client ID')).toBeInTheDocument();
    expect(screen.getByText('Client secret')).toBeInTheDocument();
    expect(screen.getByText(/Connect opens a sign-in popup/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument();
  });

  it('Gmail settings live on the connected row, not on home until opened', async () => {
    mockedList.mockResolvedValue({
      connectors: [
        makeConnector({ id: 'g-1', subclass_slug: 'gmail', kind: 'custom' }),
      ],
      total: 1,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByLabelText('Sync connector g-1')).toBeInTheDocument();
    });
    expect(screen.getByText('Gmail')).toBeInTheDocument();
    expect(screen.queryByTestId('gmail-connector-setup')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Gmail' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Service settings' }));
    expect(screen.getByTestId('gmail-connector-setup')).toBeInTheDocument();
  });

  it('clicking sync calls connectorsApi.sync(id) and toasts stats', async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [makeConnector({ id: 'con-1' })],
      total: 1,
    });
    renderPanel();
    const btn = await screen.findByLabelText('Sync connector con-1');
    fireEvent.click(btn);
    await waitFor(() => {
      expect(mockedSync).toHaveBeenCalledWith('con-1');
    });
    // Toast contains the {created, updated, conflict} stats line.
    await waitFor(() => {
      expect(
        screen.getByText(/2 created, 1 updated, 0 conflicts/i),
      ).toBeInTheDocument();
    });
  });

  it('clicking edit opens ConnectorEditModal pre-filled', async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [makeConnector({ id: 'con-1', subclass_slug: 'github_issues' })],
      total: 1,
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /^Manage /  }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Configure connection' }));
    // Modal title appears
    await waitFor(() => {
      expect(
        screen.getByText(/Edit connector — custom:con-1/i),
      ).toBeInTheDocument();
    });
  });

  it('clicking delete opens confirm and calls connectorsApi.delete on confirm', async () => {
    mockedList.mockResolvedValueOnce({
      connectors: [makeConnector({ id: 'con-1' })],
      total: 1,
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /^Manage /  }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove connection' }));
    const confirmBtn = await screen.findByRole('button', { name: /^Delete$/ });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith('con-1');
    });
  });

});
