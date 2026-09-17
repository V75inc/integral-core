import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { AppExtensionViewHost } from '../../components/extensions/AppExtensionViewHost';
import { EXTENSION_PROTOCOL } from '../../components/extensions/extensionProtocol';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AppExtensionViewHost bridge', () => {
  it('responds to ready with handshake and answers a read request', async () => {
    const posted: unknown[] = [];
    const mockWindow = {
      postMessage: (msg: unknown) => posted.push(msg),
    };

    render(
      <AppExtensionViewHost
        appId="app-1"
        viewKey="hello_panel"
        workspaceId="ws-1"
        handshakeToken="token-abc"
        packageVersion="1.0.0"
        context={{ entries: [{ id: 'e1' }, { id: 'e2' }] }}
      />,
    );

    const iframe = document.querySelector('iframe');
    expect(iframe).toBeTruthy();
    Object.defineProperty(iframe!, 'contentWindow', {
      value: mockWindow,
      configurable: true,
    });

    window.dispatchEvent(
      new MessageEvent('message', {
        data: { protocol: EXTENSION_PROTOCOL, type: 'ready' },
        source: mockWindow as unknown as MessageEventSource,
      }),
    );

    await waitFor(() => {
      expect(posted.some((msg) => {
        const m = msg as { type?: string; token?: string };
        return m.type === 'handshake' && m.token === 'token-abc';
      })).toBe(true);
    });

    window.dispatchEvent(
      new MessageEvent('message', {
        data: {
          protocol: EXTENSION_PROTOCOL,
          type: 'read',
          requestId: 'r1',
          path: 'entries.count',
        },
        source: mockWindow as unknown as MessageEventSource,
      }),
    );

    await waitFor(() => {
      const result = posted.find((msg) => {
        const m = msg as { type?: string; requestId?: string; value?: number };
        return m.type === 'read.result' && m.requestId === 'r1';
      }) as { ok?: boolean; value?: number };
      expect(result?.ok).toBe(true);
      expect(result?.value).toBe(2);
    });
  });
});
