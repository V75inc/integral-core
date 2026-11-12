import type { OperationalModelFieldSpec } from '../../types';
import { humanizeEnumValue } from '../../utils/humanizeFieldKey';
import type { FieldFilter } from '../tracks/fieldFilters';

/** What a column can be filtered by, and how a filter on it reads. Built from the Track's own field definitions. */

export type FilterKind = 'text' | 'number' | 'date' | 'datetime' | 'select' | 'boolean';

export interface ColumnFilterSpec {
  /** The stored path the server filters on. */
  field: string;
  label: string;
  kind: FilterKind;
  options: string[];
  labels?: Record<string, string>;
}

/** The stored path the server filters a column on. */
export function columnPath(field: string, fields: OperationalModelFieldSpec[]): string {
  if (field.startsWith('custom_fields.') || field.startsWith('context.')) return field;
  return fields.some(f => f.key === field) ? `custom_fields.${field}` : field;
}

/** The filter a column offers, or null when it has no simple value to compare (links to other records). */
export function columnFilterSpec(
  col: { field: string; label: string; format?: string },
  fields: OperationalModelFieldSpec[],
): ColumnFilterSpec | null {
  const key = col.field.replace(/^custom_fields\./u, '');
  const spec = fields.find(f => f.key === key);
  const path = columnPath(col.field, fields);
  if (spec) {
    if (!['text', 'number', 'date', 'datetime', 'select', 'boolean'].includes(spec.type)) return null;
    return {
      field: path, label: col.label, kind: spec.type as FilterKind,
      options: spec.type === 'select' ? (spec.enum ?? []).map(String) : [], labels: spec.enum_labels,
    };
  }
  if (col.field === 'title') return { field: 'title', label: col.label, kind: 'text', options: [] };
  if (col.field === 'created_at' || col.field === 'updated_at') return { field: col.field, label: col.label, kind: 'datetime', options: [] };
  if (col.format === 'number' || col.format === 'currency') return { field: path, label: col.label, kind: 'number', options: [] };
  return null;
}

export const optionLabel = (spec: ColumnFilterSpec, value: string) => spec.labels?.[value] ?? humanizeEnumValue(value);

export const onField = (filters: FieldFilter[], field: string) => filters.filter(f => f.field === field);
export const withoutField = (filters: FieldFilter[], field: string) => filters.filter(f => f.field !== field);

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Common date ranges, worked out from today. */
export const DATE_PRESETS: Array<{ key: string; label: string; range: (now: Date) => [string, string] }> = [
  { key: 'today', label: 'Today', range: n => [iso(n), iso(n)] },
  { key: 'last7', label: 'Last 7 days', range: n => [iso(new Date(n.getFullYear(), n.getMonth(), n.getDate() - 6)), iso(n)] },
  { key: 'month', label: 'This month', range: n => [iso(new Date(n.getFullYear(), n.getMonth(), 1)), iso(new Date(n.getFullYear(), n.getMonth() + 1, 0))] },
  { key: 'lastmonth', label: 'Last month', range: n => [iso(new Date(n.getFullYear(), n.getMonth() - 1, 1)), iso(new Date(n.getFullYear(), n.getMonth(), 0))] },
  { key: 'year', label: 'This year', range: n => [iso(new Date(n.getFullYear(), 0, 1)), iso(new Date(n.getFullYear(), 11, 31))] },
];

/** An "on or before" bound that includes the whole last day of a timestamp. */
export const endOfDay = (kind: FilterKind, day: string) => (kind === 'datetime' && day ? `${day}T23:59:59.999` : day);

const day = (v: unknown) => String(v ?? '').slice(0, 10);
const num = (v: unknown) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
const fmtDay = (v: unknown) => {
  const d = new Date(`${day(v)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? day(v) : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

/** One chip's text for everything filtered on one column: "Gross: 200,000 – 300,000", "Status: Paid, Approved". */
export function describeColumnFilter(spec: ColumnFilterSpec, filters: FieldFilter[]): string {
  const get = (op: string) => filters.find(f => f.op === op)?.value;
  const gte = get('gte'), lte = get('lte');
  switch (spec.kind) {
    case 'text': {
      const exact = get('eq');
      return exact !== undefined ? `${spec.label}: ${exact}` : `${spec.label} contains “${get('contains')}”`;
    }
    case 'number':
      if (gte !== undefined && lte !== undefined) return `${spec.label}: ${num(gte)} – ${num(lte)}`;
      if (gte !== undefined) return `${spec.label} ≥ ${num(gte)}`;
      if (lte !== undefined) return `${spec.label} ≤ ${num(lte)}`;
      return `${spec.label} = ${num(get('eq'))}`;
    case 'date':
    case 'datetime':
      if (gte !== undefined && lte !== undefined) return day(gte) === day(lte) ? `${spec.label}: ${fmtDay(gte)}` : `${spec.label}: ${fmtDay(gte)} – ${fmtDay(lte)}`;
      if (gte !== undefined) return `${spec.label} from ${fmtDay(gte)}`;
      if (lte !== undefined) return `${spec.label} to ${fmtDay(lte)}`;
      return `${spec.label}: ${fmtDay(get('eq'))}`;
    case 'select': {
      const values = ((get('in') as unknown[]) ?? [get('eq')]).map(v => optionLabel(spec, String(v)));
      return `${spec.label}: ${values.join(', ')}`;
    }
    case 'boolean':
      return `${spec.label}: ${get('eq') ? 'Yes' : 'No'}`;
  }
}
