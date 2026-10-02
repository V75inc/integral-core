import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import apiClient from '../../api/client';
import { extensionsApi } from '../../api/extensions';
import { useExtensionBridge, type ExtensionBridgeContext } from './useExtensionBridge';
import { ExtensionViewFallback } from './ExtensionViewFallback';
import {
  EXTENSION_VIEW_IFRAME_CLASS,
  EXTENSION_VIEW_SHELL_CLASS,
} from './extensionViewLayout';
import { Skeleton } from '../ui';

export interface AppExtensionViewHostProps {
  appId: string;
  viewKey: string;
  workspaceId: string;
  handshakeToken: string;
  packageVersion?: string;
  theme?: Record<string, unknown>;
  context?: Record<string, unknown>;
  className?: string;
  onError?: () => void;
}

export function AppExtensionViewHost({
  appId,
  viewKey,
  workspaceId,
  handshakeToken,
  packageVersion,
  theme,
  context,
  className,
  onError,
}: AppExtensionViewHostProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [failed, setFailed] = useState(false);
  // A real sandbox document has its own hash-only CSP. srcDoc would inherit
  // Core's CSP and block the verified package's inline bridge script.
  const frameUrl = apiClient.getUri({
    url: '/extension-view-frame',
    params: { token: handshakeToken },
  });
  const [loadedToken, setLoadedToken] = useState<string | null>(null);
  const loaded = loadedToken === handshakeToken;

  const bridge = useMemo<ExtensionBridgeContext>(
    () => ({
      workspaceId,
      appId,
      viewKey,
      handshakeToken,
      packageVersion,
      theme,
      context,
    }),
    [workspaceId, appId, viewKey, handshakeToken, packageVersion, theme, context],
  );

  const readHandler = useMemo(
    () => (path: string, ctx: ExtensionBridgeContext) => {
      if (path === 'entries.count') {
        const entries = ctx.context?.entries;
        return Array.isArray(entries) ? entries.length : 0;
      }
      if (path === 'context.primary_entry') {
        const entries = ctx.context?.entries;
        if (Array.isArray(entries) && entries.length > 0) {
          return entries[0];
        }
        return null;
      }
      if (path === 'context') {
        return ctx.context ?? {};
      }
      return null;
    },
    [],
  );

  const operationHandler = useCallback(
    async (operationKey: string, payload: Record<string, unknown>, ctx: ExtensionBridgeContext) =>
      extensionsApi.invokeOperation(ctx.appId, operationKey, payload),
    [],
  );

  const capabilitiesHandler = useCallback(
    async (ctx: ExtensionBridgeContext) => {
      const snap = await extensionsApi.listCapabilities(true);
      const filtered = (snap.capabilities || []).filter(
        (c) => !c.app_id || c.app_id === ctx.appId || c.namespace === 'integral',
      );
      return { ...snap, capabilities: filtered };
    },
    [],
  );

  const queryHandler = useCallback(
    async (
      capabilityKey: string,
      params: Record<string, unknown>,
      ctx: ExtensionBridgeContext,
    ) => extensionsApi.invokeQuery(ctx.appId, capabilityKey, params),
    [],
  );

  const { sendHandshake } = useExtensionBridge(
    iframeRef,
    bridge,
    readHandler,
    operationHandler,
    capabilitiesHandler,
    queryHandler,
  );

  // Entry hydration may finish after the iframe's initial ready handshake.
  // Re-send handshake so the child re-reads `context` (mail log uses path `context`).
  useEffect(() => {
    if (!loaded) return;
    sendHandshake();
  }, [context, loaded, sendHandshake]);

  useEffect(() => {
    setFailed(false);
  }, [handshakeToken]);

  if (failed) {
    return <ExtensionViewFallback />;
  }

  return (
    <div className={className ?? EXTENSION_VIEW_SHELL_CLASS}>
      {!loaded ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <Skeleton className="h-full w-full min-h-[32rem]" />
        </div>
      ) : null}
      {handshakeToken ? (
        <iframe
          key={handshakeToken}
          ref={iframeRef}
          title={`App extension view ${viewKey}`}
          src={frameUrl}
          referrerPolicy="no-referrer"
          onLoad={() => {
            setLoadedToken(handshakeToken);
            sendHandshake();
          }}
          sandbox="allow-scripts"
          className={EXTENSION_VIEW_IFRAME_CLASS}
          onError={() => {
            setFailed(true);
            onError?.();
          }}
        />
      ) : null}
    </div>
  );
}
