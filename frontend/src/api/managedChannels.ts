import apiClient from './client';

interface ManagedChannelDefinition {
  slug: string;
  provider: string;
  display_name: string;
  description: string;
  setup_kind: 'managed-text-v1';
  version: 1;
}

export interface ManagedChannelGeneration {
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
      configuration_kind?: 'published-public-profile-v1';
    });

export interface ManagedChannelProfileConfiguration extends ManagedChannelGeneration {
  kind: 'published-public-profile-v1';
  profile_revision: number;
  published_version?: number;
  full_page_share_check_available?: true;
  profile_key: string | null;
  eligible_profiles: Array<{key: string; name: string}>;
  editable: boolean;
}

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
      can_enable: row.can_enable === true && row.state === 'inactive' &&
        (row.configuration_kind == null || row.configuration_kind === 'published-public-profile-v1'),
      provision_id: row.provision_id, route_epoch: row.route_epoch,
      ...(row.configuration_kind === 'published-public-profile-v1'
        ? { configuration_kind: row.configuration_kind } : {}),
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

function decodeProfileConfiguration(value: unknown, slug: string, generation: ManagedChannelGeneration): ManagedChannelProfileConfiguration {
  const row = record(value);
  if (!row || row.kind !== 'published-public-profile-v1' ||
      row.provision_id !== generation.provision_id || row.route_epoch !== generation.route_epoch ||
      typeof row.profile_revision !== 'number' || !Number.isSafeInteger(row.profile_revision) || row.profile_revision < 0 ||
      (row.profile_key !== null && !nonempty(row.profile_key)) || typeof row.editable !== 'boolean' ||
      !Array.isArray(row.eligible_profiles)) {
    throw new Error('Invalid managed channel profile configuration');
  }
  const profiles = new Map<string, {key: string; name: string}>();
  for (const item of row.eligible_profiles) {
    const choice = record(item);
    if (!choice || !nonempty(choice.key) || !nonempty(choice.name) || profiles.has(choice.key)) {
      throw new Error('Invalid managed channel profile choices');
    }
    profiles.set(choice.key, { key: choice.key, name: choice.name });
  }
  const publishedVersion = row.profile_key !== null && profiles.has(row.profile_key) &&
    typeof row.published_version === 'number' && Number.isSafeInteger(row.published_version) &&
    row.published_version > 0 ? row.published_version : undefined;
  return {
    kind: row.kind,
    profile_revision: row.profile_revision,
    profile_key: row.profile_key,
    ...(publishedVersion !== undefined ? { published_version: publishedVersion } : {}),
    ...(slug === 'public_web_chat' && publishedVersion !== undefined && !row.editable && row.full_page_share_check_available === true
      ? { full_page_share_check_available: true as const } : {}),
    eligible_profiles: [...profiles.values()],
    editable: row.editable,
    ...generation,
  };
}

function configurationPath(slug: string): string {
  return `${path}/${encodeURIComponent(slug)}/configuration`;
}

export const managedChannelsApi = {
  async list(workspaceId: string, signal?: AbortSignal): Promise<ManagedChannel[]> {
    const response = await apiClient.get<unknown>(path, { ...config(workspaceId), signal });
    return decodeListing(response.data);
  },
  async profileConfiguration(workspaceId: string, slug: string, generation: ManagedChannelGeneration,
    signal?: AbortSignal): Promise<ManagedChannelProfileConfiguration> {
    const options = config(workspaceId);
    const response = await apiClient.get<unknown>(configurationPath(slug), {
      ...options, params: { ...options.params, ...generation }, signal,
    });
    return decodeProfileConfiguration(response.data, slug, generation);
  },
  async assignProfile(workspaceId: string, slug: string, generation: ManagedChannelGeneration,
    expectedProfileRevision: number, profileKey: string | null): Promise<void> {
    await apiClient.put(configurationPath(slug), {
      ...generation, expected_profile_revision: expectedProfileRevision, profile_key: profileKey,
    }, config(workspaceId));
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
