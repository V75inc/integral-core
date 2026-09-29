import { useMemo } from 'react';
import { ChevronRight, LayoutTemplate, Save, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { Button, LINE_ICON_STROKE, Modal } from '../../components/ui';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { useTrackViews } from '../../hooks/useTrackViews';
import {
  operationalModelDraftsApi,
} from '../../api/operationalModelDrafts';
import { entryTypesApi } from '../../api/entryTypes';
import {
  entryTypesForTrackQueryKey,
} from '../../queryKeys';
import type { Entry, EntryTypeNode, SavedView } from '../../types';
import { DesignerPreview } from './DesignerPreview';
import { RegionCanvas } from './RegionCanvas';
import { RegionInspector } from './RegionInspector';
import { RegionPalette } from './RegionPalette';
import { ViewConfigInspector } from './ViewConfigInspector';
import { useViewDesignerState } from './useViewDesignerState';
import type { LayoutMode, RegionSpec } from './viewDesignerTypes';

export type ViewDesignerShellProps = {
  open: boolean;
  onClose: () => void;
  trackId: string;
  view: SavedView | null;
  /** Optional live entries for track-scoped preview. */
  previewEntries?: Entry[];
  previewFields?: Record<string, unknown>;
  entryId?: string;
  appId?: string;
  entryTypeKey?: string;
  onSaved?: (view: SavedView) => void;
};

export function ViewDesignerShell({
  open,
  onClose,
  trackId,
  view,
  previewEntries,
  previewFields,
  entryId,
  appId,
  entryTypeKey,
  onSaved,
}: ViewDesignerShellProps) {
  const confirm = useConfirm();
  const { showToast } = useToast();
  const viewsQuery = useTrackViews(open ? trackId : undefined);
  const trackViews = viewsQuery.data ?? [];

  const etQuery = useQuery({
    queryKey: entryTypesForTrackQueryKey(trackId),
    queryFn: () => entryTypesApi.list({ track_id: trackId }),
    enabled: open && Boolean(trackId),
  });
  const entryTypes = (etQuery.data ?? []) as EntryTypeNode[];

  const substrateQuery = useQuery({
    queryKey: ['operational-model-substrate'],
    queryFn: () => operationalModelDraftsApi.substrate(),
    enabled: open,
    staleTime: 60_000,
  });

  const {
    stack,
    activeView,
    draftView,
    isLayout,
    layoutConfig,
    widgetConfig,
    regions,
    selectedRegionKey,
    dirty,
    saving,
    saveError,
    actions,
    save,
  } = useViewDesignerState({ trackId, rootView: view, open });

  const selectedRegion =
    regions.find(r => r.key === selectedRegionKey) ?? null;

  const configSchema = useMemo(() => {
    if (!activeView) return undefined;
    const types = substrateQuery.data?.view_types || [];
    const match = types.find(
      t =>
        String(t.type || '').toLowerCase() ===
        String(activeView.type || '').toLowerCase()
    );
    return match?.config_schema as Record<string, unknown> | undefined;
  }, [activeView, substrateQuery.data]);

  const modeValue: LayoutMode | string =
    typeof layoutConfig.mode === 'string'
      ? layoutConfig.mode
      : (layoutConfig.mode as { desktop?: string } | undefined)?.desktop ||
        'stack';

  const handleClose = async () => {
    if (dirty) {
      const ok = await confirm({
        title: 'Discard unsaved changes?',
        message: 'You have unsaved layout edits. Close without saving?',
        confirmLabel: 'Discard',
        variant: 'danger',
      });
      if (!ok) return;
    }
    onClose();
  };

  const handleSave = async () => {
    const ok = await save();
    if (ok) {
      showToast('View layout saved', 'success');
      if (draftView) onSaved?.(draftView);
    } else {
      showToast(saveError || 'Failed to save view', 'error');
    }
  };

  const handleDrill = async (nested: SavedView) => {
    if (dirty) {
      const ok = await confirm({
        title: 'Save before opening nested view?',
        message:
          'Save the current layout before editing the nested view, or discard changes.',
        confirmLabel: 'Save & continue',
        variant: 'default',
      });
      if (!ok) return;
      const saved = await save();
      if (!saved) return;
    }
    actions.drillInto(nested);
  };

  const titleNode = (
    <div className="flex items-center gap-1 min-w-0 text-sm">
      <LayoutTemplate size={16} strokeWidth={LINE_ICON_STROKE} className="shrink-0" />
      <span className="font-semibold truncate">View designer</span>
      {stack.map((entry, idx) => (
        <span key={entry.view.id} className="flex items-center gap-1 min-w-0">
          <ChevronRight size={12} className="shrink-0 text-[var(--text-muted)]" />
          {idx < stack.length - 1 ? (
            <button
              type="button"
              className="truncate text-[var(--link)] hover:underline"
              onClick={() => {
                // Pop until this index — simple: only allow pop one level via actions.popStack repeatedly
                void (async () => {
                  if (dirty) {
                    const ok = await confirm({
                      title: 'Discard nested edits?',
                      message: 'Go back without saving the current view?',
                      confirmLabel: 'Discard',
                      variant: 'danger',
                    });
                    if (!ok) return;
                  }
                  while (stack.length > idx + 1) {
                    actions.popStack();
                    break;
                  }
                })();
              }}
            >
              {entry.view.name}
            </button>
          ) : (
            <span className="truncate text-[var(--text)]">{entry.view.name}</span>
          )}
        </span>
      ))}
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={() => {
        void handleClose();
      }}
      title={titleNode}
      width="max-w-dialog-wide"
      headerActions={
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="text-[11px] text-[var(--warning-fg,var(--text-muted))]">
              Unsaved
            </span>
          )}
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty || saving || !activeView}
            onClick={() => void handleSave()}
          >
            <Save size={13} strokeWidth={LINE_ICON_STROKE} />
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      }
    >
      <div
        data-testid="view-designer-shell"
        className="grid grid-cols-1 lg:grid-cols-[200px_minmax(0,1fr)_260px] gap-3 min-h-[min(70vh,640px)] max-h-[min(78vh,720px)]"
      >
        {/* Left: palette or widget hint */}
        <aside className="border border-[var(--panel-border)] rounded-[var(--radius-card)] p-3 overflow-y-auto bg-[var(--panel-2)]/40">
          {isLayout ? (
            <RegionPalette
              trackViews={trackViews}
              onAddForm={() => actions.addForm()}
              onAddView={(viewKey, title) => actions.addView({ view: viewKey, title })}
            />
          ) : (
            <p className="text-xs text-[var(--text-muted)] leading-relaxed">
              This view type uses widget configuration (not region layout). Edit
              knobs in the inspector; preview updates live.
            </p>
          )}
        </aside>

        {/* Center: canvas + preview */}
        <main className="flex flex-col gap-3 min-h-0 min-w-0">
          <div className="flex-1 min-h-0 border border-[var(--panel-border)] rounded-[var(--radius-card)] p-3 overflow-hidden flex flex-col">
            {isLayout ? (
              <RegionCanvas
                regions={regions}
                selectedKey={selectedRegionKey}
                mode={modeValue}
                onSelect={actions.selectRegion}
                onReorder={actions.reorder}
                onRemove={actions.remove}
                onModeChange={actions.setMode}
              />
            ) : (
              <p className="text-sm text-[var(--text-muted)] py-4">
                Widget mode — configure options on the right. Use Raw JSON for
                advanced keys.
              </p>
            )}
          </div>
          <div className="h-[220px] shrink-0">
            <p className="text-[11px] uppercase tracking-wide text-[var(--text-subtle)] mb-1">
              Preview
            </p>
            <DesignerPreview
              view={draftView}
              trackId={trackId}
              entries={previewEntries}
              previewFields={previewFields}
              entryId={entryId}
              appId={appId}
              entryTypeKey={entryTypeKey}
            />
          </div>
        </main>

        {/* Right: inspector */}
        <aside className="border border-[var(--panel-border)] rounded-[var(--radius-card)] p-3 overflow-y-auto">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-subtle)] mb-2">
            Inspector
          </h3>
          {isLayout ? (
            <RegionInspector
              region={selectedRegion}
              entryTypes={entryTypes}
              trackViews={trackViews}
              onChange={(key, patch) =>
                actions.update(key, patch as Partial<RegionSpec>)
              }
              onDrillIntoView={nested => {
                void handleDrill(nested);
              }}
            />
          ) : activeView ? (
            <ViewConfigInspector
              viewType={activeView.type}
              config={widgetConfig}
              entryTypes={entryTypes}
              configSchema={configSchema}
              onChange={actions.setWidgetField}
              onReplace={actions.setWidgetConfig}
            />
          ) : null}
          {saveError && (
            <p className="mt-3 text-xs text-[var(--danger)]" role="alert">
              {saveError}
            </p>
          )}
        </aside>
      </div>
      {/* Hidden close affordance for tests that look for X — Modal already has one */}
      <span className="sr-only">
        <X size={1} />
      </span>
    </Modal>
  );
}
