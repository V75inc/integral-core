import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { AppExtensionViewHost } from '../../components/extensions/AppExtensionViewHost';
import { EXTENSION_PROTOCOL } from '../../components/extensions/extensionProtocol';
import { extensionsApi } from '../../api/extensions';

vi.mock('../../api/extensions', () => ({
  extensionsApi: {
    invokeOperation: vi.fn(async (...args: unknown[]) => ({
      app_id: args[0],
      operation_key: args[1],
      output: { ok: true },
    })),
    listCapabilities: vi.fn(),
    invokeQuery: vi.fn(),
  },
}));

vi.mock('../../api/client', () => ({
  default: {
    getUri: vi.fn(() => '/api/extension-view-frame?token=token-abc'),
  },
}));

vi.mock('../../api/extensions', () => ({
  extensionsApi: {
    invokeOperation: vi.fn(),
    listCapabilities: vi.fn(async () => ({ capabilities: [] })),
    invokeQuery: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AppExtensionViewHost bridge', () => {
  it('forwards extension operation idempotency keys to the host API', async () => {
    const posted: unknown[] = [];
    const mockWindow = { postMessage: (msg: unknown) => posted.push(msg) };
    render(
      <AppExtensionViewHost
        appId="app-1"
        viewKey="asset_detail"
        workspaceId="ws-1"
        handshakeToken="token-abc"
      />,
    );
    const iframe = document.querySelector('iframe');
    expect(iframe).toBeTruthy();
    Object.defineProperty(iframe!, 'contentWindow', {
      value: mockWindow,
      configurable: true,
    });

    window.dispatchEvent(new MessageEvent('message', {
      data: {
        protocol: EXTENSION_PROTOCOL,
        type: 'operation',
        requestId: 'register-1',
        operationKey: 'register_asset',
        idempotencyKey: 'register_asset:stable-retry-key',
        payload: { asset_tag: 'A12-001', title: 'A12 equipment' },
      },
      source: mockWindow as unknown as MessageEventSource,
    }));

    await waitFor(() => {
      expect(extensionsApi.invokeOperation).toHaveBeenCalledWith(
        'app-1',
        'register_asset',
        { asset_tag: 'A12-001', title: 'A12 equipment' },
        'register_asset:stable-retry-key',
      );
      expect(posted.some(msg =>
        (msg as { type?: string; requestId?: string; ok?: boolean }).type === 'operation.result' &&
        (msg as { requestId?: string }).requestId === 'register-1' &&
        (msg as { ok?: boolean }).ok === true,
      )).toBe(true);
    });
  });

  it('keeps the same frame when the parent re-renders with a new inline onError', async () => {
    const props = {
      appId: 'app-1',
      viewKey: 'receive_payment',
      workspaceId: 'ws-1',
      handshakeToken: 'token-abc',
    };
    const { rerender } = render(<AppExtensionViewHost {...props} onError={() => {}} />);
    await waitFor(() => {
      expect(document.querySelector('iframe')).toBeTruthy();
    });
    const first = document.querySelector('iframe');

    rerender(
      <AppExtensionViewHost {...props} onError={() => {}} context={{ draft: { custom_fields: { a: 1 } } }} />,
    );
    rerender(<AppExtensionViewHost {...props} onError={() => {}} />);

    expect(document.querySelector('iframe')).toBe(first);
  });

  it('responds to ready with handshake and answers a read request', async () => {
    const posted: unknown[] = [];
    const mockWindow = {
      postMessage: (msg: unknown) => posted.push(msg),
    };

    const { rerender } = render(
      <AppExtensionViewHost
        appId="app-1"
        viewKey="hello_panel"
        workspaceId="ws-1"
        handshakeToken="token-abc"
        packageVersion="1.0.0"
        context={{ entries: [{ id: 'e1' }, { id: 'e2' }] }}
      />,
    );

    await waitFor(() => {
      expect(document.querySelector('iframe')).toBeTruthy();
    });

    const iframe = document.querySelector('iframe');
    expect(iframe).toHaveAttribute('sandbox', 'allow-scripts');
    expect(iframe).toHaveAttribute('src', '/api/extension-view-frame?token=token-abc');
    expect(iframe).not.toHaveAttribute('srcdoc');
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

    rerender(
      <AppExtensionViewHost
        appId="app-1"
        viewKey="hello_panel"
        workspaceId="ws-1"
        handshakeToken="token-abc"
        context={{ entries: [{ id: 'e1' }, { id: 'e2' }, { id: 'e3' }] }}
      />,
    );
    await waitFor(() => {
      expect(posted.some((msg) => (msg as { type?: string }).type === 'refresh')).toBe(true);
    });
    window.dispatchEvent(new MessageEvent('message', {
      data: { protocol: EXTENSION_PROTOCOL, type: 'read', requestId: 'updated', path: 'entries.count' },
      source: mockWindow as unknown as MessageEventSource,
    }));
    await waitFor(() => {
      const updated = posted.find((msg) => (msg as { requestId?: string }).requestId === 'updated') as { value?: number };
      expect(updated?.value).toBe(3);
    });
  });

  it('forwards draft.patch to the host callback', async () => {
    const posted: unknown[] = [];
    const onDraftPatch = vi.fn();
    const mockWindow = {
      postMessage: (msg: unknown) => posted.push(msg),
    };

    render(
      <AppExtensionViewHost
        appId="app-1"
        viewKey="document_lines"
        workspaceId="ws-1"
        handshakeToken="token-abc"
        context={{ draft: { custom_fields: {} } }}
        onDraftPatch={onDraftPatch}
      />,
    );

    await waitFor(() => {
      expect(document.querySelector('iframe')).toBeTruthy();
    });

    const iframe = document.querySelector('iframe');
    Object.defineProperty(iframe!, 'contentWindow', {
      value: mockWindow,
      configurable: true,
    });

    window.dispatchEvent(
      new MessageEvent('message', {
        data: {
          protocol: EXTENSION_PROTOCOL,
          type: 'draft.patch',
          custom_fields: { total_amount: 42 },
          related: { lines: [{ description: 'A' }] },
        },
        source: mockWindow as unknown as MessageEventSource,
      }),
    );

    await waitFor(() => {
      expect(onDraftPatch).toHaveBeenCalledWith({
        custom_fields: { total_amount: 42 },
        related: { lines: [{ description: 'A' }] },
      });
    });
  });
});
