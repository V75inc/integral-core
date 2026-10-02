import { useMemo } from 'react';
import type { EntryTypeNode, OperationalModelFieldSpec } from '../../types';

type ViewConfigInspectorProps = {
  viewType: string;
  config: Record<string, unknown>;
  entryTypes: EntryTypeNode[];
  configSchema?: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onReplace: (next: Record<string, unknown>) => void;
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

type FilterRule = { field: string; op: string; value: unknown };

const FILTER_OPS = ['eq', 'neq', 'in', 'contains', 'lt', 'gt', 'lte', 'gte'];

function FieldSelect({
  id,
  value,
  fields,
  onChange,
  allowEmpty,
}: {
  id: string;
  value: string;
  fields: OperationalModelFieldSpec[];
  onChange: (v: string) => void;
  allowEmpty?: boolean;
}) {
  return (
    <select
      id={id}
      className="w-full text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
      value={value}
      onChange={e => onChange(e.target.value)}
    >
      {allowEmpty && <option value="">—</option>}
      <option value="created_at">created_at</option>
      <option value="updated_at">updated_at</option>
      <option value="title">title</option>
      {fields.map(f => (
        <option key={f.key} value={f.key}>
          {f.name || f.key} ({f.key})
        </option>
      ))}
    </select>
  );
}

export function ViewConfigInspector({
  viewType,
  config,
  entryTypes,
  configSchema,
  onChange,
  onReplace,
}: ViewConfigInspectorProps) {
  const fields = useMemo(() => collectFields(entryTypes), [entryTypes]);
  const typeLower = viewType.toLowerCase();

  const groupBy =
    typeof config.group_by === 'string'
      ? config.group_by
      : typeof (config as { groupBy?: string }).groupBy === 'string'
        ? String((config as { groupBy?: string }).groupBy)
        : '';

  const filterRules: FilterRule[] = Array.isArray(
    (config.filter as { rules?: FilterRule[] } | undefined)?.rules
  )
    ? ((config.filter as { rules: FilterRule[] }).rules || [])
    : [];

  const projection = Array.isArray(config.projection)
    ? (config.projection as string[])
    : [];

  const schemaKeys = configSchema ? Object.keys(configSchema) : [];

  return (
    <div data-testid="view-config-inspector" className="space-y-4">
      <p className="text-xs text-[var(--text-muted)]">
        Type: <span className="font-mono text-[var(--text)]">{viewType}</span>
      </p>

      {(typeLower.includes('kanban') ||
        typeLower.includes('board') ||
        typeLower.includes('grouped') ||
        'group_by' in config ||
        schemaKeys.includes('group_by')) && (
        <div>
          <label className="text-xs text-[var(--text-muted)]" htmlFor="cfg-group-by">
            Group by
          </label>
          <div className="mt-1">
            <FieldSelect
              id="cfg-group-by"
              value={groupBy.replace(/^custom_fields\./, '')}
              fields={fields}
              allowEmpty
              onChange={v =>
                onChange(
                  'group_by',
                  v
                    ? v.startsWith('custom_fields.') ||
                      v === 'created_at' ||
                      v === 'updated_at' ||
                      v === 'title'
                      ? v
                      : `custom_fields.${v}`
                    : undefined
                )
              }
            />
          </div>
        </div>
      )}

      {(typeLower.includes('calendar') || 'calendar_mapping' in config) && (
        <div>
          <label className="text-xs text-[var(--text-muted)]" htmlFor="cfg-date-field">
            Date field
          </label>
          <div className="mt-1">
            <FieldSelect
              id="cfg-date-field"
              value={
                String(
                  (config.calendar_mapping as { date_field?: string } | undefined)
                    ?.date_field ||
                    config.date_field ||
                    'created_at'
                )
              }
              fields={fields.filter(f =>
                ['date', 'datetime'].includes(String(f.type || '').toLowerCase())
              )}
              onChange={v =>
                onChange('calendar_mapping', {
                  ...((config.calendar_mapping as object) || {}),
                  date_field: v,
                })
              }
            />
          </div>
        </div>
      )}

      {(typeLower.includes('composable') ||
        typeLower.includes('list') ||
        typeLower.includes('grid') ||
        'filter' in config ||
        schemaKeys.includes('filter')) && (
        <fieldset>
          <legend className="text-xs text-[var(--text-muted)] mb-1.5">
            Filter rules
          </legend>
          <div className="space-y-2">
            {filterRules.map((rule, idx) => (
              <div
                key={idx}
                className="grid grid-cols-[1fr_auto_1fr_auto] gap-1 items-center"
              >
                <FieldSelect
                  id={`filter-field-${idx}`}
                  value={String(rule.field || '').replace(/^custom_fields\./, '')}
                  fields={fields}
                  onChange={v => {
                    const next = [...filterRules];
                    next[idx] = {
                      ...rule,
                      field: v.includes('.') ? v : `custom_fields.${v}`,
                    };
                    onChange('filter', { rules: next });
                  }}
                />
                <select
                  className="text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-1 py-1.5"
                  value={rule.op || 'eq'}
                  onChange={e => {
                    const next = [...filterRules];
                    next[idx] = { ...rule, op: e.target.value };
                    onChange('filter', { rules: next });
                  }}
                  aria-label={`Filter op ${idx}`}
                >
                  {FILTER_OPS.map(op => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>
                <input
                  className="text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
                  value={
                    typeof rule.value === 'string' || typeof rule.value === 'number'
                      ? String(rule.value)
                      : JSON.stringify(rule.value ?? '')
                  }
                  onChange={e => {
                    const next = [...filterRules];
                    next[idx] = { ...rule, value: e.target.value };
                    onChange('filter', { rules: next });
                  }}
                  aria-label={`Filter value ${idx}`}
                />
                <button
                  type="button"
                  className="text-xs text-[var(--danger)] px-1"
                  onClick={() => {
                    const next = filterRules.filter((_, i) => i !== idx);
                    onChange('filter', next.length ? { rules: next } : undefined);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="text-xs text-[var(--link)]"
              onClick={() =>
                onChange('filter', {
                  rules: [
                    ...filterRules,
                    { field: fields[0]?.key ? `custom_fields.${fields[0].key}` : 'title', op: 'eq', value: '' },
                  ],
                })
              }
            >
              + Add rule
            </button>
          </div>
        </fieldset>
      )}

      {(typeLower.includes('composable') ||
        'projection' in config ||
        schemaKeys.includes('projection')) && (
        <fieldset>
          <legend className="text-xs text-[var(--text-muted)] mb-1.5">
            Projection fields
          </legend>
          <div className="max-h-36 overflow-y-auto space-y-1 border border-[var(--panel-border)] rounded-[var(--radius-input)] p-2">
            {fields.map(f => {
              const checked = projection.includes(f.key);
              return (
                <label
                  key={f.key}
                  className="flex items-center gap-2 text-sm cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const next = checked
                        ? projection.filter(k => k !== f.key)
                        : [...projection, f.key];
                      onChange('projection', next.length ? next : undefined);
                    }}
                  />
                  {f.name || f.key}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}

      {(typeLower.includes('chart') || 'chart_type' in config) && (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-[var(--text-muted)]" htmlFor="chart-type">
              Chart type
            </label>
            <select
              id="chart-type"
              className="mt-1 w-full text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1.5"
              value={String(config.chart_type || 'bar')}
              onChange={e => onChange('chart_type', e.target.value)}
            >
              {['bar', 'line', 'area', 'pie', 'donut', 'scatter', 'gauge'].map(t => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-[var(--text-muted)]" htmlFor="chart-x">
              X field
            </label>
            <div className="mt-1">
              <FieldSelect
                id="chart-x"
                value={String(config.x_field || '')}
                fields={fields}
                allowEmpty
                onChange={v => onChange('x_field', v || undefined)}
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-[var(--text-muted)]" htmlFor="chart-y">
              Y field
            </label>
            <div className="mt-1">
              <FieldSelect
                id="chart-y"
                value={String(config.y_field || '')}
                fields={fields}
                allowEmpty
                onChange={v => onChange('y_field', v || undefined)}
              />
            </div>
          </div>
        </div>
      )}

      {/* Generic schema string fields fallback */}
      {schemaKeys.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-[var(--text-muted)]">
            Advanced config keys ({schemaKeys.length})
          </summary>
          <ul className="mt-2 space-y-1 font-mono text-[var(--text-subtle)]">
            {schemaKeys.map(k => (
              <li key={k}>{k}</li>
            ))}
          </ul>
        </details>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-[var(--text-muted)]">Raw JSON</summary>
        <textarea
          className="mt-2 w-full h-32 font-mono text-[11px] rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel-2)] p-2"
          value={JSON.stringify(config, null, 2)}
          onChange={e => {
            try {
              const parsed = JSON.parse(e.target.value) as Record<string, unknown>;
              onReplace(parsed);
            } catch {
              /* ignore while typing */
            }
          }}
          spellCheck={false}
        />
      </details>
    </div>
  );
}
