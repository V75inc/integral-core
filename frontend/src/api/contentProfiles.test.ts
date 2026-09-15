import { describe, expect, it, vi, beforeEach } from 'vitest';

// Mock the API client module before importing contentProfilesApi.
vi.mock('./client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn(),
  },
}));

import apiClient from './client';
import { contentProfilesApi } from './contentProfiles';

describe('contentProfilesApi.delete', () => {
  beforeEach(() => vi.clearAllMocks());

  it('calls DELETE /content-profiles/{id} with the given id', async () => {
    await contentProfilesApi.delete('cp-abc-123');
    expect(apiClient.delete).toHaveBeenCalledWith('/content-profiles/cp-abc-123');
  });

  it('returns without throwing on success', async () => {
    await expect(contentProfilesApi.delete('cp-abc-123')).resolves.not.toThrow();
  });
});
