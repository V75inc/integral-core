import { expect, it, vi } from 'vitest';

import { createBrowserWebChatTransport } from './browserWebChatTransport';

const signal = new AbortController().signal;
const reply = (value: unknown) => Response.json(value);

it('uses only the same-origin route and sends no identity or credential in JSON', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(reply({ kind: 'ready', messages: [] }))
    .mockResolvedValueOnce(reply({ kind: 'pending' }));
  const transport = createBrowserWebChatTransport('route_1', fetcher);
  await expect(transport.start({ mode: 'resume', signal })).resolves.toEqual({ kind: 'ready', messages: [] });
  await expect(transport.send({ id: 'event-1', text: 'Hello', signal })).resolves.toEqual({ kind: 'pending' });
  expect(fetcher).toHaveBeenNthCalledWith(1, '/api/public/web-chat/route_1/session', {
    method: 'POST', credentials: 'same-origin', mode: 'same-origin', redirect: 'error',
    cache: 'no-store', headers: { 'content-type': 'application/json' }, body: '{"mode":"resume"}', signal,
  });
  expect(fetcher).toHaveBeenNthCalledWith(2, '/api/public/web-chat/route_1/messages', expect.objectContaining({
    body: '{"id":"event-1","text":"Hello"}', signal,
  }));
});

it.each(['', 'route\n', '..', 'https://elsewhere.test', 'route%2fpath', 'r'.repeat(129)])(
  'refuses an invalid route: %j',
  route => expect(() => createBrowserWebChatTransport(route)).toThrow('Invalid Web Chat route'),
);

it.each([
  { kind: 'pending' },
  { kind: 'ready', messages: [{ id: 'x', role: 'system', text: 'not a visitor message' }] },
  { kind: 'ready', messages: [{ id: 'x', role: 'visitor', text: 'x'.repeat(1001) }] },
  { kind: 'ready', messages: [{ id: 'x', role: 'assistant', text: 'ok', token: 'hidden' }] },
  { kind: 'expired', workspace: 'hidden' },
])('rejects an unexpected response rather than exposing it', async value => {
  const transport = createBrowserWebChatTransport('route', vi.fn<typeof fetch>().mockResolvedValue(reply(value)));
  await expect(transport.start({ mode: 'resume', signal })).rejects.toThrow('Invalid Web Chat response');
});

it('keeps expired distinct, does not propagate upstream error text, and rejects invalid sends locally', async () => {
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(reply({ kind: 'expired' }))
    .mockResolvedValueOnce(new Response('private details', { status: 502 }));
  const transport = createBrowserWebChatTransport('route', fetcher);
  await expect(transport.send({ id: 'event', text: 'Hello', signal })).resolves.toEqual({ kind: 'expired' });
  await expect(transport.send({ id: 'event', text: 'Hello', signal })).rejects.toThrow(
    'Web Chat request could not be completed',
  );
  await expect(transport.send({ id: 'bad\n', text: 'Hello', signal })).rejects.toThrow('Invalid Web Chat message');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
