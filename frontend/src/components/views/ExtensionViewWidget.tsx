import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { extensionsApi } from '../../api/extensions';
import { useScope } from '../../context/ScopeContext';
import { AppExtensionViewHost } from '../extensions/AppExtensionViewHost';
import { ExtensionViewFallback } from '../extensions/ExtensionViewFallback';
import type { ViewWidgetProps } from '../../views/types';

function resolveExtensionViewKey(view: ViewWidgetProps['view']): string {
  const cfg = (view.config || {}) as { extension_view_key?: string };
  const top = (view as { extension_view_key?: string }).extension_view_key;
  return String(top || cfg.extension_view_key || '').trim();
}

export function ExtensionViewWidget({
  view,
  entries,
  track,
}: ViewWidgetProps) {
  const viewKey = resolveExtensionViewKey(view);
  const appId = track?.app_id || '';
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId ?? '';
  const [failed, setFailed] = useState(false);

  const handshakeQuery = useQuery({
    queryKey: ['extension-view-handshake', appId, viewKey, workspaceId],
    queryFn: () => extensionsApi.handshake(appId, viewKey),
    enabled: Boolean(appId && viewKey && workspaceId),
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    if (handshakeQuery.isError) {
      setFailed(true);
    }
  }, [handshakeQuery.isError]);

  if (!appId || !viewKey) {
    return (
      <ExtensionViewFallback
        message="This extension view is missing app or view key metadata."
      />
    );
  }

  if (failed || handshakeQuery.isError) {
    return <ExtensionViewFallback />;
  }

  if (handshakeQuery.isLoading || !handshakeQuery.data) {
    return (
      <div className="min-h-[240px] rounded-[var(--radius-card)] bg-[var(--panel-2)] animate-pulse" />
    );
  }

  const hs = handshakeQuery.data;
  return (
    <AppExtensionViewHost
      appId={appId}
      viewKey={viewKey}
      workspaceId={workspaceId}
      handshakeToken={hs.handshake_token}
      packageVersion={hs.package_version}
      theme={hs.theme}
      context={{ entries, trackId: track?.id }}
      onError={() => setFailed(true)}
    />
  );
}
