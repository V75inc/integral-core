import { Columns, LayoutTemplate, Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button, LINE_ICON_STROKE } from '../../components/ui';
import type { SavedView } from '../../types';
import { LAYOUT_PALETTE_TYPES } from './viewDesignerTypes';

type RegionPaletteProps = {
  trackViews: SavedView[];
  onAddForm: () => void;
  onAddView: (viewKey: string, title?: string) => void;
};

function manifestKey(view: SavedView): string | null {
  const cfg = view.config as { _manifest_view_key?: string } | undefined;
  const key = cfg?._manifest_view_key;
  if (typeof key === 'string' && key.trim()) return key.trim();
  const legacy = (view as SavedView & { key?: string }).key;
  if (typeof legacy === 'string' && legacy.trim()) return legacy.trim();
  return null;
}

export function RegionPalette({
  trackViews,
  onAddForm,
  onAddView,
}: RegionPaletteProps) {
  const [selectedViewKey, setSelectedViewKey] = useState('');

  const nestableViews = useMemo(() => {
    return trackViews
      .map(v => {
        const key = manifestKey(v);
        return key
          ? { id: v.id, key, name: v.name || key, type: v.type }
          : null;
      })
      .filter(Boolean) as Array<{
      id: string;
      key: string;
      name: string;
      type: string;
    }>;
  }, [trackViews]);

  return (
    <div data-testid="region-palette" className="flex flex-col gap-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-subtle)]">
        Add region
      </h3>

      <Button
        size="sm"
        variant="outline"
        onClick={onAddForm}
        className="justify-start"
      >
        <Columns size={13} strokeWidth={LINE_ICON_STROKE} />
        Form fields
      </Button>

      <div className="space-y-1.5">
        <label className="text-xs text-[var(--text-muted)]" htmlFor="nested-view-pick">
          Nested saved view
        </label>
        <div className="flex gap-1.5">
          <select
            id="nested-view-pick"
            className="flex-1 min-w-0 text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
            value={selectedViewKey}
            onChange={e => setSelectedViewKey(e.target.value)}
          >
            <option value="">Select view…</option>
            {nestableViews.map(v => (
              <option key={v.id} value={v.key}>
                {v.name} ({v.type})
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="primary"
            disabled={!selectedViewKey}
            onClick={() => {
              const match = nestableViews.find(v => v.key === selectedViewKey);
              if (!match) return;
              onAddView(match.key, match.name);
              setSelectedViewKey('');
            }}
            aria-label="Add nested view region"
          >
            <Plus size={13} />
          </Button>
        </div>
      </div>

      <div className="pt-2 border-t border-[var(--panel-border)]">
        <p className="text-[11px] text-[var(--text-subtle)] mb-2 flex items-center gap-1">
          <LayoutTemplate size={11} strokeWidth={LINE_ICON_STROKE} />
          Region types (reference)
        </p>
        <ul className="space-y-1 text-xs text-[var(--text-muted)]">
          {LAYOUT_PALETTE_TYPES.map(t => (
            <li key={t.type}>{t.label}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
