import { useMemo, useRef, useState } from 'react';
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
  const base = `/api/extensions/${encodeURIComponent(appId)}/views/${encodeURIComponent(viewKey)}`;
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
      if (path === 'context') {
        return ctx.context ?? {};
      }
      return null;
    },
    [],
  );

  useExtensionBridge(iframeRef, bridge, readHandler);

  if (failed) {
    return <ExtensionViewFallback />;
  }

  const src = extensionAssetUrl(appId, viewKey, 'index.html');

  return (
    <div className={className ?? 'relative min-h-[240px] w-full'}>
      {!loaded ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <Skeleton className="h-full w-full min-h-[240px]" />
        </div>
      ) : null}
      <iframe
        ref={iframeRef}
        title={`App extension view ${viewKey}`}
        src={src}
        sandbox="allow-scripts"
        className="w-full min-h-[240px] border border-[var(--panel-border)] rounded-[var(--radius-card)] bg-[var(--bg)]"
        onLoad={() => setLoaded(true)}
        onError={() => {
          setFailed(true);
          onError?.();
        }}
      />
    </div>
  );
}
