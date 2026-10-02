import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { extensionsApi } from '../../api/extensions';
import { useScope } from '../../context/ScopeContext';
import { useContributionLifecycle } from '../entries/contributionLifecycle';
import { AppExtensionViewHost } from '../extensions/AppExtensionViewHost';
import { ExtensionViewFallback } from '../extensions/ExtensionViewFallback';
import type { ViewWidgetProps } from '../../views/types';

function resolveExtensionViewKey(view: ViewWidgetProps['view']): string {
  const cfg = (view.config || {}) as { extension_view_key?: string };
  const top = (view as { extension_view_key?: string }).extension_view_key;
  return String(top || cfg.extension_view_key || '').trim();
}

type ContributionBindings = {
  appId?: string;
  trackId?: string;
  entryId?: string;
  entryValues?: Record<string, unknown>;
  __contributionMode?: string;
  __contributionPlacement?: string;
  entry_type_key?: string;
};

/**
 * Resolve app id for nested document-shell regions.
 *
 * ``ComposableViewSlot`` / ``NativeContributionHost`` often omit the ``track``
 * prop, so ``track.app.id`` is empty inside layout_container children. Hosts
 * already forward ``appId`` on ``view.config.__bindings`` (see
 * NativeContributionHost) — prefer that fallback.
 */
function resolveAppId(
  track: ViewWidgetProps['track'],
  bindings: ContributionBindings,
): string {
  return String(track?.app?.id || bindings.appId || '').trim();
}

export function ExtensionViewWidget({
  view,
  entries,
  track,
}: ViewWidgetProps) {
  const viewKey = resolveExtensionViewKey(view);
  const bindings = ((view.config || {}) as { __bindings?: ContributionBindings })
    .__bindings || {};
  const lifecycle = useContributionLifecycle();
  const appId = resolveAppId(track, bindings);
  const trackId = String(
    track?.id || bindings.trackId || lifecycle?.trackId || view.track_id || '',
  ).trim();
  const entryId = String(
    bindings.entryId ||
      lifecycle?.entryId ||
      (entries[0] && entries[0].id) ||
      '',
  ).trim();
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId ?? '';
  const [failed, setFailed] = useState(false);
  // Prefer live lifecycle values (updated by sibling form regions) over the
  // snapshot frozen into __bindings when the host first mounted.
  const entryValues =
    (lifecycle?.customFields && Object.keys(lifecycle.customFields).length
      ? lifecycle.customFields
      : null) ||
    bindings.entryValues ||
    {};

  const hostContext = useMemo(
    () => ({
      entries,
      trackId,
      track_id: trackId,
      entryId: entryId || null,
      entry_id: entryId || null,
      entry_type_key: bindings.entry_type_key,
      mode: bindings.__contributionMode || 'detail',
      placement: bindings.__contributionPlacement,
      custom_fields: entryValues,
      draft: {
        custom_fields: entryValues,
        related: {},
      },
      viewKey,
    }),
    [
      entries,
      trackId,
      entryId,
      bindings.entry_type_key,
      bindings.__contributionMode,
      bindings.__contributionPlacement,
      entryValues,
      viewKey,
    ],
  );

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
      context={hostContext}
      onDraftPatch={lifecycle?.onDraftPatch}
      onError={() => setFailed(true)}
    />
  );
}
