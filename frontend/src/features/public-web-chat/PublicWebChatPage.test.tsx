import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PublicWebChatPage } from './PublicWebChatPage';

const ROUTE = 'admitted-page';
const TITLE = '<em>Visitor support</em>';
const config = { route: ROUTE, title: TITLE };

function ready(text?: string) {
  return new Response(JSON.stringify({
    kind: 'ready',
    messages: text ? [{ id: 'assistant-fixture', role: 'assistant', text }] : [],
  }), { headers: { 'content-type': 'application/json' } });
}

const originalScrollTo = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTo');

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  });
  Object.defineProperty(Element.prototype, 'scrollTo', { value: vi.fn(), configurable: true });
});

afterEach(() => {
  cleanup();
  if (originalScrollTo) Object.defineProperty(Element.prototype, 'scrollTo', originalScrollTo);
  else Reflect.deleteProperty(Element.prototype, 'scrollTo');
  vi.unstubAllGlobals();
});

describe('detached PublicWebChatPage', () => {
  it('uses the existing assistant-ui page and starts exactly one scoped session across title-only rerenders', async () => {
    const fetcher = vi.fn().mockResolvedValue(ready('Current conversation'));
    vi.stubGlobal('fetch', fetcher);
    const result = render(<StrictMode><PublicWebChatPage config={config} /></StrictMode>);
    expect(screen.getByRole('heading', { name: TITLE })).toBeVisible();
    expect(result.container.querySelector('em')).toBeNull();
    expect(await screen.findByText('Current conversation')).toBeVisible();
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/public/web-chat/admitted-page/session',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: '{"mode":"resume"}',
      }));
    result.rerender(<StrictMode><PublicWebChatPage config={{ ...config, title: 'Updated title' }} /></StrictMode>);
    expect(screen.getByRole('heading', { name: 'Updated title' })).toBeVisible();
    expect(screen.getByText('Current conversation')).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it.each([
    undefined, null, {},
    { route: 'https://foreign.example/chat', title: 'Unsafe' },
    { route: 'another/path', title: 'Unsafe' },
    { route: 'admitted-page?q=1', title: 'Unsafe' },
    { route: ROUTE, title: 'Unsafe\nheading' },
    { route: ROUTE, title: '\ud800' },
    { route: ROUTE, title: 'X'.repeat(121) },
    { route: ROUTE, title: 'Safe', workspace_id: 'not allowed' },
    { route: ROUTE, title: 'Safe', token: 'not allowed' },
    { route: ROUTE, title: 'Safe', model: 'not allowed' },
  ])('makes no request when given missing or inadmissible configuration (%j)', async (unsafe) => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    render(<PublicWebChatPage config={unsafe} />);
    expect(screen.getByRole('status')).toHaveTextContent('This chat is unavailable.');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    await act(async () => { await Promise.resolve(); });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('clears the transcript and cancels a send when the admitted route changes', async () => {
    let resolveOld!: (response: Response) => void;
    const oldRequest: { signal?: AbortSignal } = {};
    const fetcher = vi.fn((path: string, init: RequestInit) => {
      if (path.endsWith('/admitted-page/messages')) {
        oldRequest.signal = init.signal ?? undefined;
        return new Promise<Response>(resolve => { resolveOld = resolve; });
      }
      return Promise.resolve(ready(path.includes('/admitted-page/') ? 'Prior history' : 'Separate history'));
    });
    vi.stubGlobal('fetch', fetcher);
    const result = render(<PublicWebChatPage config={config} />);
    expect(await screen.findByText('Prior history')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'One old turn' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(oldRequest.signal).toBeDefined());

    result.rerender(<PublicWebChatPage config={{ route: 'another-admitted-page', title: 'Another chat' }} />);
    expect(screen.queryByText('Prior history')).not.toBeInTheDocument();
    expect(oldRequest.signal?.aborted).toBe(true);
    expect(await screen.findByText('Separate history')).toBeVisible();
    await act(async () => resolveOld(ready('Obsolete late reply')));
    expect(screen.queryByText('Obsolete late reply')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('');
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      '/api/public/web-chat/admitted-page/session',
      '/api/public/web-chat/admitted-page/messages',
      '/api/public/web-chat/another-admitted-page/session',
    ]);
  });
});
