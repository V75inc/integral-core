import { expect, it, vi } from 'vitest';

import apiClient from '../client';
import { connectionProfilesApi } from '../connectionProfiles';

vi.mock('../client', () => ({ default: { post: vi.fn() } }));

it('uses the existing Publish contract without the opt-in by default, and sends only explicit true', async () => {
  vi.mocked(apiClient.post).mockResolvedValue({ data: { revision: 3 } });
  await connectionProfilesApi.publish('workspace', 2, 'public', false, []);
  await connectionProfilesApi.publish('workspace', 2, 'public', false, [], true);
  const body = { expected_revision: 2, key: 'public', offer_in_workspace_chat: false, connection_ids: [] };
  const scope = { headers: { 'X-Integral-Scope': 'ws:workspace' } };
  expect(apiClient.post).toHaveBeenNthCalledWith(1, '/workspace/agent-profiles/publish', body, scope);
  expect(apiClient.post).toHaveBeenNthCalledWith(
    2, '/workspace/agent-profiles/publish', { ...body, enable_public_service_identity: true }, scope);
});
