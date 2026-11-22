import { describe, expect, it, vi } from 'vitest';

import { createEmbedWebChatTransport } from './embedWebChatTransport';

const origin = 'https://embed.example.invalid';
const seed = `iweb_${'b'.repeat(43)}`;
const token = `iwes_${'s'.repeat(43)}`;
const nextToken = `iwes_${'n'.repeat(43)}`;
const route = 'opaque_embed';
const base = `/api/public/web-chat-embed/${route}`;
const config = { origin, route, bootstrapSeed: seed };
const current = { id: 'reply', role: 'assistant', text: 'Literal example' };
const signal = () => new AbortController().signal;

function json(input: unknown, status = 200, cacheControl = 'no-store') {
  return new Response(JSON.stringify(input), {
    status, headers: { 'content-type': 'application/json', 'cache-control': cacheControl },
  });
}

function issue(value = token, history: unknown[] = [], seconds = 300) {
  return json({ kind: 'ready', messages: history, session_token: value, expires_in: seconds });
}

function fixture({
  framed = true,
  at = origin,
  supplied = config as unknown,
}: { framed?: boolean; at?: string; supplied?: unknown } = {}) {
  const self = {};
  const clock = { now: 1 };
  const fetcher = vi.fn<typeof fetch>();
  const browser = { self, top: framed ? {} : self, location: { origin: at }, fetch: fetcher };
  const transport = createEmbedWebChatTransport(supplied, browser, () => clock.now);
  return { fetcher, transport, clock, browser };
}

describe('detached embed visitor transport', () => {
  it('boots once, then sends and resumes using only its in-memory bearer', async () => {
    const { fetcher, transport } = fixture();
    fetcher.mockResolvedValueOnce(issue())
      .mockResolvedValueOnce(json({ kind: 'ready', messages: [current] }))
      .mockResolvedValueOnce(issue(token, [current]));
    const init = signal();
    expect(await transport?.start({ mode: 'resume', signal: init })).toEqual({ kind: 'ready', messages: [] });
    expect(fetcher).toHaveBeenNthCalledWith(1, `${base}/session`, {
      method: 'POST', credentials: 'omit', mode: 'same-origin', cache: 'no-store', redirect: 'error',
      referrerPolicy: 'no-referrer', signal: init,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ mode: 'resume', bootstrap_seed: seed }),
    });
    expect(await transport?.send({ id: 'event_1', text: 'Question', signal: init }))
      .toEqual({ kind: 'ready', messages: [current] });
    expect(fetcher).toHaveBeenNthCalledWith(2, `${base}/messages`, expect.objectContaining({
      credentials: 'omit', mode: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: `Bearer ${token}` },
      body: '{"id":"event_1","text":"Question"}',
    }));
    expect(await transport?.start({ mode: 'resume', signal: init })).toEqual({ kind: 'ready', messages: [current] });
    expect(fetcher).toHaveBeenNthCalledWith(3, `${base}/session`, expect.objectContaining({
      credentials: 'omit', mode: 'same-origin',
      headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
      body: '{"mode":"resume"}',
    }));
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      `${base}/session`, `${base}/messages`, `${base}/session`,
    ]);
  });

  it.each([
    { framed: false },
    { at: 'https://parent.example.invalid' },
    { supplied: { ...config, origin: 'https://embed.example.invalid/extra' } },
    { supplied: { ...config, origin: 'https://embed.example.invalid:443' } },
    { supplied: { ...config, origin: 'https://localhost' }, at: 'https://localhost' },
    { supplied: { ...config, route: 'other/route' } },
    { supplied: { ...config, route: 'other?token=x' } },
    { supplied: { ...config, route: token } },
    { supplied: { ...config, bootstrapSeed: '' } },
    { supplied: { ...config, bootstrapSeed: seed.replace('iweb_', 'iwcs_') } },
    { supplied: { ...config, session_token: token } },
    { supplied: {} },
  ])('rejects absent, invalid or top-level host context without I/O (%j)', options => {
    const { fetcher, transport } = fixture(options);
    expect(transport).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('never replays a spent bootstrap after a network fault and sends no bearerless turns', async () => {
    const { fetcher, transport } = fixture();
    fetcher.mockRejectedValueOnce(new Error(`must not surface ${seed}`));
    const beforeBoot = await transport?.send({ id: 'event', text: 'Message', signal: signal() });
    expect(beforeBoot).toEqual({ kind: 'expired' });
    await expect(transport?.start({ mode: 'resume', signal: signal() }))
      .rejects.toThrow('Embedded Web Chat request could not be completed');
    expect(await transport?.start({ mode: 'new', signal: signal() })).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('ignores a successful response delivered after its owner aborts the bootstrap', async () => {
    const { fetcher, transport } = fixture();
    let release!: () => void;
    fetcher.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve(issue()); }));
    const controller = new AbortController();
    const pending = transport?.start({ mode: 'resume', signal: controller.signal });
    controller.abort();
    release();
    await expect(pending).rejects.toThrow('Embedded Web Chat request could not be completed');
    expect(await transport?.send({ id: 'event', text: 'Message', signal: signal() })).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('rotates New and cannot continue by retrying an uncertain rotation', async () => {
    const { fetcher, transport } = fixture();
    fetcher.mockResolvedValueOnce(issue()).mockResolvedValueOnce(issue(nextToken))
      .mockRejectedValueOnce(new Error('ambiguous New fixture'));
    expect(await transport?.start({ mode: 'resume', signal: signal() })).toEqual({ kind: 'ready', messages: [] });
    expect(await transport?.start({ mode: 'new', signal: signal() })).toEqual({ kind: 'ready', messages: [] });
    expect(fetcher).toHaveBeenNthCalledWith(2, `${base}/session`, expect.objectContaining({
      headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
      body: '{"mode":"new"}',
    }));
    await expect(transport?.start({ mode: 'new', signal: signal() })).rejects.toThrow();
    expect(fetcher).toHaveBeenNthCalledWith(3, `${base}/session`, expect.objectContaining({
      headers: expect.objectContaining({ Authorization: `Bearer ${nextToken}` }),
      body: '{"mode":"new"}',
    }));
    expect(await transport?.start({ mode: 'resume', signal: signal() })).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it.each([
    () => issue('iwcs_' + 't'.repeat(43)),
    () => issue(token, [current]),
    () => issue(token, [], 301),
    () => issue(token, [], 0),
    () => json({ kind: 'ready', session_token: token, expires_in: 5, messages: [] }, 200, 'max-age=60'),
    () => json({ kind: 'ready', session_token: token, expires_in: 5, messages: [], workspace: 'private' }),
  ])('rejects wrong token type, nonempty bootstrap, bad TTL, caching or excess fields', async response => {
    const { fetcher, transport } = fixture();
    fetcher.mockResolvedValueOnce(response());
    await expect(transport?.start({ mode: 'resume', signal: signal() })).rejects.toThrow();
    expect(await transport?.send({ id: 'event', text: 'Message', signal: signal() })).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('keeps exact event retries for pending turns without leaking credentials into replies', async () => {
    const { fetcher, transport } = fixture();
    fetcher.mockResolvedValueOnce(issue()).mockResolvedValueOnce(json({ kind: 'pending' }))
      .mockResolvedValueOnce(json({ kind: 'ready', messages: [{ ...current, text: `Do not show ${token}` }] }));
    await transport?.start({ mode: 'resume', signal: signal() });
    const attempt = { id: 'repeat', text: 'Same attempted text', signal: signal() };
    expect(await transport?.send(attempt)).toEqual({ kind: 'pending' });
    await expect(transport?.send(attempt)).rejects.toThrow('Embedded Web Chat request could not be completed');
    const [, one, two] = fetcher.mock.calls;
    expect(one).toEqual(two);
    await expect(transport?.send({ ...attempt, text: seed })).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it.each([401, 403, 410])('clears memory after server rejection (%d)', async status => {
    const { fetcher, transport } = fixture();
    fetcher.mockResolvedValueOnce(issue()).mockResolvedValueOnce(json({ detail: 'private' }, status));
    await transport?.start({ mode: 'resume', signal: signal() });
    const attempt = { id: 'event', text: 'Message', signal: signal() };
    expect(await transport?.send(attempt)).toEqual({ kind: status === 403 ? 'unavailable' : 'expired' });
    expect(await transport?.send(attempt)).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('drops an expired in-memory token without network, and a new document needs its own seed', async () => {
    const { fetcher, transport, clock } = fixture();
    fetcher.mockResolvedValueOnce(issue(token, [], 1));
    await transport?.start({ mode: 'resume', signal: signal() });
    clock.now += 1001;
    expect(await transport?.send({ id: 'event', text: 'Message', signal: signal() })).toEqual({ kind: 'expired' });
    expect(await transport?.start({ mode: 'resume', signal: signal() })).toEqual({ kind: 'expired' });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const reloaded = fixture({ supplied: { ...config, bootstrapSeed: null } });
    expect(reloaded.transport).toBeNull();
    expect(reloaded.fetcher).not.toHaveBeenCalled();
  });
});
