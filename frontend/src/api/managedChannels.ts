import apiClient from './client';

interface ManagedChannelDefinition {
  slug: string;
  provider: string;
  display_name: string;
  description: string;
  setup_kind: 'managed-text-v1';
  version: 1;
}

interface ManagedChannelGeneration {
  provision_id: string;
  route_epoch: string;
}

export type ManagedChannel =
  | {
      definition: ManagedChannelDefinition;
      state: 'registered';
      connected: false;
      action: 'add';
      can_install: boolean;
    }
  | (ManagedChannelGeneration & {
      definition: ManagedChannelDefinition;
      state: 'inactive' | 'unavailable' | 'active';
      connected: true;
      action: 'configure';
      can_enable: boolean;
    });

export type ManagedChannelChange =
  | { kind: 'add' }
  | { kind: 'enabled'; enabled: boolean; generation: ManagedChannelGeneration }
  | { kind: 'remove'; generation: ManagedChannelGeneration };

const path = '/agentive/managed-channels';
const config = (workspaceId: string) => ({
  headers: { 'X-Integral-Scope': `ws:${workspaceId}` },
  params: { workspace_id: workspaceId },
  __suppressSystemNotify: true,
});

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

/** Only the supported, inert setup descriptor and a consistent server projection can offer actions. */
function decode(value: unknown): ManagedChannel | undefined {
  const row = record(value);
  const d = record(row?.definition);
  if (!row || !d || !nonempty(d.slug) || !/^[a-z][a-z0-9_]{0,63}$/.test(d.slug) ||
      !nonempty(d.provider) || !nonempty(d.display_name) || !nonempty(d.description) ||
      d.setup_kind !== 'managed-text-v1' || d.version !== 1) return;
  const definition: ManagedChannelDefinition = {
    slug: d.slug, provider: d.provider, display_name: d.display_name,
    description: d.description, setup_kind: d.setup_kind, version: d.version,
  };
  if (row.connected === false && row.state === 'registered' && row.action === 'add' &&
      row.provision_id == null && row.route_epoch == null) {
    return { definition, connected: false, state: 'registered', action: 'add', can_install: row.can_install === true };
  }
  if (row.connected === true && row.action === 'configure' &&
      (row.state === 'inactive' || row.state === 'active' || row.state === 'unavailable') &&
      nonempty(row.provision_id) && nonempty(row.route_epoch)) {
    return {
      definition, connected: true, state: row.state, action: 'configure',
      can_enable: row.can_enable === true && row.state === 'inactive',
      provision_id: row.provision_id, route_epoch: row.route_epoch,
    };
  }
}

function decodeListing(value: unknown): ManagedChannel[] {
  const channels = record(value)?.channels;
  if (!Array.isArray(channels)) throw new Error('Invalid managed channel listing');
  // A repeated identity is ambiguous, even if one declaration is unsupported.
  const counts = new Map<string, number>();
  for (const item of channels) {
    const slug = record(record(item)?.definition)?.slug;
    if (typeof slug === 'string') counts.set(slug, (counts.get(slug) ?? 0) + 1);
  }
  return channels.flatMap(item => {
    const channel = decode(item);
    return channel && counts.get(channel.definition.slug) === 1 ? [channel] : [];
  });
}

export const managedChannelsApi = {
  async list(workspaceId: string, signal?: AbortSignal): Promise<ManagedChannel[]> {
    const response = await apiClient.get<unknown>(path, { ...config(workspaceId), signal });
    return decodeListing(response.data);
  },
  async change(workspaceId: string, slug: string, change: ManagedChannelChange): Promise<void> {
    const url = `${path}/${encodeURIComponent(slug)}`;
    const options = config(workspaceId);
    if (change.kind === 'add') {
      await apiClient.post(`${url}/install`, undefined, options);
    } else if (change.kind === 'enabled') {
      await apiClient.put(`${url}/enabled`, { enabled: change.enabled, ...change.generation }, options);
    } else {
      await apiClient.delete(url, { ...options, params: { ...options.params, ...change.generation } });
    }
  },
};
