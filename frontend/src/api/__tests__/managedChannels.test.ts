import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../client';
import { managedChannelsApi } from '../managedChannels';

vi.mock('../client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const definition = {
  slug: 'example_text', provider: 'Example Provider', display_name: 'Example Text',
  description: 'A reviewed text channel.', setup_kind: 'managed-text-v1', version: 1,
};
const registered = { definition, connected: false, action: 'add', state: 'registered', can_install: true };

beforeEach(() => vi.clearAllMocks());

describe('managed channel transport', () => {
  it('decodes versioned inert listings and never grants action for ambiguous or unsupported responses', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { channels: [
      registered,
      { ...registered, definition: { ...definition, version: 2 } },
      { ...registered, definition: { ...definition, slug: 'unknown_setup', setup_kind: 'future' } },
      { ...registered, definition: { ...definition, slug: 'bad_slug/' } },
      { ...registered, definition: { ...definition, slug: 'waiting_text' }, can_install: undefined },
      { ...registered, definition: { ...definition, slug: 'outage_text' }, action: 'configure', state: 'unavailable',
        connected: true, can_enable: true, provision_id: 'p1', route_epoch: 'e2', full_page_url: 'https://example.test' },
      { ...registered, definition: { ...definition, slug: 'missing_epoch' }, action: 'configure', state: 'inactive',
        connected: true, can_enable: true, provision_id: 'p1' },
    ] } });
    const signal = new AbortController().signal;
    const entries = await managedChannelsApi.list('workspace', signal);
    expect(entries.map(row => row.definition.slug)).toEqual(['waiting_text', 'outage_text']);
    expect(entries[0]).toMatchObject({ can_install: false });
    expect(entries[1]).toMatchObject({ can_enable: false, route_epoch: 'e2' });
    expect(entries[1]).not.toHaveProperty('full_page_url');
    expect(apiClient.get).toHaveBeenCalledWith('/agentive/managed-channels', expect.objectContaining({
      signal, headers: { 'X-Integral-Scope': 'ws:workspace' }, params: { workspace_id: 'workspace' },
    }));
  });


  it('admits the supported profile capability only on installed channels and disables activation for unknown kinds', async () => {
    const installed = { ...registered, state: 'inactive', connected: true, action: 'configure',
      can_enable: true, provision_id: 'p1', route_epoch: 'e2' };
    vi.mocked(apiClient.get).mockResolvedValue({ data: { channels: [
      { ...installed, configuration_kind: 'published-public-profile-v1' },
      { ...installed, definition: { ...definition, slug: 'other_text' }, configuration_kind: 'unknown-picker' },
      { ...registered, definition: { ...definition, slug: 'not_connected' }, configuration_kind: 'published-public-profile-v1' },
    ] } });
    const rows = await managedChannelsApi.list('workspace');
    expect(rows[0]).toMatchObject({ can_enable: true, configuration_kind: 'published-public-profile-v1' });
    expect(rows[1]).toMatchObject({ can_enable: false });
    expect(rows[1]).not.toHaveProperty('configuration_kind');
    expect(rows[2]).not.toHaveProperty('configuration_kind');
  });

  it('gets/sets only the scoped typed profile config and rejects mismatched or ambiguous evidence', async () => {
    const generation = { provision_id: 'p1', route_epoch: 'e2' };
    const signal = new AbortController().signal;
    const base = { ...generation, kind: 'published-public-profile-v1', profile_revision: 3, profile_key: null,
      eligible_profiles: [{ key: 'p', name: 'A profile' }], editable: true };
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { ...base, route_epoch: 'stale' } })
      .mockResolvedValueOnce({ data: { ...base, eligible_profiles: [...base.eligible_profiles, ...base.eligible_profiles] } })
      .mockResolvedValueOnce({ data: { ...base, full_page_url: 'https://example.test', provider_secret: 'hidden' } });
    await expect(managedChannelsApi.profileConfiguration('workspace', 'example_text', generation, signal))
      .rejects.toThrow('Invalid managed channel profile configuration');
    await expect(managedChannelsApi.profileConfiguration('workspace', 'example_text', generation, signal))
      .rejects.toThrow('Invalid managed channel profile choices');
    await expect(managedChannelsApi.profileConfiguration('workspace', 'example_text', generation, signal))
      .resolves.toEqual(base);
    expect(apiClient.get).toHaveBeenCalledWith('/agentive/managed-channels/example_text/configuration', expect.objectContaining({
      signal, params: { workspace_id: 'workspace', ...generation }, headers: { 'X-Integral-Scope': 'ws:workspace' },
    }));
    await managedChannelsApi.assignProfile('workspace', 'example_text', generation, 3, null);
    expect(apiClient.put).toHaveBeenCalledWith('/agentive/managed-channels/example_text/configuration',
      { ...generation, expected_profile_revision: 3, profile_key: null }, expect.objectContaining({
        params: { workspace_id: 'workspace' }, headers: { 'X-Integral-Scope': 'ws:workspace' },
      }));
  });

  it('treats only a paired positive integer as the current publication version', async () => {
    const generation = { provision_id: 'p1', route_epoch: 'e2' };
    const base = { ...generation, kind: 'published-public-profile-v1', profile_revision: 9,
      profile_key: 'p', eligible_profiles: [{ key: 'p', name: 'A profile' }], editable: false };
    for (const row of [
      { ...base },
      { ...base, published_version: 0 },
      { ...base, published_version: '2' },
      { ...base, published_version: 7.5 },
      { ...base, profile_key: null, published_version: 2 },
      { ...base, profile_key: 'other', published_version: 2 },
    ]) {
      vi.mocked(apiClient.get).mockResolvedValue({ data: row });
      const decoded = await managedChannelsApi.profileConfiguration('workspace', 'example_text', generation);
      expect(decoded).not.toHaveProperty('published_version');
      expect(decoded.profile_revision).toBe(9);
    }
    vi.mocked(apiClient.get).mockResolvedValue({ data: { ...base, published_version: 2 } });
    await expect(managedChannelsApi.profileConfiguration('workspace', 'example_text', generation))
      .resolves.toMatchObject({ profile_revision: 9, published_version: 2 });
  });

  it('does not turn an invalid response into a successful empty listing', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: {} });
    await expect(managedChannelsApi.list('workspace')).rejects.toThrow('Invalid managed channel listing');
  });

  it('uses only the scoped typed mutation routes and supplies exact generation values', async () => {
    const config = expect.objectContaining({
      headers: { 'X-Integral-Scope': 'ws:workspace' }, params: { workspace_id: 'workspace' },
    });
    const generation = { provision_id: 'p1', route_epoch: 'revision-2' };
    await managedChannelsApi.change('workspace', 'example_text', { kind: 'add' });
    await managedChannelsApi.change('workspace', 'example_text', { kind: 'enabled', enabled: true, generation });
    await managedChannelsApi.change('workspace', 'example_text', { kind: 'remove', generation });
    expect(apiClient.post).toHaveBeenCalledWith('/agentive/managed-channels/example_text/install', undefined, config);
    expect(apiClient.put).toHaveBeenCalledWith('/agentive/managed-channels/example_text/enabled', { enabled: true, ...generation }, config);
    expect(apiClient.delete).toHaveBeenCalledWith('/agentive/managed-channels/example_text', expect.objectContaining({
      headers: { 'X-Integral-Scope': 'ws:workspace' }, params: { workspace_id: 'workspace', ...generation },
    }));
  });
});
