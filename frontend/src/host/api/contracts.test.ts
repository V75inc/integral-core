import { beforeEach, describe, expect, it, vi } from 'vitest';
import { memberContractApi, publicContractApi } from './contracts';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../api/client', () => ({ default: client }));

describe('Business public contract API adapter', () => {
  beforeEach(() => vi.resetAllMocks());

  it('loads member metadata with the selected assigned entry', async () => {
    const data = { update_available: true, template_id: 'template' };
    client.get.mockResolvedValue({ data });
    await expect(memberContractApi.fetchContractMeta('entry/a & b')).resolves.toBe(data);
    expect(client.get).toHaveBeenCalledWith('/me/assigned-form/contract/meta', {
      params: { entry: 'entry/a & b' },
    });
  });

  it('preserves member metadata authorization failures', async () => {
    const error = new Error('Access denied');
    client.get.mockRejectedValue(error);
    await expect(memberContractApi.fetchContractMeta('entry')).rejects.toBe(error);
  });

  it('encodes the scoped route and requests PDF bytes', async () => {
    const blob = new Blob(['%PDF']);
    client.get.mockResolvedValue({ data: blob });
    expect(await publicContractApi.fetchOnboardingContractBlob('token/a', 'entry/b', 7)).toBe(blob);
    expect(client.get).toHaveBeenCalledWith('/public-share/track/token%2Fa/entries/entry%2Fb/contract', {
      responseType: 'blob', params: { v: 7 },
    });
  });

  it('forwards regeneration intent to the Business endpoint', async () => {
    client.post.mockResolvedValue({ data: { contract_status: 'generated' } });
    await publicContractApi.generateOnboardingContract('token', 'entry', { force: true });
    expect(client.post).toHaveBeenCalledWith('/public-share/track/token/entries/entry/contract/generate', undefined, { params: { force: '1' } });
  });

  it('preserves the decision payload', async () => {
    const body = { action: 'reject' as const, reason: 'Synthetic test' };
    client.post.mockResolvedValue({ data: { contract_status: 'rejected' } });
    await publicContractApi.decideOnboardingContract('token', 'entry', body);
    expect(client.post).toHaveBeenCalledWith('/public-share/track/token/entries/entry/contract/decision', body);
  });

  it('loads contract template update metadata', async () => {
    client.get.mockResolvedValue({ data: { update_available: true } });
    await expect(publicContractApi.fetchOnboardingContractMeta('token', 'entry')).resolves.toEqual({
      update_available: true,
    });
    expect(client.get).toHaveBeenCalledWith('/public-share/track/token/entries/entry/contract/meta');
  });
});
