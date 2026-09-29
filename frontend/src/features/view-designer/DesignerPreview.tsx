import { useEffect, useState } from 'react';
import {
  ContributionLifecycleContext,
  type ContributionLifecycleApi,
  type ContributionLifecycleHandle,
} from '../../components/entries/contributionLifecycle';
import { ViewRenderer } from '../../views/registry';
import type { Entry, SavedView } from '../../types';

type DesignerPreviewProps = {
  view: SavedView | null;
  trackId: string;
  entries?: Entry[];
  previewFields?: Record<string, unknown>;
  entryId?: string;
  appId?: string;
  entryTypeKey?: string;
};

export function DesignerPreview({
  view,
  trackId,
  entries = [],
  previewFields,
  entryId,
  appId,
  entryTypeKey,
}: DesignerPreviewProps) {
  const [customFields, setCustomFields] = useState<Record<string, unknown>>(
    () => ({ ...(previewFields || {}) })
  );

  useEffect(() => {
    setCustomFields({ ...(previewFields || {}) });
  }, [previewFields]);

  const lifecycle: ContributionLifecycleApi = {
    mode: 'edit',
    placement: 'entry_detail',
    entryId,
    trackId,
    entryTypeKey,
    appId,
    customFields,
    onDraftPatch: patch => {
      if (patch.custom_fields) {
        setCustomFields(prev => ({ ...prev, ...patch.custom_fields }));
      }
    },
    register: (_handle: ContributionLifecycleHandle) => () => undefined,
  };

  if (!view) {
    return (
      <div
        data-testid="designer-preview-empty"
        className="h-full flex items-center justify-center text-sm text-[var(--text-muted)]"
      >
        No view selected
      </div>
    );
  }

  const boundView: SavedView = {
    ...view,
    track_id: trackId,
    config: {
      ...(view.config || {}),
      __bindings: {
        entryId,
        entryValues: customFields,
        __contributionMode: 'edit',
        __contributionPlacement: 'entry_detail',
        entry_type_key: entryTypeKey,
        trackId,
        appId,
      },
    },
  };

  return (
    <div
      data-testid="designer-preview"
      className="h-full overflow-auto rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)] p-3"
    >
      <ContributionLifecycleContext.Provider value={lifecycle}>
        <ViewRenderer
          view={boundView}
          entries={entries}
          isLoading={false}
          onEntryOpen={() => undefined}
          isEditor
        />
      </ContributionLifecycleContext.Provider>
    </div>
  );
}
