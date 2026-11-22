import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { managedChannelsApi, type ManagedChannel, type ManagedChannelProfileConfiguration } from '../../../../api/managedChannels';
import { ManagedChannelSettings } from '../ManagedChannelSettings';
import { managedProfileOptions } from '../managedChannelQueries';
import type { ManagedChannelShareSource } from '../ManagedChannelShareControl';
import type { ManagedShareOwner } from '../managedChannelShareProof';

vi.mock('../../../../api/managedChannels', () => ({
  managedChannelsApi: { profileConfiguration: vi.fn(), change: vi.fn() },
}));

const channel = {
  definition: { slug: 'synthetic', provider: 'Fixture', display_name: 'Test channel',
    description: 'No service.', setup_kind: 'managed-text-v1', version: 1 },
  connected: true, state: 'active', action: 'configure',
  can_enable: false, provision_id: 'installed', route_epoch: 'epoch',
  configuration_kind: 'published-public-profile-v1',
} satisfies ManagedChannel;
const generation = { provision_id: channel.provision_id, route_epoch: channel.route_epoch };
const fixture: ManagedChannelProfileConfiguration = {
  ...generation, kind: 'published-public-profile-v1', profile_key: 'public',
  profile_revision: 2, published_version: 7, editable: false,
  eligible_profiles: [{ key: 'public', name: 'A visitor profile' }],
};
const selection = { slug: channel.definition.slug, name: channel.definition.display_name, action: 'configure' } as const;
const scope = { principalId: 'manager', workspaceId: 'shared', sessionFence: 'local-auth-fence' };
const scopedKey = managedProfileOptions(scope.principalId, scope.workspaceId, selection.slug, generation).queryKey;
const url = 'https://fixture-visitor.example.invalid/chat/synthetic-key';

function wire(owner: ManagedShareOwner, href = url) {
  return { kind: 'approved-full-page-share-v1', workspace_id: owner.workspaceId, slug: owner.slug,
    provision_id: owner.provisionId, route_epoch: owner.routeEpoch, profile_key: owner.publication.profileKey,
    published_version: owner.publication.publishedVersion, full_page_url: href };
}

function Dialog({ source }: { source?: ManagedChannelShareSource }) {
  const query = useQuery<ManagedChannel[]>({ queryKey: ['active-test-channel'], queryFn: async () => [channel] });
  return <ManagedChannelSettings selection={selection} principalId={scope.principalId} workspaceId={scope.workspaceId}
    query={query} shareSource={source} onClose={vi.fn()} onInstalled={vi.fn()} onAbandoned={vi.fn()} />;
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue(fixture);
});

describe('optional Configure share read', () => {
  it('has no share control by default; also rejects absent publication and mismatched scope', async () => {
    vi.mocked(managedChannelsApi.profileConfiguration).mockResolvedValue({ ...fixture, published_version: undefined });
    const read = vi.fn();
    const qc = client();
    const draw = (source?: ManagedChannelShareSource) => <QueryClientProvider client={qc}><Dialog source={source} /></QueryClientProvider>;
    const { rerender } = render(draw());
    expect(await screen.findByRole('combobox', { name: 'Published public profile' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /full-page link/i })).not.toBeInTheDocument();
    rerender(draw({ scope, read }));
    expect(screen.queryByRole('button', { name: /full-page link/i })).not.toBeInTheDocument();
    await act(async () => { qc.setQueryData(scopedKey, fixture); });
    expect(await screen.findByRole('button', { name: /full-page link/i })).toBeVisible();
    rerender(draw({ scope: { ...scope, workspaceId: 'elsewhere' }, read }));
    expect(screen.queryByRole('button', { name: /full-page link/i })).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  it('checks once on a gesture and hides the previous link on speculative refresh, republish, or mutation event', async () => {
    const read = vi.fn<ManagedChannelShareSource['read']>().mockImplementation(async owner => wire(owner));
    const qc = client();
    render(<QueryClientProvider client={qc}><Dialog source={{ scope, read }} /></QueryClientProvider>);
    const dialog = within(screen.getByRole('dialog'));
    const check = await dialog.findByRole('button', { name: 'Check full-page link' });
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(check);
    fireEvent.click(check);
    expect(await dialog.findByRole('link', { name: 'Open full-page chat' })).toHaveAttribute('href', url);
    expect(read).toHaveBeenCalledExactlyOnceWith(
      { ...scope, slug: selection.slug, provisionId: 'installed', routeEpoch: 'epoch',
        publication: { profileKey: 'public', publishedVersion: 7 } }, expect.any(AbortSignal));
    act(() => window.dispatchEvent(new Event('integral:records-changed')));
    expect(dialog.queryByRole('link')).not.toBeInTheDocument();

    fireEvent.click(dialog.getByRole('button', { name: 'Check full-page link' }));
    expect(await dialog.findByRole('link')).toBeVisible();
    await act(async () => { qc.setQueryData(scopedKey, { ...fixture, published_version: 8 }); });
    await waitFor(() => expect(dialog.queryByRole('link')).not.toBeInTheDocument());
    expect(read).toHaveBeenCalledTimes(2);
    expect(managedChannelsApi.change).not.toHaveBeenCalled();
  });

  it('aborts and discards a late proof after the same manager changes session', async () => {
    let release!: () => void;
    let signal!: AbortSignal;
    const read = vi.fn<ManagedChannelShareSource['read']>((owner, abort) => {
      signal = abort;
      return new Promise(resolve => { release = () => resolve(wire(owner)); });
    });
    const qc = client();
    const draw = (sessionFence: string) => (
      <QueryClientProvider client={qc}><Dialog source={{ scope: { ...scope, sessionFence }, read }} /></QueryClientProvider>
    );
    const { rerender } = render(draw('first-auth-context'));
    fireEvent.click(await screen.findByRole('button', { name: 'Check full-page link' }));
    expect(signal.aborted).toBe(false);
    rerender(draw('second-auth-context'));
    expect(signal.aborted).toBe(true);
    await act(async () => release());
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(1);
  });
});
