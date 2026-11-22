import { beforeEach, describe, expect, it, vi } from 'vitest';

import apiClient, { getActiveScopeHeader } from '../../../../api/client';
import { getAccessToken, getSessionGeneration } from '../../../../api/session';
import { currentManagedEmbedSnippet, type ManagedEmbedOwner } from '../managedChannelEmbedProof';
import { readManagedChannelEmbedShare } from '../managedChannelEmbedShareReader';

vi.mock('../../../../api/client', () => ({ default: { get: vi.fn() }, getActiveScopeHeader: vi.fn() }));
vi.mock('../../../../api/session', () => ({ getAccessToken: vi.fn(), getSessionGeneration: vi.fn() }));

const approvedUrl = 'https://isolated.example.com/embed/fictional';
const privateToken = 'synthetic-local-token-fixture';

function owner(): ManagedEmbedOwner {
  return {
    principalId: 'synthetic-manager', sessionFence: '8', workspaceId: 'shared',
    slug: 'public_web_chat', provisionId: 'installed', routeEpoch: '23',
    publication: { profileKey: 'public', publishedVersion: 4 },
    policy: { parentOrigin: 'https://publisher.example.com', policyRevision: 6 },
  };
}

const params = {
  workspace_id: 'shared', provision_id: 'installed', route_epoch: '23',
  profile_key: 'public', published_version: 4,
  embed_policy_revision: 6, parent_origin: 'https://publisher.example.com',
};

const wire = { kind: 'approved-web-chat-embed-v1', slug: 'public_web_chat',
  ...params, embed_page_url: approvedUrl };
const headers = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' };
const response = { status: 200, headers, data: wire };
const unavailable = { state: 'unavailable' };
const signal = () => new AbortController().signal;

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getAccessToken).mockReturnValue(privateToken);
  vi.mocked(getSessionGeneration).mockReturnValue(8);
  vi.mocked(getActiveScopeHeader).mockReturnValue('ws:shared');
  vi.mocked(apiClient.get).mockResolvedValue(response);
});

describe('detached authenticated embed-share reader', () => {
  it('requires the canonical authentication lane and binds exact policy in a quiet read', async () => {
    const request = owner();
    const abortSignal = signal();
    const proof = await readManagedChannelEmbedShare(request, abortSignal);
    expect(apiClient.get).toHaveBeenCalledExactlyOnceWith(
      '/agentive/managed-channels/public_web_chat/embed-share', {
        params,
        headers: { 'X-Integral-Scope': 'ws:shared', 'Cache-Control': 'no-cache, no-store', Accept: 'application/json' },
        signal: abortSignal,
        timeout: 5000,
        __suppressSystemNotify: true,
      });
    expect(JSON.stringify(vi.mocked(apiClient.get).mock.calls)).not.toMatch(/synthetic-manager|synthetic-local-token|sessionFence/);
    expect(currentManagedEmbedSnippet(proof, request)).toContain(approvedUrl);
    expect(currentManagedEmbedSnippet(proof, owner())).toContain(params.parent_origin);
    expect(currentManagedEmbedSnippet(proof, { ...request, sessionFence: '9' })).toBeNull();
    expect(currentManagedEmbedSnippet(proof, {
      ...request, policy: { ...request.policy, policyRevision: 7 },
    })).toBeNull();
  });

  it.each([
    (value: ManagedEmbedOwner) => { value.sessionFence = '7'; },
    (value: ManagedEmbedOwner) => { value.principalId = ''; },
    (value: ManagedEmbedOwner) => { value.workspaceId = 'other'; },
    (value: ManagedEmbedOwner) => { value.slug = 'another_connector'; },
    (value: ManagedEmbedOwner) => { value.provisionId = 'spaced\nidentity'; },
    (value: ManagedEmbedOwner) => { value.routeEpoch = '023'; },
    (value: ManagedEmbedOwner) => { value.publication.publishedVersion = 0; },
    (value: ManagedEmbedOwner) => { value.publication.profileKey = 'p'.repeat(129); },
    (value: ManagedEmbedOwner) => { value.policy.policyRevision = 0; },
    (value: ManagedEmbedOwner) => { value.policy.parentOrigin = 'https://other.example.com/private'; },
    (value: ManagedEmbedOwner) => { value.policy.parentOrigin = 'https://127.0.0.1'; },
  ])('does not send a request with absent, stale, malformed or other owner authority', async mutate => {
    const request = owner();
    mutate(request);
    await expect(readManagedChannelEmbedShare(request, signal())).resolves.toEqual(unavailable);
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it('does not read without an active token or after abort', async () => {
    const cancelled = new AbortController();
    cancelled.abort();
    await expect(readManagedChannelEmbedShare(owner(), cancelled.signal)).resolves.toEqual(unavailable);
    vi.mocked(getAccessToken).mockReturnValue(null);
    await expect(readManagedChannelEmbedShare(owner(), signal())).resolves.toEqual(unavailable);
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  const changed: Array<(request: ManagedEmbedOwner, controller: AbortController) => void> = [
    (_request, controller) => controller.abort(),
    () => vi.mocked(getSessionGeneration).mockReturnValue(9),
    () => vi.mocked(getAccessToken).mockReturnValue('rotated-or-other-account'),
    () => vi.mocked(getActiveScopeHeader).mockReturnValue('ws:other'),
    request => { request.principalId = 'changed-principal'; },
    request => { request.publication.publishedVersion = 5; },
    request => { request.policy.policyRevision = 7; },
    request => { request.policy.parentOrigin = 'https://other.example.com'; },
  ];

  it.each(changed)('discards a late reply when authorization or the exact request owner changes', async mutate => {
    let release!: () => void;
    vi.mocked(apiClient.get).mockImplementation(() => new Promise(resolve => {
      release = () => resolve(response);
    }));
    const request = owner();
    const abort = new AbortController();
    const result = readManagedChannelEmbedShare(request, abort.signal);
    mutate(request, abort);
    release();
    await expect(result).resolves.toEqual(unavailable);
  });

  it.each([
    { ...response, status: 203 },
    { ...response, headers: { ...headers, 'content-type': 'text/html' } },
    { ...response, headers: { 'content-type': 'application/json' } },
    { ...response, headers: { ...headers, 'cache-control': 'private, max-age=30' } },
    { ...response, data: { ...wire, published_version: 5 } },
    { ...response, data: { ...wire, embed_policy_revision: 7 } },
    { ...response, data: { ...wire, parent_origin: 'https://other.example.com' } },
    { ...response, data: { ...wire, embed_page_url: 'https://isolated.example.com/chat/fictional' } },
    { ...response, data: { ...wire, embed_page_url: 'https://isolated.example.com/embed/fictional?private=value' } },
    { ...response, data: { ...wire, internal: privateToken } },
  ])('rejects cacheable, malformed, unrelated or unapproved projections', async result => {
    vi.mocked(apiClient.get).mockResolvedValue(result);
    await expect(readManagedChannelEmbedShare(owner(), signal())).resolves.toEqual(unavailable);
  });

  it('redacts transport exceptions and never returns the raw body', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(new Error(privateToken));
    await expect(readManagedChannelEmbedShare(owner(), signal())).resolves.toEqual(unavailable);
  });
});
