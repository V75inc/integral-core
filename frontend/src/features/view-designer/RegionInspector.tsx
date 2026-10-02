import { useMemo } from 'react';
import type { EntryTypeNode, OperationalModelFieldSpec, SavedView } from '../../types';
import type { RegionSpec } from './viewDesignerTypes';
import { fieldEntryKey } from '../../components/views/regionConditions';

type RegionInspectorProps = {
  region: RegionSpec | null;
  entryTypes: EntryTypeNode[];
  trackViews: SavedView[];
  onChange: (key: string, patch: Partial<RegionSpec>) => void;
  onDrillIntoView?: (view: SavedView) => void;
};

function collectFields(entryTypes: EntryTypeNode[]): OperationalModelFieldSpec[] {
  const out: OperationalModelFieldSpec[] = [];
  const seen = new Set<string>();
  for (const et of entryTypes) {
    for (const f of et.form_schema?.fields || []) {
      if (!f.key || seen.has(f.key)) continue;
      seen.add(f.key);
      out.push(f);
    }
  }
  return out;
}

function manifestKey(view: SavedView): string | null {
  const cfg = view.config as { _manifest_view_key?: string } | undefined;
  const key = cfg?._manifest_view_key;
  return typeof key === 'string' && key.trim() ? key.trim() : null;
}

export function RegionInspector({
  region,
  entryTypes,
  trackViews,
  onChange,
  onDrillIntoView,
}: RegionInspectorProps) {
  const fields = useMemo(() => collectFields(entryTypes), [entryTypes]);

  if (!region) {
    return (
      <p className="text-sm text-[var(--text-muted)] py-4">
        Select a region on the canvas to edit its properties.
      </p>
    );
  }

  const selectedFieldKeys =
    region.kind === 'form'
      ? (region.fields || []).map(fieldEntryKey)
      : [];

  const toggleField = (fieldKey: string) => {
    if (region.kind !== 'form') return;
    const current = selectedFieldKeys;
    const next = current.includes(fieldKey)
      ? current.filter(k => k !== fieldKey)
      : [...current, fieldKey];
    onChange(region.key, { fields: next } as Partial<RegionSpec>);
  };

  const nestedView =
    region.kind === 'view'
      ? trackViews.find(v => manifestKey(v) === region.view)
      : null;

  return (
    <div data-testid="region-inspector" className="space-y-3">
      <div>
        <label className="text-xs text-[var(--text-muted)]" htmlFor="region-title">
          Title
        </label>
        <input
          id="region-title"
          className="mt-1 w-full text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
          value={region.title || ''}
          onChange={e => onChange(region.key, { title: e.target.value })}
        />
      </div>

      <div className="text-xs text-[var(--text-muted)]">
        Key: <span className="font-mono text-[var(--text)]">{region.key}</span>
        <span className="mx-1">·</span>
        Kind: <span className="font-mono text-[var(--text)]">{region.kind}</span>
      </div>

      {region.kind === 'form' && (
        <>
          <div>
            <label className="text-xs text-[var(--text-muted)]" htmlFor="region-cols">
              Columns
            </label>
            <input
              id="region-cols"
              type="number"
              min={1}
              max={4}
              className="mt-1 w-20 text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
              value={region.columns ?? 2}
              onChange={e =>
                onChange(region.key, {
                  columns: Math.max(1, Math.min(4, Number(e.target.value) || 1)),
                } as Partial<RegionSpec>)
              }
            />
          </div>
          <fieldset>
            <legend className="text-xs text-[var(--text-muted)] mb-1.5">Fields</legend>
            <div className="max-h-48 overflow-y-auto space-y-1 border border-[var(--panel-border)] rounded-[var(--radius-input)] p-2">
              {fields.length === 0 ? (
                <p className="text-xs text-[var(--text-muted)]">No fields on this track.</p>
              ) : (
                fields.map(f => (
                  <label
                    key={f.key}
                    className="flex items-center gap-2 text-sm text-[var(--text)] cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedFieldKeys.includes(f.key)}
                      onChange={() => toggleField(f.key)}
                    />
                    <span className="truncate">{f.name || f.key}</span>
                    <span className="text-[11px] text-[var(--text-muted)] font-mono shrink-0">
                      {f.key}
                    </span>
                  </label>
                ))
              )}
            </div>
          </fieldset>
        </>
      )}

      {region.kind === 'view' && (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-[var(--text-muted)]" htmlFor="region-view-key">
              Nested view key
            </label>
            <select
              id="region-view-key"
              className="mt-1 w-full text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
              value={region.view || ''}
              onChange={e =>
                onChange(region.key, { view: e.target.value } as Partial<RegionSpec>)
              }
            >
              <option value="">Select…</option>
              {trackViews.map(v => {
                const key = manifestKey(v);
                if (!key) return null;
                return (
                  <option key={v.id} value={key}>
                    {v.name} ({key})
                  </option>
                );
              })}
            </select>
          </div>
          {nestedView && onDrillIntoView && (
            <button
              type="button"
              className="text-sm text-[var(--link)] hover:underline"
              onClick={() => onDrillIntoView(nestedView)}
            >
              Open nested view in designer →
            </button>
          )}
        </div>
      )}
    </div>
  );
}
