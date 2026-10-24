import { afterEach, describe, expect, it, vi } from 'vitest';
import client from '../client';
import { feedApi } from '../feed';
vi.mock('../client', () => ({ default: { get: vi.fn() } }));
vi.mock('../helpers', () => ({
  hydrateEntryTagsForList: vi.fn(), normalizeEntry: vi.fn(),
  unwrapApiDataEnvelope: (data: unknown) => data, toArr: () => [],
}));
afterEach(() => vi.clearAllMocks());
describe('feed compatibility fallback', () => {
  it.each([429, 500, 503])('does not replay a %s against another endpoint', async status => {
    const error = { response: { status } };
    vi.mocked(client.get).mockRejectedValueOnce(error);
    await expect(feedApi.getEntriesPage({ entry_ids: 'one,two' })).rejects.toBe(error);
    expect(client.get).toHaveBeenCalledTimes(1);
  });
  it('retains fallback for an unavailable route', async () => {
    vi.mocked(client.get).mockRejectedValueOnce({ response: { status: 404 } }).mockResolvedValueOnce({ data: { entries: [], has_more: false } });
    await expect(feedApi.getEntriesPage()).resolves.toMatchObject({ entries: [] });
    expect(client.get).toHaveBeenCalledTimes(2);
  });
});

it('does not replay a ranked selection against an older route without that contract', async () => {
  const error = { response: { status: 404 } };
  vi.mocked(client.get).mockRejectedValueOnce(error);
  await expect(feedApi.getEntriesPage({ entry_ids: 'one,two' })).rejects.toBe(error);
  expect(client.get).toHaveBeenCalledTimes(1);
});
