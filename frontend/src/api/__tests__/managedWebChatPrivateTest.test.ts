import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { getActiveScopeHeader } from '../client';
import { runManagedWebChatPrivateTest } from '../managedWebChatPrivateTest';
import { getAccessToken, getSessionGeneration } from '../session';

vi.mock('../client', () => ({ getActiveScopeHeader: vi.fn() }));
vi.mock('../session', () => ({ getAccessToken: vi.fn(), getSessionGeneration: vi.fn() }));
vi.mock('../../config', () => ({ getApiBaseURL: () => '/api' }));

const owner = { principalId: 'manager', workspaceId: 'workspace', sessionFence: '3',
  provisionId: 'provision', routeEpoch: '4', profileKey: 'profile', publishedVersion: 5 };
const exact = { kind: 'private-web-chat-test-v1', workspace_id: 'workspace', slug: 'public_web_chat',
  provision_id: 'provision', route_epoch: '4', profile_key: 'profile', published_version: 5, reply: '<b>Literal reply</b>' };
const fetcher = vi.fn<typeof fetch>();
const signal = () => new AbortController().signal;
const headers = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, no-store' };
const response = (body: unknown, status = 200, responseHeaders: HeadersInit = headers) =>
  new Response(JSON.stringify(body), { headers: responseHeaders, status });

beforeEach(() => {
  fetcher.mockReset();
  vi.stubGlobal('fetch', fetcher);
  vi.mocked(getActiveScopeHeader).mockReturnValue('ws:workspace');
  vi.mocked(getAccessToken).mockReturnValue('fake-local-token-marker');
  vi.mocked(getSessionGeneration).mockReturnValue(3);
});

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('sends exactly once to private manager scope and returns only an exact no-store text reply', async () => {
  fetcher.mockResolvedValue(response(exact));
  await expect(runManagedWebChatPrivateTest(owner, '  hello  ', signal())).resolves.toBe(exact.reply);
  expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/agentive/managed-channels/public_web_chat/test?workspace_id=workspace', {
    method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
    headers: { Authorization: 'Bearer fake-local-token-marker', 'X-Integral-Scope': 'ws:workspace',
      'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ provision_id: 'provision', route_epoch: '4', profile_key: 'profile', published_version: 5, text: 'hello' }),
    signal: expect.any(AbortSignal),
  });
});

it('refuses stale owner, workspace, session, and unsupported message input without a request', async () => {
  for (const candidate of [{ ...owner, workspaceId: 'other' }, { ...owner, sessionFence: '2' },
    { ...owner, routeEpoch: '012' }, { ...owner, publishedVersion: 0 }, { ...owner, profileKey: '' }]) {
    await expect(runManagedWebChatPrivateTest(candidate, 'hello', signal())).rejects.toThrow(/Could not verify/);
  }
  for (const text of ['', 'x'.repeat(1001), `iweb_${'x'.repeat(43)}`, 'test\u0000']) {
    await expect(runManagedWebChatPrivateTest(owner, text, signal())).rejects.toThrow(/Could not verify/);
  }
  const cancelled = new AbortController();
  cancelled.abort();
  await expect(runManagedWebChatPrivateTest(owner, 'hello', cancelled.signal)).rejects.toThrow(/Could not verify/);
  vi.mocked(getAccessToken).mockReturnValue(null);
  await expect(runManagedWebChatPrivateTest(owner, 'hello', signal())).rejects.toThrow(/Could not verify/);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  () => response({ kind: 'unavailable' }, 401),
  () => response({ kind: 'unavailable' }, 403),
  () => response({ kind: 'unavailable' }, 404),
  () => response({ kind: 'unavailable' }, 429),
  () => response({ kind: 'unavailable' }, 503),
  () => response(exact, 200, { 'Content-Type': 'application/json' }),
  () => response(exact, 200, { ...headers, 'Content-Type': 'text/html' }),
  () => response({ ...exact, route_epoch: 'old' }),
  () => response({ ...exact, published_version: 6 }),
  () => response({ ...exact, workspace_id: 'different-workspace' }),
  () => response({ ...exact, reply: '' }),
  () => response({ ...exact, reply: 'x'.repeat(4001) }),
  () => response({ ...exact, reply: `iwes_${'x'.repeat(43)}` }),
  () => response({ ...exact, public_url: 'https://not-a-reply.example.com' }),
])('rejects denied, stale, cacheable, or expanded responses without replay', async reply => {
  fetcher.mockResolvedValue(reply());
  await expect(runManagedWebChatPrivateTest(owner, 'hello', signal())).rejects.toThrow('Could not verify the private Web Chat test');
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  (_controller: AbortController) => vi.mocked(getSessionGeneration).mockReturnValue(4),
  () => vi.mocked(getAccessToken).mockReturnValue('another'),
  () => vi.mocked(getActiveScopeHeader).mockReturnValue('ws:other'),
  (controller: AbortController) => controller.abort(),
])('drops late replies after session, token, workspace, or cancellation changes', async mutate => {
  let finish!: () => void;
  const controller = new AbortController();
  fetcher.mockImplementation(() => new Promise(resolve => { finish = () => resolve(response(exact)); }));
  const pending = runManagedWebChatPrivateTest(owner, 'hello', controller.signal);
  mutate(controller);
  finish();
  await expect(pending).rejects.toThrow(/Could not verify/);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('aborts the transport by a bounded deadline, instead of retrying', async () => {
  vi.useFakeTimers();
  let requestSignal!: AbortSignal;
  fetcher.mockImplementation((_url, settings) => new Promise((_resolve, reject) => {
    requestSignal = settings!.signal!;
    requestSignal.addEventListener('abort', () => reject(new Error('synthetic timeout')), { once: true });
  }));
  const pending = expect(runManagedWebChatPrivateTest(owner, 'hello', signal())).rejects.toThrow(/Could not verify/);
  await vi.advanceTimersByTimeAsync(18_000);
  await pending;
  expect(requestSignal.aborted).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
