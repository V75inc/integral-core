import { beforeEach, describe, expect, it, vi } from 'vitest';

import apiClient, { getActiveScopeHeader } from '../../../../api/client';
import { getAccessToken, getSessionGeneration } from '../../../../api/session';
import { readManagedChannelShare } from '../managedChannelShareReader';
import type { ManagedShareOwner } from '../managedChannelShareProof';

vi.mock('../../../../api/client', () => ({ default: { get: vi.fn() }, getActiveScopeHeader: vi.fn() }));
vi.mock('../../../../api/session', () => ({ getAccessToken: vi.fn(), getSessionGeneration: vi.fn() }));

const owner: ManagedShareOwner = {
  principalId: 'manager', workspaceId: 'shared', sessionFence: '3', slug: 'synthetic',
  provisionId: 'installed', routeEpoch: 'epoch',
  publication: { profileKey: 'public', publishedVersion: 5 },
};
const response = {
  data: { kind: 'synthetic-projection-fixture' },
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getActiveScopeHeader).mockReturnValue('ws:shared');
  vi.mocked(getAccessToken).mockReturnValue('synthetic-session-fixture');
  vi.mocked(getSessionGeneration).mockReturnValue(3);
  vi.mocked(apiClient.get).mockResolvedValue(response);
});

describe('detached authenticated share reader', () => {
  it('uses only one no-cache read over the canonical manager-scoped transport', async () => {
    const signal = new AbortController().signal;
    await expect(readManagedChannelShare(owner, signal)).resolves.toEqual(response.data);
    expect(apiClient.get).toHaveBeenCalledExactlyOnceWith('/agentive/managed-channels/synthetic/share',
      expect.objectContaining({
        params: { workspace_id: 'shared', provision_id: 'installed', route_epoch: 'epoch',
          profile_key: 'public', published_version: 5 },
        headers: { 'X-Integral-Scope': 'ws:shared', 'Cache-Control': 'no-cache, no-store', Accept: 'application/json' },
        signal,
      }));
    expect(JSON.stringify(vi.mocked(apiClient.get).mock.calls)).not.toMatch(/synthetic-session-fixture|local-session|principalId/);
  });

  it('never requests an invalid/default workspace, credentialless read, or invalid slug', async () => {
    const signal = new AbortController().signal;
    await expect(readManagedChannelShare({ ...owner, workspaceId: 'another' }, signal)).rejects.toThrow();
    await expect(readManagedChannelShare({ ...owner, sessionFence: 'obsolete-session' }, signal)).rejects.toThrow();
    await expect(readManagedChannelShare({ ...owner, slug: 'other/path' }, signal)).rejects.toThrow();
    await expect(readManagedChannelShare({ ...owner, publication: { ...owner.publication, publishedVersion: 0 } }, signal))
      .rejects.toThrow();
    vi.mocked(getAccessToken).mockReturnValue(null);
    await expect(readManagedChannelShare(owner, signal)).rejects.toThrow();
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it.each([
    () => vi.mocked(getSessionGeneration).mockReturnValue(4),
    () => vi.mocked(getAccessToken).mockReturnValue('another-synthetic-session'),
    () => vi.mocked(getActiveScopeHeader).mockReturnValue('ws:other'),
    (controller: AbortController) => controller.abort(),
  ])('rejects a response after credential, generation, scope or cancellation changes', async mutate => {
    let finish!: () => void;
    vi.mocked(apiClient.get).mockImplementation(() => new Promise(resolve => { finish = () => resolve(response); }));
    const controller = new AbortController();
    const result = readManagedChannelShare(owner, controller.signal);
    mutate(controller);
    finish();
    await expect(result).rejects.toThrow('Full-page sharing could not be verified');
  });

  it.each([
    { 'content-type': 'application/json', 'cache-control': 'public, max-age=60' },
    { 'content-type': 'text/html', 'cache-control': 'no-store' },
    { 'content-type': 'application/json' },
  ])('rejects unexpected or cacheable responses', async headers => {
    vi.mocked(apiClient.get).mockResolvedValue({ ...response, headers });
    await expect(readManagedChannelShare(owner, new AbortController().signal)).rejects.toThrow();
  });
});
