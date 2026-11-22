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
