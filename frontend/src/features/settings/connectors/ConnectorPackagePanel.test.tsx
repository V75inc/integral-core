import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectorPackagesApi } from '../../../api/connectorPackages';
import { managedChannelsApi } from '../../../api/managedChannels';
import { saveWhatsAppChannelBinding } from '../../../api/agentive';
import { ConfirmProvider } from '../../../context/ConfirmContext';
import { ToastProvider } from '../../../context/ToastContext';
import { ConnectorPackagePanel } from './ConnectorPackagePanel';

vi.mock('../../../api/connectors', () => ({
  connectorsApi: {
    listBindings: vi.fn().mockResolvedValue({ bindings: [], total: 0 }),
    createBinding: vi.fn(),
    deleteBinding: vi.fn(),
  },
}));

vi.mock('../../../api/tracks', () => ({
  tracksApi: { list: vi.fn().mockResolvedValue([]) },
}));

vi.mock('../../../api/agentive', () => ({
  getWhatsAppChannelBinding: vi.fn().mockResolvedValue({ binding: null }),
  saveWhatsAppChannelBinding: vi.fn(),
  disableWhatsAppChannelBinding: vi.fn(),
}));

vi.mock('../../../api/managedChannels', () => ({
  managedChannelsApi: {
    credentialMetadata: vi.fn().mockResolvedValue({ binding: null }),
    configureCredentials: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('../../../api/connectorPackages', () => ({
  connectorPackagesApi: {
    list: vi.fn(),
    rescan: vi.fn(),
    install: vi.fn(),
    configure: vi.fn(),
    startOAuth: vi.fn(),
    setEnabled: vi.fn(),
    remove: vi.fn(),
  },
}));

const list = vi.mocked(connectorPackagesApi.list);
const configure = vi.mocked(connectorPackagesApi.configure);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ConnectorPackagePanel', () => {
  it('renders Add and Configure from the manifest and omits a blank secret', async () => {
    list.mockResolvedValue({
      packages: [
        {
          id: 'public_web_chat',
          name: 'Web Chat',
          description: 'A managed Web Chat channel.',
          version: '1.0.0',
          capabilities: ['channel'],
          host_managed: true,
          channel_mode: 'managed-text-v1',
          configuration: [],
        },
        {
          id: 'acme_crm',
          name: 'Acme CRM',
          description: 'Look up contacts.',
          version: '1.0.0',
          capabilities: ['actions'],
          configuration: [
            { key: 'base_url', label: 'URL', type: 'url', required: true },
            { key: 'api_key', label: 'Key', type: 'secret', required: true },
          ],
        },
      ],
      rejected: [{ id: 'broken', reason: 'schema' }],
      installations: [
        {
          id: 'inst-1',
          package_id: 'acme_crm',
          name: 'Acme CRM',
          version: '1.0.0',
          revision: 0,
          enabled: true,
          health_status: 'healthy',
          config: { base_url: 'https://crm.example.test' },
          pending_review: false,
        },
      ],
    });
    configure.mockResolvedValue({
      id: 'inst-1',
      package_id: 'acme_crm',
      name: 'Acme CRM',
      version: '1.0.0',
      revision: 1,
      enabled: false,
      health_status: 'unknown',
      config: { base_url: 'https://crm.example.test/v2' },
      pending_review: false,
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectorPackagePanel workspaceId="ws-1" />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('broken: schema')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Web Chat' })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/URL/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Configure' }));
    const url = document.getElementById('app-settings-field-inst-1-base_url') as HTMLInputElement;
    fireEvent.change(url, { target: { value: 'https://crm.example.test/v2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(configure).toHaveBeenCalledOnce());
    const values = configure.mock.calls[0][2];
    expect(values.base_url).toBe('https://crm.example.test/v2');
    expect(values).not.toHaveProperty('api_key');

    fireEvent.click(screen.getByRole('button', { name: 'Configure' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Key' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(configure).toHaveBeenCalledTimes(2));
    expect(configure.mock.calls[1][2].api_key).toBeNull();
  });

  it('starts OAuth for the existing package installation', async () => {
    list.mockResolvedValue({
      packages: [{
        id: 'acme_mail', name: 'Acme Mail', description: 'A mailbox connector.',
        version: '1.0.0', capabilities: ['actions'], auth_type: 'oauth2', configuration: [],
      }],
      rejected: [],
      installations: [{
        id: 'mail-install', package_id: 'acme_mail', name: 'Acme Mail',
        version: '1.0.0', revision: 0, enabled: false, health_status: 'unknown',
        config: {}, pending_review: false,
      }],
    });
    vi.mocked(connectorPackagesApi.startOAuth).mockResolvedValue({
      consent_url: 'https://consent.example/authorize',
    });
    const replace = vi.fn();
    const open = vi.spyOn(window, 'open').mockReturnValue({
      location: { replace },
      close: vi.fn(),
    } as unknown as Window);
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <ConnectorPackagePanel workspaceId="ws-1" />
        </QueryClientProvider>,
      );
      fireEvent.click(await screen.findByRole('button', { name: 'Authorize' }));
      await waitFor(() => expect(replace).toHaveBeenCalledWith('https://consent.example/authorize'));
      expect(connectorPackagesApi.startOAuth).toHaveBeenCalledWith('ws-1', 'acme_mail', 'mail-install');
    } finally {
      open.mockRestore();
    }
  });

  it('offers a Track binding when the package syncs', async () => {
    list.mockResolvedValue({
      packages: [
        {
          id: 'acme_sync',
          name: 'Acme Sync',
          description: 'Pull contacts.',
          version: '1.0.0',
          capabilities: ['sync'],
          configuration: [],
        },
      ],
      rejected: [],
      installations: [
        {
          id: 'inst-sync',
          package_id: 'acme_sync',
          name: 'Acme Sync',
          version: '1.0.0',
          revision: 0,
          enabled: false,
          health_status: 'unknown',
          config: {},
          pending_review: false,
        },
      ],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <ConfirmProvider>
            <ConnectorPackagePanel workspaceId="ws-1" />
          </ConfirmProvider>
        </ToastProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Track bindings/)).toBeInTheDocument();
  });

  it('disconnects a host-managed WhatsApp line without adding an installation', async () => {
    const { disableWhatsAppChannelBinding } = await import('../../../api/agentive');
    vi.mocked(disableWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'b1',
        user_id: 'u',
        workspace_id: 'ws-1',
        jvconnect_base_url: 'https://connect.example',
        enabled: false,
      },
    });
    list.mockResolvedValue({
      packages: [
        {
          id: 'whatsapp',
          name: 'WhatsApp',
          description: 'Workspace line.',
          version: '1.0.0',
          capabilities: ['channel'],
          host_managed: true,
          configuration: [],
          grant: {
            edge: 'USES_WHATSAPP_LINE',
            present: true,
            id: 'b1',
            enabled: true,
          },
        },
      ],
      rejected: [],
      installations: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectorPackagePanel workspaceId="ws-1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/USES_WHATSAPP_LINE/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(disableWhatsAppChannelBinding).toHaveBeenCalledWith('b1'));
    expect(connectorPackagesApi.install).not.toHaveBeenCalled();
  });

  it('saves the WhatsApp line through the existing binding', async () => {
    const { getWhatsAppChannelBinding, saveWhatsAppChannelBinding } = await import('../../../api/agentive');
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'b1',
        user_id: 'u',
        workspace_id: 'ws-1',
        jvconnect_base_url: 'https://old.example',
        enabled: true,
      },
    });
    vi.mocked(saveWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'b1',
        user_id: 'u',
        workspace_id: 'ws-1',
        jvconnect_base_url: 'https://new.example',
        enabled: false,
      },
    });
    list.mockResolvedValue({
      packages: [
        {
          id: 'whatsapp',
          name: 'WhatsApp',
          description: 'Workspace line.',
          version: '1.0.0',
          capabilities: ['channel'],
          host_managed: true,
          configuration: [],
          grant: {
            edge: 'USES_WHATSAPP_LINE',
            present: true,
            id: 'b1',
            enabled: true,
          },
        },
      ],
      rejected: [],
      installations: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectorPackagePanel workspaceId="ws-1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByLabelText(/jvconnect base URL/)).toHaveValue('https://old.example'));
    const url = screen.getByLabelText(/jvconnect base URL/);
    fireEvent.change(url, { target: { value: 'https://new.example' } });
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: 'jvk_new' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(saveWhatsAppChannelBinding).toHaveBeenCalledWith({
      jvconnect_base_url: 'https://new.example',
      api_key: 'jvk_new',
      expected_binding_id: 'b1',
    }));
    expect(connectorPackagesApi.install).not.toHaveBeenCalled();
  });

  it('saves managed line credentials through the package lifecycle', async () => {
    list.mockResolvedValue({
      packages: [{
        id: 'whatsapp', name: 'WhatsApp', description: 'A managed line.',
        version: '1.0.0', capabilities: ['channel'], host_managed: true,
        channel_mode: 'managed-text-v1', configuration: [],
        grant: { edge: 'USES_WHATSAPP_LINE', present: false },
      }],
      rejected: [],
      installations: [],
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ConnectorPackagePanel workspaceId="ws-1" />
      </QueryClientProvider>,
    );

    fireEvent.change(await screen.findByLabelText(/jvconnect base URL/i), {
      target: { value: 'https://connect.example' },
    });
    fireEvent.change(screen.getByLabelText('API key'), {
      target: { value: 'fixture-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save line' }));
    await waitFor(() => expect(managedChannelsApi.configureCredentials).toHaveBeenCalledWith(
      'ws-1', 'whatsapp', {
        jvconnect_base_url: 'https://connect.example',
        api_key: 'fixture-key',
        expected_binding_id: null,
      },
    ));
    expect(saveWhatsAppChannelBinding).not.toHaveBeenCalled();
  });

  it('shows the line form from the package grant, not the package id', async () => {
    list.mockResolvedValue({
      packages: [
        {
          id: 'acme_line',
          name: 'Acme line',
          description: 'A host line.',
          version: '1.0.0',
          capabilities: ['channel'],
          host_managed: true,
          configuration: [],
          grant: { edge: 'USES_WHATSAPP_LINE', present: false },
        },
        {
          id: 'whatsapp',
          name: 'WhatsApp',
          description: 'No grant on this row.',
          version: '1.0.0',
          capabilities: ['channel'],
          host_managed: true,
          configuration: [],
        },
      ],
      rejected: [],
      installations: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectorPackagePanel workspaceId="ws-1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Acme line' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Save line' })).toHaveLength(1);
    expect(screen.getByRole('heading', { name: 'WhatsApp' })).toBeInTheDocument();
  });
});
