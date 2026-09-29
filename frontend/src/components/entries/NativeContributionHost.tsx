/**
 * Mounts a Core view palette widget as an entry ui_contribution (native
 * alternative to the ADR-011 iframe path).
 *
 * Resolves either:
 *   - ``view`` — saved view key on the host track (``_manifest_view_key``), or
 *   - ``view_type`` + optional inline ``config`` — synthetic SavedView
 *
 * Widgets that participate in compose validate/submit register via
 * ``ContributionLifecycleContext`` (see ``editable_related_lines``).
 */

import { useEffect, useMemo, useState } from 'react';
import { trackViewsApi } from '../../api';
import type { SavedView } from '../../types';
import { ViewRenderer } from '../../views/registry';
import {
  ContributionLifecycleContext,
  type ContributionLifecycleApi,
} from './contributionLifecycle';

export type NativeContributionSpec = {
  view?: string;
  view_type?: string;
  config?: Record<string, unknown>;
};

type NativeContributionHostProps = {
  trackId: string;
  contribution: NativeContributionSpec;
  lifecycle: ContributionLifecycleApi;
  className?: string;
  minHeight?: number;
};

function matchSavedView(views: SavedView[], viewKey: string): SavedView | undefined {
  return (
    views.find(
      v =>
        String(
          (v.config as { _manifest_view_key?: string } | undefined)?._manifest_view_key ||
            ''
        ) === viewKey
    ) ||
    views.find(v => (v as SavedView & { key?: string }).key === viewKey) ||
    views.find(v => (v.name || '').toLowerCase() === viewKey.toLowerCase())
  );
}

export function NativeContributionHost({
  trackId,
  contribution,
  lifecycle,
  className,
  minHeight = 240,
}: NativeContributionHostProps) {
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [loading, setLoading] = useState(Boolean(contribution.view));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!contribution.view || !trackId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    trackViewsApi
      .list(trackId)
      .then(views => {
        if (!cancelled) setSavedViews(views);
      })
      .catch(() => {
        if (!cancelled) {
          setSavedViews([]);
          setError('Could not load contribution view');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contribution.view, trackId]);

  const view = useMemo<SavedView | null>(() => {
    if (contribution.view) {
      const matched = matchSavedView(savedViews, contribution.view);
      if (!matched) return null;
      if (contribution.config && Object.keys(contribution.config).length) {
        return {
          ...matched,
          config: { ...(matched.config || {}), ...contribution.config },
        };
      }
      return matched;
    }
    if (contribution.view_type) {
      return {
        id: `native-contrib-${contribution.view_type}`,
        name: contribution.view_type,
        type: contribution.view_type,
        track_id: trackId,
        config: {
          ...(contribution.config || {}),
          _manifest_view_key: contribution.view_type,
        },
      } as SavedView;
    }
    return null;
  }, [contribution, savedViews, trackId]);

  const boundView = useMemo(() => {
    if (!view) return null;
    return {
      ...view,
      config: {
        ...(view.config || {}),
        __bindings: {
          entryId: lifecycle.entryId,
          entryValues: lifecycle.customFields,
          currentUser: undefined,
          __contributionMode: lifecycle.mode,
          __contributionPlacement: lifecycle.placement,
          entry_type_key: lifecycle.entryTypeKey,
          trackId: lifecycle.trackId || trackId,
          appId: lifecycle.appId,
        },
      },
    } as SavedView;
  }, [view, lifecycle, trackId]);

  if (loading) {
    return (
      <div
        className={
          className ??
          'min-h-[200px] rounded-[var(--radius-card)] bg-[var(--panel-2)] animate-pulse'
        }
        style={{ minHeight }}
      />
    );
  }

  if (error) {
    return (
      <div className="text-sm text-[var(--danger)] py-2" role="alert">
        {error}
      </div>
    );
  }

  if (!boundView) {
    return null;
  }

  return (
    <ContributionLifecycleContext.Provider value={lifecycle}>
      <div className={className} style={{ minHeight }} data-testid="native-contribution-host">
        <ViewRenderer
          view={boundView}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => undefined}
          isEditor={lifecycle.mode !== 'detail'}
        />
      </div>
    </ContributionLifecycleContext.Provider>
  );
}
