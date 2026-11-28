import { act, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mountEmbedWebChat } from './mountEmbedWebChat';

const origin = 'https://embed.example.com';
const title = '<em>Visitor support</em>';
const route = 'opaque-frame';
const seed = `iweb_${'A'.repeat(43)}`;
const token = `iwes_${'B'.repeat(43)}`;
const mountId = 'public-web-chat-embed-root';
const attributes = {
  'data-web-chat-embed-route': route,
  'data-web-chat-embed-origin': origin,
  'data-web-chat-embed-bootstrap-seed': seed,
  'data-web-chat-embed-title': title,
};
const jsdom = (globalThis as unknown as { jsdom: {
  reconfigure(config: { url: string }): void;
} }).jsdom;
const roots: NonNullable<ReturnType<typeof mountEmbedWebChat>>[] = [];
const fetcher = vi.fn<typeof fetch>();
const defaultView = document.defaultView!;
const browserFetch = Object.getOwnPropertyDescriptor(defaultView, 'fetch');
const scrollTo = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTo');

function hostConfig(overrides: Record<string, string | null> = {}) {
  const element = document.createElement('div');
  element.id = mountId;
  for (const [name, value] of Object.entries({ ...attributes, ...overrides })) {
    if (value !== null) element.setAttribute(name, value);
  }
  document.body.append(element);
  return element;
}

function mount() {
  let root: ReturnType<typeof mountEmbedWebChat> = null;
  act(() => {
    root = mountEmbedWebChat(document);
    if (root) roots.push(root);
  });
  return root;
}

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

beforeEach(() => {
  fetcher.mockReset();
  jsdom.reconfigure({ url: origin + '/embed/does-not-configure-transport?seed=ignored' });
  vi.stubGlobal('top', {});
  Object.defineProperty(defaultView, 'fetch', { value: fetcher, configurable: true });
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  });
  Object.defineProperty(Element.prototype, 'scrollTo', { value: vi.fn(), configurable: true });
});

afterEach(() => {
  act(() => { for (const root of roots.splice(0)) root.unmount(); });
  document.body.replaceChildren();
  jsdom.reconfigure({ url: 'http://localhost:3000' });
  if (scrollTo) Object.defineProperty(Element.prototype, 'scrollTo', scrollTo);
  else Reflect.deleteProperty(Element.prototype, 'scrollTo');
  if (browserFetch) Object.defineProperty(defaultView, 'fetch', browserFetch);
  else Reflect.deleteProperty(defaultView, 'fetch');
  vi.unstubAllGlobals();
});

describe('detached embed production mount', () => {
  it('scrubs host attributes before rendering or opening a single cookie-free session', async () => {
    const element = hostConfig();
    fetcher.mockImplementationOnce(async () => {
      expect(element.getAttributeNames()).toEqual(['id']);
      return reply(200, { kind: 'ready', messages: [], session_token: token, expires_in: 30 });
    });
    expect(mount()).not.toBeNull();
    expect(element.getAttributeNames()).toEqual(['id']);
    expect(mount()).toBeNull();
    expect(screen.getByRole('heading', { name: title })).toBeVisible();
    expect(element.querySelector('em')).toBeNull();
    expect(await screen.findByText('Send a message to begin.')).toBeVisible();
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(`/api/public/web-chat-embed/${route}/session`,
      expect.objectContaining({ credentials: 'omit', body: JSON.stringify({ mode: 'resume', bootstrap_seed: seed }) }));
    expect(element.outerHTML).not.toContain(seed);
    expect(element.outerHTML).not.toContain(token);
    expect(screen.queryByRole('button', { name: /new chat/i })).not.toBeInTheDocument();
  });

  it.each([
    ['blank template', { 'data-web-chat-embed-route': '', 'data-web-chat-embed-origin': '',
      'data-web-chat-embed-bootstrap-seed': '', 'data-web-chat-embed-title': '' }],
    ['absent proof', { 'data-web-chat-embed-bootstrap-seed': null }],
    ['unexpected configuration', { 'data-web-chat-embed-workspace-id': 'never-authority' }],
    ['wrong embed origin', { 'data-web-chat-embed-origin': 'https://other.example.com' }],
    ['unbounded route', { 'data-web-chat-embed-route': 'A'.repeat(500) }],
    ['title would expose proof', { 'data-web-chat-embed-title': seed }],
    ['unbounded title', { 'data-web-chat-embed-title': 'x'.repeat(121) }],
  ] as const)('fails closed and scrubs attributes for %s', async (_label, overrides) => {
    const element = hostConfig(overrides);
    mount();
    expect(element.getAttributeNames()).toEqual(['id']);
    expect(await screen.findByRole('status')).toHaveTextContent('This chat is unavailable.');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('refuses a top-level view even with otherwise valid attributes', async () => {
    vi.stubGlobal('top', defaultView);
    const element = hostConfig();
    mount();
    expect(element.getAttributeNames()).toEqual(['id']);
    expect(await screen.findByRole('status')).toHaveTextContent('This chat is unavailable.');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('refuses duplicate roots and erases their attributes', () => {
    const first = hostConfig();
    const second = hostConfig();
    expect(mount()).toBeNull();
    expect(first.getAttributeNames()).toEqual(['id']);
    expect(second.getAttributeNames()).toEqual(['id']);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('requires reopening after expiry instead of offering unsupported in-place rotation', async () => {
    fetcher.mockResolvedValueOnce(reply(410, { kind: 'expired' }));
    hostConfig();
    mount();
    expect(await screen.findByText('This chat has expired. Reopen this chat from the website to start again.'))
      .toBeVisible();
    expect(screen.queryByRole('button', { name: /new chat/i })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('requires reopening on ambiguous bootstrap failure rather than replaying the proof', async () => {
    fetcher.mockRejectedValueOnce(new TypeError('ambiguous'));
    hostConfig();
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('Reopen this chat from the website');
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
