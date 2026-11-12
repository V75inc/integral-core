import { useEffect, useState } from 'react';
import type { OperationalModelFieldSpec } from '../../types';
import { humanizeEnumValue } from '../../utils/humanizeFieldKey';
import { filterValue, withFilter, type FieldFilter } from '../tracks/fieldFilters';

/**
 * One filter control per visible table column, in a row under the headings: text contains, number min/max,
 * date from/to, and a list for choices and yes/no. The value is typed into the column it filters, so what you
 * filter is what you see. Columns with no simple value to compare (links to other records) get no control.
 */

type Kind = 'text' | 'number' | 'date' | 'datetime' | 'select' | 'boolean';

export interface ColumnFilterSpec {
  field: string;
  kind: Kind;
  options: string[];
}

/** The stored path the server filters a column on. */
export function columnPath(field: string, fields: OperationalModelFieldSpec[]): string {
  if (field.startsWith('custom_fields.') || field.startsWith('context.')) return field;
  return fields.some(f => f.key === field) ? `custom_fields.${field}` : field;
}

export function columnFilterSpec(
  col: { field: string; format?: string },
  fields: OperationalModelFieldSpec[],
): ColumnFilterSpec | null {
  const key = col.field.replace(/^custom_fields\./u, '');
  const spec = fields.find(f => f.key === key);
  const path = columnPath(col.field, fields);
  if (spec) {
    if (['text', 'number', 'date', 'datetime', 'select', 'boolean'].includes(spec.type)) {
      return { field: path, kind: spec.type as Kind, options: spec.type === 'select' ? (spec.enum ?? []).map(String) : [] };
    }
    return null;
  }
  if (col.field === 'title') return { field: 'title', kind: 'text', options: [] };
  if (col.field === 'created_at' || col.field === 'updated_at') return { field: col.field, kind: 'datetime', options: [] };
  if (col.format === 'number' || col.format === 'currency') return { field: path, kind: 'number', options: [] };
  return null;
}

const control =
  'h-8 w-full min-w-0 rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 text-xs text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none focus:border-[var(--text-muted)]';

/** A box that sends its value to the server shortly after typing stops. */
function Debounced({
  value, onCommit, type = 'text', placeholder, label,
}: { value: string; onCommit(v: string): void; type?: string; placeholder?: string; label: string }) {
  const [text, setText] = useState(value);
  useEffect(() => { setText(value); }, [value]);
  useEffect(() => {
    if (text === value) return;
    const timer = setTimeout(() => onCommit(text), type === 'text' || type === 'number' ? 450 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return (
    <input
      type={type}
      value={text}
      placeholder={placeholder}
      aria-label={label}
      onChange={e => setText(e.target.value)}
      onClick={e => e.stopPropagation()}
      className={control}
    />
  );
}

const toNumber = (raw: string): number | undefined => {
  if (raw.trim() === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
};
const str = (v: unknown) => (v === undefined || v === null ? '' : String(v));
/** An "on or before" bound that includes the whole last day of a timestamp. */
const endOfDay = (kind: Kind, day: string) => (kind === 'datetime' && day ? `${day}T23:59:59.999` : day);
const dayOf = (v: unknown) => str(v).slice(0, 10);

export function ColumnFilterRow({
  columns, fields, filters, onChange, className,
}: {
  columns: Array<{ field: string; label: string; format?: string }>;
  fields: OperationalModelFieldSpec[];
  filters: FieldFilter[];
  onChange(next: FieldFilter[]): void;
  className?: string;
}) {
  const set = (path: string, op: string, value: unknown) => onChange(withFilter(filters, path, op, value));
  return (
    <tr className={className} data-testid="column-filter-row">
      {columns.map(col => {
        const spec = columnFilterSpec(col, fields);
        return (
          <th key={col.field} className="px-3 py-1.5 align-top font-normal">
            {spec ? renderControl(spec, col.label, filters, set) : null}
          </th>
        );
      })}
    </tr>
  );
}

function renderControl(
  spec: ColumnFilterSpec,
  label: string,
  filters: FieldFilter[],
  set: (path: string, op: string, value: unknown) => void,
) {
  const { field, kind } = spec;
  switch (kind) {
    case 'text':
      return (
        <div className="min-w-[6rem]"><Debounced label={`Filter ${label}`} placeholder="Contains…" value={str(filterValue(filters, field, 'contains'))}
          onCommit={v => set(field, 'contains', v.trim())} /></div>
      );
    case 'number':
      return (
        <div className="flex min-w-[6rem] flex-col gap-1">
          <Debounced type="number" label={`${label} at least`} placeholder="Min" value={str(filterValue(filters, field, 'gte'))}
            onCommit={v => set(field, 'gte', toNumber(v))} />
          <Debounced type="number" label={`${label} at most`} placeholder="Max" value={str(filterValue(filters, field, 'lte'))}
            onCommit={v => set(field, 'lte', toNumber(v))} />
        </div>
      );
    case 'date':
    case 'datetime':
      return (
        <div className="flex min-w-[7.5rem] flex-col gap-1">
          <Debounced type="date" label={`${label} from`} value={dayOf(filterValue(filters, field, 'gte'))}
            onCommit={v => set(field, 'gte', v)} />
          <Debounced type="date" label={`${label} to`} value={dayOf(filterValue(filters, field, 'lte'))}
            onCommit={v => set(field, 'lte', endOfDay(kind, v))} />
        </div>
      );
    case 'select':
      return (
        <select aria-label={`Filter ${label}`} className={`${control} min-w-[5rem]`} value={str(filterValue(filters, field, 'eq'))}
          onClick={e => e.stopPropagation()} onChange={e => set(field, 'eq', e.target.value)}>
          <option value="">All</option>
          {spec.options.map(o => <option key={o} value={o}>{humanizeEnumValue(o)}</option>)}
        </select>
      );
    case 'boolean': {
      const current = filterValue(filters, field, 'eq');
      return (
        <select aria-label={`Filter ${label}`} className={`${control} min-w-[5rem]`} value={current === undefined ? '' : String(current)}
          onClick={e => e.stopPropagation()}
          onChange={e => set(field, 'eq', e.target.value === '' ? undefined : e.target.value === 'true')}>
          <option value="">Any</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      );
    }
  }
}
