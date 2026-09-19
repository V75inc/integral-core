import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import apiClient from '../../api/client';
import { extensionsApi } from '../../api/extensions';
import { useExtensionBridge, type ExtensionBridgeContext } from './useExtensionBridge';
import { ExtensionViewFallback } from './ExtensionViewFallback';
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

function extensionAssetUrl(appId: string, viewKey: string, entry = 'index.html') {
  const base = `/extensions/${encodeURIComponent(appId)}/views/${encodeURIComponent(viewKey)}`;
  const path = entry.replace(/^\/+/, '');
  return `${base}/${path}`;
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
  const [loaded, setLoaded] = useState(false);
  const [srcDoc, setSrcDoc] = useState<string | null>(null);

  // Iframe navigations cannot attach the JWT Authorization header. Fetch the
  // package entry HTML through the authenticated API client and mount via
  // srcDoc (inline Asset Register / hello panels are self-contained).
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    setLoaded(false);
    setSrcDoc(null);
    const url = extensionAssetUrl(appId, viewKey, 'index.html');
    (async () => {
      try {
        const { data } = await apiClient.get<string>(url, {
          responseType: 'text',
          transformResponse: [(body) => body],
        });
        if (cancelled) return;
        setSrcDoc(typeof data === 'string' ? data : String(data ?? ''));
        setLoaded(true);
      } catch {
        if (cancelled) return;
        setFailed(true);
        onError?.();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, viewKey, onError]);

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

  useExtensionBridge(
    iframeRef,
    bridge,
    readHandler,
    operationHandler,
    capabilitiesHandler,
    queryHandler,
  );

  if (failed) {
    return <ExtensionViewFallback />;
  }

  return (
    <div className={className ?? 'relative min-h-[240px] w-full'}>
      {!loaded || !srcDoc ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <Skeleton className="h-full w-full min-h-[240px]" />
        </div>
      ) : null}
      {srcDoc ? (
        <iframe
          ref={iframeRef}
          title={`App extension view ${viewKey}`}
          srcDoc={srcDoc}
          sandbox="allow-scripts"
          className="w-full min-h-[240px] border border-[var(--panel-border)] rounded-[var(--radius-card)] bg-[var(--bg)]"
          onError={() => {
            setFailed(true);
            onError?.();
          }}
        />
      ) : null}
    </div>
  );
}
