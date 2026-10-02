import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import apiClient from '../../api/client';
import { extensionsApi } from '../../api/extensions';
import {
  useExtensionBridge,
  type ExtensionBridgeApi,
  type ExtensionBridgeContext,
} from './useExtensionBridge';
import { EXTENSION_PROTOCOL } from './extensionProtocol';
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
  onDraftPatch?: (patch: {
    custom_fields?: Record<string, unknown>;
    related?: unknown;
  }) => void;
  minHeight?: number;
  /** Open one of the app's entries when the frame asks (the host page supplies routing). */
  onOpenEntry?: (entryId: string) => void;
}

export type AppExtensionViewHostHandle = ExtensionBridgeApi;

export const AppExtensionViewHost = forwardRef<
  AppExtensionViewHostHandle,
  AppExtensionViewHostProps
>(function AppExtensionViewHost(
  {
    appId,
    viewKey,
    workspaceId,
    handshakeToken,
    packageVersion,
    theme,
    context,
    className,
    onError,
    onDraftPatch,
    minHeight = 240,
    onOpenEntry,
  },
  ref,
) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [failed, setFailed] = useState(false);
  // A real sandbox document has its own hash-only CSP. srcDoc would inherit
  // Core's CSP and block the verified package's inline bridge script.
  const frameUrl = apiClient.getUri({
    url: '/extension-view-frame',
    params: { token: handshakeToken },
  });
  const [loadedToken, setLoadedToken] = useState<string | null>(null);
  const [frameHeight, setFrameHeight] = useState<number | 'fill'>(minHeight);
  // 'fill' tracks the window height so the view gets all the room there is.
  const [windowHeight, setWindowHeight] = useState(() =>
    typeof window === 'undefined' ? 800 : window.innerHeight,
  );
  useEffect(() => {
    if (frameHeight !== 'fill') return undefined;
    const onResize = () => setWindowHeight(window.innerHeight);
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [frameHeight]);
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
      if (path === 'draft' || path === 'draft.snapshot') {
        return ctx.context?.draft ?? ctx.context ?? {};
      }
      return null;
    },
    [],
  );

  const operationHandler = useCallback(
    async (
      operationKey: string,
      payload: Record<string, unknown>,
      ctx: ExtensionBridgeContext,
      idempotencyKey?: string,
    ) =>
      extensionsApi.invokeOperation(
        ctx.appId,
        operationKey,
        payload,
        idempotencyKey,
      ),
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

  const bridgeApi = useExtensionBridge(
    iframeRef,
    bridge,
    readHandler,
    operationHandler,
    capabilitiesHandler,
    queryHandler,
    onDraftPatch,
    (height) => setFrameHeight(Math.max(minHeight, height)),
    {
      // A frame may ask for more room (kept within sane bounds) or to open one of its entries.
      onResize: height =>
        setFrameHeight(height === 'fill' ? 'fill' : Math.min(Math.max(Math.round(height), minHeight), 2400)),
      onNavigate: entryId => onOpenEntry?.(entryId),
    },
  );

  useImperativeHandle(ref, () => bridgeApi, [bridgeApi]);

  // Entry hydration may finish after the iframe's initial ready handshake.
  // Notify the mounted view to reread through the now-current bridge context.
  useEffect(() => {
    iframeRef.current?.contentWindow?.postMessage(
      { protocol: EXTENSION_PROTOCOL, type: 'refresh' },
      '*',
    );
  }, [context]);

  useEffect(() => {
    setFailed(false);
  }, [handshakeToken]);

  if (failed) {
    return <ExtensionViewFallback />;
  }

  return (
    <div
      className={className ?? 'relative w-full'}
      style={{ minHeight: frameHeight === 'fill' ? Math.max(windowHeight - 96, 480) : frameHeight }}
    >
      {!loaded ? (
        <div className="absolute inset-0 flex items-center justify-center" style={{ minHeight }}>
          <Skeleton className="h-full w-full min-h-[240px]" />
        </div>
      ) : null}
      {handshakeToken ? (
        <iframe
          key={handshakeToken}
          ref={iframeRef}
          title={`App extension view ${viewKey}`}
          src={frameUrl}
          referrerPolicy="no-referrer"
          onLoad={() => setLoadedToken(handshakeToken)}
          sandbox="allow-scripts"
          style={
            frameHeight === 'fill'
              ? { height: Math.max(windowHeight - 96, 480), minHeight }
              : { height: frameHeight, minHeight }
          }
          className="w-full border border-[var(--panel-border)] rounded-[var(--radius-card)] bg-[var(--bg)]"
          onError={() => {
            setFailed(true);
            onError?.();
          }}
        />
      ) : null}
    </div>
  );
});
