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

export function useExtensionBridge(
  iframeRef: React.RefObject<HTMLIFrameElement | null>,
  bridge: ExtensionBridgeContext | null,
  readHandler?: ReadHandler,
) {
  const bridgeRef = useRef(bridge);
  bridgeRef.current = bridge;

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
      }
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [iframeRef, postToChild, readHandler, sendHandshake]);

  return { sendHandshake };
}
