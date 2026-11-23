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

vi.mock('../../connectors/managedChannelEmbedShareReader', () => ({ readManagedChannelEmbedShare: vi.fn() }));
vi.mock('../../connectors/managedChannelShareReader', () => ({ readManagedChannelShare: vi.fn() }));

vi.mock('../../../../api/managedWebChatPrivateTest', async importOriginal => ({
  ...await importOriginal<typeof import('../../../../api/managedWebChatPrivateTest')>(),
  runManagedWebChatPrivateTest: vi.fn(),
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

vi.mock('../../../../context/AuthContext', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../context/AuthContext')>(),
  useAuthOptional: vi.fn(),
}));

vi.mock('../../../../api/session', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../api/session')>(),
  getSessionGeneration: vi.fn(),
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
import { useAuthOptional } from '../../../../context/AuthContext';
import { getSessionGeneration } from '../../../../api/session';
import type { ManagedChannelShareSource } from '../../connectors/ManagedChannelShareControl';
import { readManagedChannelShare } from '../../connectors/managedChannelShareReader';
import { readManagedChannelEmbedShare } from '../../connectors/managedChannelEmbedShareReader';
import { decodeApprovedManagedEmbed, type ManagedEmbedOwner } from '../../connectors/managedChannelEmbedProof';
import { managedProfileOptions } from '../../connectors/managedChannelQueries';
import { runManagedWebChatPrivateTest } from '../../../../api/managedWebChatPrivateTest';

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

function panelWithProviders(qc: QueryClient, shareSource?: ManagedChannelShareSource) {
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <ConfirmProvider>
            <ConnectorsSection shareSource={shareSource} />
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
  vi.mocked(useAuthOptional).mockReturnValue(null);
  vi.mocked(getSessionGeneration).mockReturnValue(1);
  vi.mocked(runManagedWebChatPrivateTest).mockReset();
  vi.mocked(readManagedChannelShare).mockReset();
  vi.mocked(readManagedChannelEmbedShare).mockReset();
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


  function configurable(state: 'inactive' | 'unavailable' | 'active', epoch: string): Extract<ManagedChannel, { connected: true }> {
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

  function authenticateManager(id: string) {
    vi.mocked(useAuthOptional).mockReturnValue({
      user: { id }, token: 'synthetic-auth-fixture', loading: false,
    } as NonNullable<ReturnType<typeof useAuthOptional>>);
  }

  it('shows a neutral pending enablement until the exact host-ready generation is read back', async () => {
    selectWorkspace('shared');
    authenticateManager('original-manager');
    let channel = configurable('inactive', 'r2');
    vi.mocked(managedChannelsApi.list).mockImplementation(async () => [channel]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockImplementation(async (_workspace, _slug, generation) => ({
      ...profileConfig(5, generation.route_epoch, 'greeter', channel.state === 'inactive'), published_version: 7,
    }));
    vi.mocked(managedChannelsApi.change).mockImplementation(async () => {
      channel = configurable('unavailable', 'r3');
    });
    const read = vi.fn<ManagedChannelShareSource['read']>();
    const source = { scope: { principalId: 'original-manager', workspaceId: 'shared', sessionFence: '1' }, read };
    render(panelWithProviders(new QueryClient({ defaultOptions: { queries: { retry: false } } }), source));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    const enable = dialog.getByRole('button', { name: 'Enable channel' });
    await waitFor(() => expect(enable).toBeEnabled());
    fireEvent.click(enable);
    expect(await dialog.findByText('Enable request accepted. Waiting for the host to be ready.')).toBeVisible();
    expect(dialog.getByText('Unavailable')).toBeVisible();
    expect(dialog.queryByRole('alert')).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();

    channel = configurable('active', 'r3');
    fireEvent.click(dialog.getByRole('button', { name: 'Refresh channel status' }));
    expect(await dialog.findByText('Active')).toBeVisible();
    expect(await dialog.findByRole('button', { name: 'Check full-page link' })).toBeVisible();
    expect(dialog.queryByText(/enable request accepted/i)).not.toBeInTheDocument();
    expect(managedChannelsApi.change).toHaveBeenCalledExactlyOnceWith(
      'shared', 'outside_text_fixture', { kind: 'enabled', enabled: true, generation: { provision_id: 'provision-1', route_epoch: 'r2' } });
    expect(read).not.toHaveBeenCalled();
  });

  it.each(['same epoch', 'another provision'])('does not claim pending enablement with %s', async change => {
    selectWorkspace('shared');
    let channel = configurable('inactive', 'r2');
    vi.mocked(managedChannelsApi.list).mockImplementation(async () => [channel]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue(profileConfig(5, 'r2', 'greeter'));
    vi.mocked(managedChannelsApi.change).mockImplementation(async () => {
      channel = change === 'same epoch' ? configurable('unavailable', 'r2')
        : { ...configurable('unavailable', 'r3'), provision_id: 'different-provision' };
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Outside Text' }));
    const enable = dialog.getByRole('button', { name: 'Enable channel' });
    await waitFor(() => expect(enable).toBeEnabled());
    fireEvent.click(enable);
    expect(await dialog.findByRole('alert')).toHaveTextContent('The requested channel status could not be confirmed.');
    expect(dialog.queryByText(/enable request accepted/i)).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Refresh channel status' })).not.toBeInTheDocument();
    expect(managedChannelsApi.change).toHaveBeenCalledTimes(1);
  });

  it('forwards an approved source only to the exact active managed Configure and drops it on session or actor change', async () => {
    selectWorkspace('shared');
    authenticateManager('original-manager');
    vi.mocked(managedChannelsApi.list).mockResolvedValue([configurable('active', 'r3')]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
      ...profileConfig(5, 'r3', 'greeter', false), published_version: 7,
    });
    let finish!: () => void;
    let signal!: AbortSignal;
    const read = vi.fn<ManagedChannelShareSource['read']>((owner, abort) => {
      signal = abort;
      return new Promise(resolve => { finish = () => resolve({
        kind: 'approved-full-page-share-v1', workspace_id: owner.workspaceId, slug: owner.slug,
        provision_id: owner.provisionId, route_epoch: owner.routeEpoch,
        profile_key: owner.publication.profileKey, published_version: owner.publication.publishedVersion,
        full_page_url: 'https://fixture-visitor.example.invalid/chat/unserved',
      }); });
    });
    const source = { scope: { principalId: 'original-manager', workspaceId: 'shared', sessionFence: '1' }, read };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const panel = render(panelWithProviders(qc, source));
    const configure = await screen.findByRole('button', { name: 'Configure Outside Text' });
    expect(screen.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();
    fireEvent.click(configure);
    fireEvent.click(await screen.findByRole('button', { name: 'Check full-page link' }));
    expect(read).toHaveBeenCalledTimes(1);
    expect(signal.aborted).toBe(false);

    vi.mocked(getSessionGeneration).mockReturnValue(2);
    panel.rerender(panelWithProviders(qc, source));
    expect(signal.aborted).toBe(true);
    expect(screen.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();
    await act(async () => finish());
    expect(screen.queryByRole('link', { name: 'Open full-page chat' })).not.toBeInTheDocument();

    authenticateManager('different-manager');
    panel.rerender(panelWithProviders(qc, { ...source, scope: { ...source.scope, sessionFence: '2' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Outside Text' }));
    expect(await screen.findByRole('combobox', { name: 'Published public profile' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes(1);
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

  it('shows only a read-only Web Chat library entry for known unavailable transport', async () => {
    selectWorkspace('shared');
    renderPanel();
    await waitFor(() => expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledWith('shared'));
    expect(screen.queryByRole('heading', { name: 'Web Chat' })).not.toBeInTheDocument();
    expect(screen.queryByText('Web Chat', { exact: true })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('heading', { name: 'Connector library' })).toBeVisible();
    expect(await screen.findByText('Gmail', { selector: ':not(title)' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Channels' }));
    fireEvent.change(screen.getByPlaceholderText('Search connectors…'), { target: { value: 'Web Chat' } });
    const notice = (await screen.findByText('Web Chat', { exact: true })).closest('li') as HTMLLIElement;
    expect(within(notice).getByText('Unavailable')).toBeVisible();
    expect(within(notice).getByText(/no ready Web Chat host/)).toBeVisible();
    expect(within(notice).queryByRole('button')).not.toBeInTheDocument();
    expect(within(notice).queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Connected' })).not.toBeInTheDocument();
    expect(connectorsApi.installFromCatalog).not.toHaveBeenCalled();
    expect(connectionProfilesApi.setPublicWebChatEnabled).not.toHaveBeenCalled();
  });

  it('adds managed Web Chat while the host is unavailable, saves only a published public profile, and keeps it pending', async () => {
    selectWorkspace('shared');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    const draft = (epoch: string): ManagedChannel => ({
      ...configurable('inactive', epoch), definition, can_enable: false,
    });
    let channel: ManagedChannel = { ...available, definition };
    let config = profileConfig(0, '1', null);
    vi.mocked(managedChannelsApi.list).mockImplementation(async () => [channel]);
    vi.mocked(managedChannelsApi.change).mockImplementation(async (_workspace, _slug, change) => {
      if (change.kind === 'add') channel = draft('1');
    });
    vi.mocked(managedChannelsApi.profileConfiguration).mockImplementation(async () => config);
    vi.mocked(managedChannelsApi.assignProfile).mockImplementation(async () => {
      channel = draft('2');
      config = { ...profileConfig(1, '2', 'greeter'), published_version: 2 };
    });

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    const addWebChat = await screen.findByRole('button', { name: 'Add Web Chat' });
    expect(addWebChat).toBeEnabled();
    fireEvent.click(addWebChat);
    const add = within(screen.getByRole('dialog', { name: 'Add Web Chat' }));
    expect(add.getByText(/does not publish a chat or turn on messaging/)).toBeVisible();
    expect(add.getByText(/Connection scope: Workspace/)).toBeVisible();
    expect(add.queryByRole('combobox')).not.toBeInTheDocument();
    fireEvent.click(add.getByRole('button', { name: 'Add channel' }));
    await waitFor(() => expect(screen.getByTestId('settings-hash')).toHaveTextContent('#connectors'));
    expect(screen.queryByRole('dialog', { name: 'Add Web Chat' })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const configure = within(await screen.findByRole('dialog', { name: 'Configure Web Chat' }));
    const picker = await configure.findByRole('combobox', { name: 'Published public profile' });
    expect(picker).toBeEnabled();
    expect(configure.getByText('Setup pending')).toBeVisible();
    expect(configure.getByText(/added but not live/)).toBeVisible();
    expect(configure.getByText(/no separate connector to install/i)).toBeVisible();
    expect(configure.getByText(/hosting readiness is not confirmed/i)).toBeVisible();
    expect(configure.getByText('No profile saved yet.')).toBeVisible();
    expect(within(picker).getAllByRole('option').map(option => option.textContent))
      .toEqual(['No profile assigned', 'Public greeter']);
    expect(configure.queryByRole('button', { name: /enable|disable|pair|publish|embed|full.page/i })).not.toBeInTheDocument();
    expect(configure.queryByRole('link')).not.toBeInTheDocument();

    fireEvent.change(picker, { target: { value: 'greeter' } });
    expect(configure.getByText('No profile saved yet.')).toBeVisible();
    fireEvent.click(configure.getByRole('button', { name: 'Save profile' }));
    expect(await configure.findByText('Saved profile: Public greeter')).toBeVisible();
    expect(configure.getByText('Profile assignment saved.')).toBeVisible();
    expect(configure.getByText('Setup pending')).toBeVisible();
    expect(managedChannelsApi.assignProfile).toHaveBeenCalledExactlyOnceWith(
      'shared', 'public_web_chat', { provision_id: 'provision-1', route_epoch: '1' }, 0, 'greeter');
    expect(managedChannelsApi.change).toHaveBeenCalledExactlyOnceWith('shared', 'public_web_chat', { kind: 'add' });
    expect(connectionProfilesApi.setPublicWebChatEnabled).not.toHaveBeenCalled();

    fireEvent.click(configure.getAllByRole('button', { name: 'Close' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const reopened = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
    expect(await reopened.findByText('Saved profile: Public greeter')).toBeVisible();
    fireEvent.click(reopened.getByRole('button', { name: 'Refresh setup status' }));
    await waitFor(() => expect(managedChannelsApi.list).toHaveBeenCalledTimes(4));
    expect(await reopened.findByText('Saved profile: Public greeter')).toBeVisible();
    expect(reopened.queryByRole('button', { name: /enable|pair|publish|embed|full.page/i })).not.toBeInTheDocument();
    expect(managedChannelsApi.assignProfile).toHaveBeenCalledTimes(1);
    expect(managedChannelsApi.change).toHaveBeenCalledTimes(1);
  });

  it('explains when a pending Web Chat draft has no eligible published public profile', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{
      ...configurable('inactive', '1'), can_enable: false,
      definition: { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' },
    }]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
      ...profileConfig(1, '1', 'removed-profile'), eligible_profiles: [],
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const picker = await screen.findByRole('combobox', { name: 'Published public profile' });
    expect(screen.getByText('No eligible published public profiles are available.')).toBeVisible();
    expect(screen.getByText('The saved profile is no longer eligible.')).toBeVisible();
    expect(within(picker).getByRole('option', { name: 'Current profile is no longer eligible' })).toBeDisabled();
    expect(screen.queryByRole('option', { name: 'removed-profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /enable|pair|publish/i })).not.toBeInTheDocument();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it('reports only verified publishing details in active Web Chat Configure without inventing a share source', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable('active', '3'), definition }]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
      ...profileConfig(1, '3', 'greeter', false), published_version: 2,
    });
    const read = vi.fn<ManagedChannelShareSource['read']>();
    const source = { scope: { principalId: 'current-manager', workspaceId: 'shared', sessionFence: '1' }, read };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(panelWithProviders(qc, source));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
    expect(await dialog.findByText('No verified full-page link, embed code or API access details are available here.'))
      .toBeVisible();
    expect(dialog.getByText(/hosting and publishing belong to this web chat connector/i)).toBeVisible();
    expect(dialog.queryByRole('link')).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: /full.page|embed|api access|publish|pair/i })).not.toBeInTheDocument();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
    expect(read).not.toHaveBeenCalled();
    expect(readManagedChannelShare).not.toHaveBeenCalled();
  });

  it('drops verified full-page checks on non-active state and drops late replies after Configure closes', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    const active = { ...configurable('active', '3'), definition };
    const inactive = { ...configurable('inactive', '3'), definition };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([active]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
      ...profileConfig(1, '3', 'greeter', false), published_version: 2, full_page_share_check_available: true,
    });
    const checks: Array<{ signal: AbortSignal; finish: () => void }> = [];
    const read = vi.mocked(readManagedChannelShare).mockImplementation((owner, signal) =>
      new Promise(resolve => checks.push({ signal, finish: () => resolve({
        kind: 'approved-full-page-share-v1', workspace_id: owner.workspaceId, slug: owner.slug,
        provision_id: owner.provisionId, route_epoch: owner.routeEpoch,
        profile_key: owner.publication.profileKey, published_version: owner.publication.publishedVersion,
        full_page_url: 'https://fixture-visitor.example.invalid/chat/unserved',
      }) })));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const listKey = ['connectors', 'managed-channels', 'current-manager', 'shared'];
    render(panelWithProviders(qc));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));

    expect(await dialog.findByText(/Check the full-page link below/)).toBeVisible();
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Check full-page link' }));
    expect(read).toHaveBeenCalledExactlyOnceWith({
      principalId: 'current-manager', workspaceId: 'shared', sessionFence: '1', slug: 'public_web_chat',
      provisionId: 'provision-1', routeEpoch: '3', publication: { profileKey: 'greeter', publishedVersion: 2 },
    }, expect.any(AbortSignal));
    expect(checks[0].signal.aborted).toBe(false);
    await act(async () => { qc.setQueryData(listKey, [inactive]); });
    expect(await dialog.findByText(/hosting is ready, but web chat is not live/i)).toBeVisible();
    expect(checks[0].signal.aborted).toBe(true);
    expect(dialog.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();
    await act(async () => { checks[0].finish(); });
    expect(dialog.queryByRole('link')).not.toBeInTheDocument();

    await act(async () => { qc.setQueryData(listKey, [active]); });
    fireEvent.click(await dialog.findByRole('button', { name: 'Check full-page link' }));
    fireEvent.click(dialog.getAllByRole('button', { name: 'Close' })[0]);
    expect(checks[1].signal.aborted).toBe(true);
    await act(async () => { checks[1].finish(); });
    expect(screen.queryByRole('dialog', { name: 'Configure Web Chat' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open full-page chat' })).not.toBeInTheDocument();
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });


  it('opens or copies only a fresh exact full-page share after an approved Configure gesture', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable('active', '3'), definition }]);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
      ...profileConfig(1, '3', 'greeter', false), published_version: 2, full_page_share_check_available: true,
    });
    const url = 'https://fixture-visitor.example.invalid/chat/unserved';
    vi.mocked(readManagedChannelShare).mockImplementation(async owner => ({
      kind: 'approved-full-page-share-v1', workspace_id: owner.workspaceId, slug: owner.slug,
      provision_id: owner.provisionId, route_epoch: owner.routeEpoch, profile_key: owner.publication.profileKey,
      published_version: owner.publication.publishedVersion, full_page_url: url,
    }));
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const check = await screen.findByRole('button', { name: 'Check full-page link' });
    expect(readManagedChannelShare).not.toHaveBeenCalled();
    expect(screen.queryByRole('link', { name: 'Open full-page chat' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copy full-page link' })).not.toBeInTheDocument();
    fireEvent.click(check);
    expect(await screen.findByRole('link', { name: 'Open full-page chat' })).toHaveAttribute('href', url);
    expect(screen.getByRole('button', { name: 'Copy full-page link' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /embed|api access/i })).not.toBeInTheDocument();
    expect(readManagedChannelShare).toHaveBeenCalledTimes(1);
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it('requires a new Configure admission after the signed-in session changes', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable('active', '3'), definition }]);
    const base = { ...profileConfig(1, '3', 'greeter', false), published_version: 2 };
    vi.mocked(managedChannelsApi.profileConfiguration)
      .mockResolvedValueOnce({ ...base, full_page_share_check_available: true })
      .mockResolvedValue(base);
    let finish!: () => void;
    let signal!: AbortSignal;
    const read = vi.mocked(readManagedChannelShare).mockImplementation((owner, abort) => {
      signal = abort;
      return new Promise(resolve => { finish = () => resolve({
        kind: 'approved-full-page-share-v1', workspace_id: owner.workspaceId, slug: owner.slug,
        provision_id: owner.provisionId, route_epoch: owner.routeEpoch,
        profile_key: owner.publication.profileKey, published_version: owner.publication.publishedVersion,
        full_page_url: 'https://fixture-visitor.example.invalid/chat/unserved',
      }); });
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(panelWithProviders(qc));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Check full-page link' }));
    vi.mocked(getSessionGeneration).mockReturnValue(2);
    view.rerender(panelWithProviders(qc));
    expect(signal.aborted).toBe(true);
    await act(async () => finish());
    expect(await screen.findByText('No verified full-page link, embed code or API access details are available here.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Check full-page link' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open full-page chat' })).not.toBeInTheDocument();
    expect(managedChannelsApi.profileConfiguration).toHaveBeenCalledTimes(2);
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('only checks selected approved embed policies and drops stale results across origin, policy, and session changes', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable('active', '3'), definition }]);
    const one = { parent_origin: 'https://first.example.com', embed_policy_revision: 4 };
    const two = { parent_origin: 'https://second.example.com', embed_policy_revision: 5 };
    const projection = {
      ...profileConfig(1, '3', 'greeter', false), published_version: 2, approved_embed_policies: [one, two],
    };
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue(projection);
    const url = 'https://fixture-embed.example.invalid/embed/unserved';
    const checks: Array<{ signal: AbortSignal; finish: () => void; owner: ManagedEmbedOwner }> = [];
    vi.mocked(readManagedChannelEmbedShare).mockImplementation((owner, signal) => new Promise(resolve =>
      checks.push({ signal, owner, finish: () => resolve(decodeApprovedManagedEmbed({
        kind: 'approved-web-chat-embed-v1', workspace_id: owner.workspaceId, slug: owner.slug,
        provision_id: owner.provisionId, route_epoch: owner.routeEpoch, profile_key: owner.publication.profileKey,
        published_version: owner.publication.publishedVersion, embed_policy_revision: owner.policy.policyRevision,
        parent_origin: owner.policy.parentOrigin, embed_page_url: url,
      }, owner)) })));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(panelWithProviders(qc));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
    const picker = await dialog.findByRole('combobox', { name: 'Approved embedding site' });
    expect(within(picker).getAllByRole('option').map(option => option.textContent))
      .toEqual(['Choose an approved site', one.parent_origin, two.parent_origin]);
    expect(picker).toHaveValue('');
    expect(readManagedChannelEmbedShare).not.toHaveBeenCalled();
    expect(dialog.queryByRole('button', { name: 'Check embed code' })).not.toBeInTheDocument();
    fireEvent.change(picker, { target: { value: one.parent_origin } });
    expect(readManagedChannelEmbedShare).not.toHaveBeenCalled();
    expect(dialog.queryByRole('textbox', { name: 'Embed code' })).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Check embed code' }));
    expect(checks[0].owner).toMatchObject({
      principalId: 'current-manager', workspaceId: 'shared', sessionFence: '1', slug: 'public_web_chat',
      provisionId: 'provision-1', routeEpoch: '3', publication: { profileKey: 'greeter', publishedVersion: 2 },
      policy: { parentOrigin: one.parent_origin, policyRevision: one.embed_policy_revision },
    });
    fireEvent.change(picker, { target: { value: two.parent_origin } });
    expect(checks[0].signal.aborted).toBe(true);
    await act(async () => checks[0].finish());
    expect(dialog.queryByRole('textbox', { name: 'Embed code' })).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Check embed code' }));
    await act(async () => checks[1].finish());
    const snippet = await dialog.findByRole<HTMLTextAreaElement>('textbox', { name: 'Embed code' });
    expect(snippet.value).toContain(url);
    expect(snippet.value).toContain(two.parent_origin);
    expect(dialog.getByRole('button', { name: 'Copy embed code' })).toBeVisible();
    expect(dialog.queryByRole('button', { name: /full.page|api access/i })).not.toBeInTheDocument();

    const generation = { provision_id: 'provision-1', route_epoch: '3' };
    const cfgKey = managedProfileOptions('current-manager', 'shared', 'public_web_chat', generation).queryKey;
    const changed = { ...projection, approved_embed_policies: [{ ...two, embed_policy_revision: 6 }] };
    await act(async () => { qc.setQueryData(cfgKey, changed); });
    await waitFor(() => expect(dialog.queryByRole('textbox', { name: 'Embed code' })).not.toBeInTheDocument());
    const next = dialog.getByRole('combobox', { name: 'Approved embedding site' });
    expect(next).toHaveValue('');
    expect(within(next).queryByRole('option', { name: one.parent_origin })).not.toBeInTheDocument();
    fireEvent.change(next, { target: { value: two.parent_origin } });
    fireEvent.click(dialog.getByRole('button', { name: 'Check embed code' }));
    expect(checks[2].owner.policy.policyRevision).toBe(6);
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({ ...projection, approved_embed_policies: undefined });
    vi.mocked(getSessionGeneration).mockReturnValue(2);
    view.rerender(panelWithProviders(qc));
    expect(checks[2].signal.aborted).toBe(true);
    await act(async () => checks[2].finish());
    expect(dialog.queryByRole('combobox', { name: 'Approved embedding site' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('textbox', { name: 'Embed code' })).not.toBeInTheDocument();
    expect(readManagedChannelEmbedShare).toHaveBeenCalledTimes(3);
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it.each(['inactive', 'active', 'unavailable'] as const)(
    'offers an explicit private test in the same Configure dialog for %s Web Chat with exact permission',
    async state => {
      selectWorkspace('shared');
      authenticateManager('current-manager');
      const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
      vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable(state, '3'), definition }]);
      vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({
        ...profileConfig(1, '3', 'greeter', state !== 'active'), published_version: 2, private_test_available: true,
      });
      let finish!: () => void;
      const reply = '<a href="https://fixture.example.invalid">Literal test reply</a>';
      const run = vi.mocked(runManagedWebChatPrivateTest).mockImplementation((_owner, _text, _signal) =>
        new Promise(resolve => { finish = () => resolve(reply); }));
      renderPanel();
      fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
      const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
      const openTest = await dialog.findByRole('button', { name: 'Test Web Chat' });
      expect(run).not.toHaveBeenCalled();
      fireEvent.click(openTest);
      const pane = within(dialog.getByRole('region', { name: 'Test Web Chat' }));
      expect(screen.getAllByRole('dialog')).toHaveLength(1);
      expect(pane.getByText(/private test; does not publish or check public hosting/i)).toBeVisible();
      const send = pane.getByRole('button', { name: 'Send test' });
      expect(send).toBeDisabled();
      fireEvent.change(pane.getByRole('textbox', { name: 'Test message' }), { target: { value: 'Try a greeting' } });
      expect(run).not.toHaveBeenCalled();
      fireEvent.click(send);
      fireEvent.click(send);
      expect(run).toHaveBeenCalledExactlyOnceWith({
        principalId: 'current-manager', workspaceId: 'shared', sessionFence: '1', provisionId: 'provision-1',
        routeEpoch: '3', profileKey: 'greeter', publishedVersion: 2,
      }, 'Try a greeting', expect.any(AbortSignal));
      await act(async () => finish());
      expect(await pane.findByText(reply)).toBeVisible();
      expect(pane.queryByRole('link')).not.toBeInTheDocument();
      expect(dialog.queryByRole('link', { name: 'Open full-page chat' })).not.toBeInTheDocument();
      expect(managedChannelsApi.change).not.toHaveBeenCalled();
    },
  );

  it('never opens a test on missing admission and clears and aborts late sends on close or publication change', async () => {
    selectWorkspace('shared');
    authenticateManager('current-manager');
    const definition = { ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat' };
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{ ...configurable('inactive', '3'), definition }]);
    const prior = { ...profileConfig(1, '3', 'greeter'), published_version: 2, private_test_available: true as const };
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue(prior);
    const calls: Array<{signal: AbortSignal; finish: () => void}> = [];
    vi.mocked(runManagedWebChatPrivateTest).mockImplementation((_owner, _text, signal) => new Promise(resolve => {
      calls.push({ signal, finish: () => resolve('Do not show this late reply') });
    }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const panel = render(panelWithProviders(qc));
    fireEvent.click(await screen.findByRole('button', { name: 'Configure Web Chat' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Configure Web Chat' }));
    const begin = async () => {
      fireEvent.click(await dialog.findByRole('button', { name: 'Test Web Chat' }));
      const box = dialog.getByRole('textbox', { name: 'Test message' });
      expect(box).toHaveValue('');
      fireEvent.change(box, { target: { value: 'A new private question' } });
      fireEvent.click(dialog.getByRole('button', { name: 'Send test' }));
    };
    await begin();
    fireEvent.click(dialog.getByRole('button', { name: 'Close test' }));
    expect(calls[0].signal.aborted).toBe(true);
    await act(async () => calls[0].finish());
    expect(dialog.queryByText(/late reply/)).not.toBeInTheDocument();

    await begin();
    const cfgKey = managedProfileOptions('current-manager', 'shared', 'public_web_chat', {
      provision_id: 'provision-1', route_epoch: '3',
    }).queryKey;
    await act(async () => { qc.setQueryData(cfgKey, { ...prior, published_version: 3 }); });
    await waitFor(() => expect(calls[1].signal.aborted).toBe(true));
    await act(async () => calls[1].finish());
    expect(dialog.queryByText(/late reply/)).not.toBeInTheDocument();

    await begin();
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({ ...prior, private_test_available: undefined });
    vi.mocked(getSessionGeneration).mockReturnValue(2);
    panel.rerender(panelWithProviders(qc));
    expect(calls[2].signal.aborted).toBe(true);
    await act(async () => calls[2].finish());
    expect(dialog.queryByRole('button', { name: 'Test Web Chat' })).not.toBeInTheDocument();
    expect(dialog.queryByText(/late reply/)).not.toBeInTheDocument();
    expect(runManagedWebChatPrivateTest).toHaveBeenCalledTimes(3);
  });

  it('honors the managed Web Chat entry rather than the legacy fallback', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list).mockResolvedValue([{
      ...available,
      definition: {
        ...channelDefinition, slug: 'public_web_chat', display_name: 'Web Chat',
      },
      can_install: false,
    }]);
    vi.mocked(connectionProfilesApi.publicWebChat).mockResolvedValue({
      revision: 2, profile_key: null, enabled: false, transport_ready: true,
      active: false, eligible_profiles: [],
    });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('button', { name: 'Add Web Chat' })).toBeDisabled();
    expect(screen.getAllByText('Web Chat', { exact: true })).toHaveLength(1);
    expect(screen.queryByText('Setup in Connectors')).not.toBeInTheDocument();
  });

  it('does not show the legacy Web Chat entry when managed listing fails', async () => {
    selectWorkspace('shared');
    vi.mocked(managedChannelsApi.list).mockRejectedValue(new Error('Unavailable'));
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByText('Gmail', { selector: ':not(title)' })).toBeVisible();
    await waitFor(() => expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledWith('shared'));
    expect(screen.queryByText('Web Chat', { exact: true })).not.toBeInTheDocument();
  });

  it.each([403, 404, 'network'])('does not advertise an unverified service (%s)', async status => {
    selectWorkspace('shared');
    vi.mocked(connectionProfilesApi.publicWebChat).mockRejectedValue({ status });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByText('Gmail', { selector: ':not(title)' })).toBeVisible();
    await waitFor(() => expect(connectionProfilesApi.publicWebChat).toHaveBeenCalledWith('shared'));
    expect(screen.queryByText('Web Chat', { exact: true })).not.toBeInTheDocument();
  });

  it('points a ready manager to existing Connectors setup without adding a library action', async () => {
    selectWorkspace('shared');
    vi.mocked(connectionProfilesApi.publicWebChat).mockResolvedValue({
      revision: 2, profile_key: null, enabled: false, transport_ready: true,
      active: false, eligible_profiles: [],
    });
    renderPanel();
    expect(await screen.findByRole('button', { name: 'Configure Web Chat' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    const notice = (await screen.findByText('Web Chat', { exact: true })).closest('li') as HTMLLIElement;
    expect(within(notice).getByText('Setup in Connectors')).toBeVisible();
    expect(within(notice).queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Configure Web Chat' })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('heading', { name: 'Connector library' })).toBeVisible();
    expect(screen.queryByText('Web Chat', { exact: true })).not.toBeInTheDocument();
    expect(connectionProfilesApi.publicWebChat).not.toHaveBeenCalled();
  });

  it('does not advertise the organization channel in a personal workspace', async () => {
    mockedScope.mockReturnValue({
      scope: { workspaceId: 'personal' }, isPersonal: true,
      activeWorkspace: { your_role: 'owner' },
    } as NonNullable<ReturnType<typeof useScopeOptional>>);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Add Connector' }));
    expect(await screen.findByRole('heading', { name: 'Connector library' })).toBeVisible();
    expect(screen.queryByText('Web Chat', { exact: true })).not.toBeInTheDocument();
    expect(connectionProfilesApi.publicWebChat).not.toHaveBeenCalled();
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
