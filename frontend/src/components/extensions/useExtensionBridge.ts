import { useCallback, useEffect, useRef } from 'react';
import {
  EXTENSION_PROTOCOL,
  isExtensionMessage,
  type ExtensionBridgeMessage,
} from './extensionProtocol';

export type ExtensionBridgeContext = {
  workspaceId: string;
  appId: string;
  viewKey: string;
  handshakeToken: string;
  packageVersion?: string;
  theme?: Record<string, unknown>;
  /** Optional host context for read handlers (e.g. entry id). */
  context?: Record<string, unknown>;
};

type ReadHandler = (path: string, ctx: ExtensionBridgeContext) => unknown;
type OperationHandler = (
  operationKey: string,
  payload: Record<string, unknown>,
  ctx: ExtensionBridgeContext,
  idempotencyKey?: string,
) => Promise<unknown>;
type CapabilitiesHandler = (ctx: ExtensionBridgeContext) => Promise<unknown>;
type QueryHandler = (
  capabilityKey: string,
  params: Record<string, unknown>,
  ctx: ExtensionBridgeContext,
) => Promise<unknown>;

/** Things a frame may ask the host to do to the page around it. */
export type ExtensionHostActions = {
  onResize?: (height: number | 'fill') => void;
  onNavigate?: (entryId: string) => void;
};

export type DraftPatchHandler = (patch: {
  custom_fields?: Record<string, unknown>;
  related?: unknown;
}) => void;

export type UiResizeHandler = (height: number) => void;

type PendingRequest = {
  resolve: (value: {
    ok: boolean;
    error?: string;
    field_errors?: Record<string, string>;
    value?: unknown;
  }) => void;
  kind: 'validate' | 'submit';
};

export type ExtensionBridgeApi = {
  sendHandshake: () => void;
  postToChild: (message: ExtensionBridgeMessage) => void;
  requestValidate: () => Promise<{
    ok: boolean;
    error?: string;
    field_errors?: Record<string, string>;
  }>;
  requestSubmit: (entryId?: string | null) => Promise<{
    ok: boolean;
    error?: string;
    value?: unknown;
  }>;
  notifyCommitted: (entryId: string, mode?: 'create' | 'edit') => void;
};

function newRequestId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function useExtensionBridge(
  iframeRef: React.RefObject<HTMLIFrameElement | null>,
  bridge: ExtensionBridgeContext | null,
  readHandler?: ReadHandler,
  operationHandler?: OperationHandler,
  capabilitiesHandler?: CapabilitiesHandler,
  queryHandler?: QueryHandler,
  draftPatchHandler?: DraftPatchHandler,
  uiResizeHandler?: UiResizeHandler,
  hostActions?: ExtensionHostActions,
): ExtensionBridgeApi {
  const actionsRef = useRef(hostActions);
  actionsRef.current = hostActions;
  const bridgeRef = useRef(bridge);
  bridgeRef.current = bridge;
  const pendingRef = useRef<Map<string, PendingRequest>>(new Map());

  const postToChild = useCallback((message: ExtensionBridgeMessage) => {
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    win.postMessage(message, '*');
  }, [iframeRef]);

  const sendHandshake = useCallback(() => {
    const current = bridgeRef.current;
    if (!current) return;
    postToChild({
      protocol: EXTENSION_PROTOCOL,
      type: 'handshake',
      token: current.handshakeToken,
      workspaceId: current.workspaceId,
      appId: current.appId,
      viewKey: current.viewKey,
      packageVersion: current.packageVersion,
      theme: current.theme,
    });
  }, [postToChild]);

  const requestValidate = useCallback(() => {
    return new Promise<{
      ok: boolean;
      error?: string;
      field_errors?: Record<string, string>;
    }>((resolve) => {
      const requestId = newRequestId('validate');
      pendingRef.current.set(requestId, {
        kind: 'validate',
        resolve: (value) =>
          resolve({
            ok: value.ok,
            error: value.error,
            field_errors: value.field_errors,
          }),
      });
      postToChild({
        protocol: EXTENSION_PROTOCOL,
        type: 'draft.validate',
        requestId,
      });
      // If the iframe ignores draft messages, do not block forever.
      window.setTimeout(() => {
        const pending = pendingRef.current.get(requestId);
        if (!pending) return;
        pendingRef.current.delete(requestId);
        resolve({ ok: true });
      }, 4000);
    });
  }, [postToChild]);

  const requestSubmit = useCallback(
    (entryId?: string | null) => {
      return new Promise<{
        ok: boolean;
        error?: string;
        value?: unknown;
      }>((resolve) => {
        const requestId = newRequestId('submit');
        pendingRef.current.set(requestId, {
          kind: 'submit',
          resolve: (value) =>
            resolve({
              ok: value.ok,
              error: value.error,
              value: value.value,
            }),
        });
        postToChild({
          protocol: EXTENSION_PROTOCOL,
          type: 'draft.submit',
          requestId,
          entry_id: entryId ?? null,
        });
        window.setTimeout(() => {
          const pending = pendingRef.current.get(requestId);
          if (!pending) return;
          pendingRef.current.delete(requestId);
          resolve({ ok: true });
        }, 8000);
      });
    },
    [postToChild],
  );

  const notifyCommitted = useCallback(
    (entryId: string, mode?: 'create' | 'edit') => {
      postToChild({
        protocol: EXTENSION_PROTOCOL,
        type: 'draft.committed',
        entry_id: entryId,
        mode,
      });
    },
    [postToChild],
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const iframeWin = iframeRef.current?.contentWindow;
      if (!iframeWin || event.source !== iframeWin) return;
      if (!isExtensionMessage(event.data)) return;
      const msg = event.data;
      const current = bridgeRef.current;
      if (!current) return;

      if (msg.type === 'ready') {
        sendHandshake();
        return;
      }

      if (msg.type === 'resize') {
        if (msg.height === 'fill') {
          actionsRef.current?.onResize?.('fill');
          return;
        }
        const height = Number(msg.height);
        if (Number.isFinite(height)) actionsRef.current?.onResize?.(height);
        return;
      }

      if (msg.type === 'navigate') {
        // Only a plain entry id: never a path or URL a frame could steer the page with.
        const entryId = String(msg.entryId || '');
        if (/^[A-Za-z0-9_.-]{1,128}$/.test(entryId)) actionsRef.current?.onNavigate?.(entryId);
        return;
      }

      if (msg.type === 'draft.patch') {
        draftPatchHandler?.({
          custom_fields: msg.custom_fields,
          related: msg.related,
        });
        return;
      }

      if (msg.type === 'ui.resize') {
        if (typeof msg.height === 'number' && Number.isFinite(msg.height)) {
          uiResizeHandler?.(Math.max(0, msg.height));
        }
        return;
      }

      if (msg.type === 'draft.validate.result' || msg.type === 'draft.submit.result') {
        const pending = pendingRef.current.get(msg.requestId);
        if (!pending) return;
        pendingRef.current.delete(msg.requestId);
        pending.resolve({
          ok: Boolean(msg.ok),
          error: msg.error,
          field_errors:
            msg.type === 'draft.validate.result' ? msg.field_errors : undefined,
          value: msg.type === 'draft.submit.result' ? msg.value : undefined,
        });
        return;
      }

      if (msg.type === 'read' && readHandler) {
        let value: unknown;
        let ok = true;
        let error: string | undefined;
        try {
          value = readHandler(msg.path, current);
        } catch (err) {
          ok = false;
          error = err instanceof Error ? err.message : 'read failed';
        }
        postToChild({
          protocol: EXTENSION_PROTOCOL,
          type: 'read.result',
          requestId: msg.requestId,
          ok,
          value,
          error,
        });
        return;
      }

      if (msg.type === 'operation' && operationHandler) {
        void (async () => {
          let value: unknown;
          let ok = true;
          let error: string | undefined;
          try {
            value = await operationHandler(
              msg.operationKey,
              msg.payload ?? {},
              current,
              msg.idempotencyKey,
            );
          } catch (err) {
            ok = false;
            error = err instanceof Error ? err.message : 'operation failed';
          }
          postToChild({
            protocol: EXTENSION_PROTOCOL,
            type: 'operation.result',
            requestId: msg.requestId,
            ok,
            value,
            error,
          });
        })();
        return;
      }

      if (msg.type === 'capabilities' && capabilitiesHandler) {
        void (async () => {
          let value: unknown;
          let ok = true;
          let error: string | undefined;
          try {
            value = await capabilitiesHandler(current);
          } catch (err) {
            ok = false;
            error = err instanceof Error ? err.message : 'capabilities failed';
          }
          postToChild({
            protocol: EXTENSION_PROTOCOL,
            type: 'capabilities.result',
            requestId: msg.requestId,
            ok,
            value,
            error,
          });
        })();
        return;
      }

      if (msg.type === 'query' && queryHandler) {
        void (async () => {
          let value: unknown;
          let ok = true;
          let error: string | undefined;
          try {
            value = await queryHandler(
              msg.capabilityKey,
              msg.params ?? {},
              current,
            );
          } catch (err) {
            ok = false;
            error = err instanceof Error ? err.message : 'query failed';
          }
          postToChild({
            protocol: EXTENSION_PROTOCOL,
            type: 'query.result',
            requestId: msg.requestId,
            ok,
            value,
            error,
          });
        })();
      }
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [
    iframeRef,
    postToChild,
    readHandler,
    operationHandler,
    capabilitiesHandler,
    queryHandler,
    draftPatchHandler,
    uiResizeHandler,
    sendHandshake,
  ]);

  return {
    sendHandshake,
    postToChild,
    requestValidate,
    requestSubmit,
    notifyCommitted,
  };
}
