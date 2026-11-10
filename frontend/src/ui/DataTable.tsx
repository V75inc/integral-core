/**
 * DataTable — rows and columns for an App's own data (calculated or fetched), drawn like Core's native table views.
 *
 * Sorting, paging, a left-pinned first column, right-aligned numbers and a totals row are built in so Apps do
 * not draw tables themselves. Apps pass plain rows; they never style cells.
 *
 * Layer: Primitive (Layer 1).
 */

import { useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { Button } from '../components/ui/Button';

export interface DataTableColumn<Row = Record<string, unknown>> {
  key: string;
  label: ReactNode;
  /** `end` right-aligns the column and uses tabular figures: use it for amounts. */
  align?: 'start' | 'end';
  sortable?: boolean;
  /** Sort by this instead of `row[key]`. */
  sortValue?: (row: Row) => string | number | null | undefined;
  /** Keep this column in view while the table scrolls sideways. Only leading columns can be pinned. */
  pinned?: boolean;
  /** CSS width, for example `10rem`. Pinned columns default to `10rem`. */
  width?: string;
  render?: (row: Row) => ReactNode;
  /** Cell for the totals row; the row shows when any column has one. */
  total?: ReactNode;
}

export interface DataTableSort { key: string; direction: 'asc' | 'desc' }

export interface DataTableProps<Row = Record<string, unknown>> {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** Names the table for assistive technology. */
  caption: string;
  /** Start sorted this way. The user can change it on sortable columns. */
  defaultSort?: DataTableSort | null;
  /** Rows per page; omit to show every row. */
  pageSize?: number;
  onRowClick?: (row: Row) => void;
  /** Shown when there are no rows. */
  empty?: ReactNode;
  /** Scroll the body inside this height (CSS value) and keep the header in view. */
  maxHeight?: string;
}

const PIN_DEFAULT = '10rem';
const compare = (a: unknown, b: unknown) => {
  const missingA = a === null || a === undefined || a === '', missingB = b === null || b === undefined || b === '';
  if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1; // empty values sort last
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
};

export function DataTable<Row extends object = Record<string, unknown>>({
  columns, rows, rowKey, caption, defaultSort = null, pageSize, onRowClick, empty, maxHeight,
}: DataTableProps<Row>) {
  const [sort, setSort] = useState<DataTableSort | null>(defaultSort);
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => {
    const column = sort ? columns.find(c => c.key === sort.key) : undefined;
    if (!sort || !column) return rows;
    const value = (row: Row) => column.sortValue ? column.sortValue(row) : (row as Record<string, unknown>)[column.key];
    const factor = sort.direction === 'desc' ? -1 : 1;
    return [...rows].sort((x, y) => {
      const a = value(x), b = value(y);
      const missing = a === null || a === undefined || a === '' || b === null || b === undefined || b === '';
      return missing ? compare(a, b) : factor * compare(a, b); // empty values stay last either way
    });
  }, [rows, columns, sort]);
  const pages = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pages - 1);
  const shown = pageSize ? sorted.slice(current * pageSize, (current + 1) * pageSize) : sorted;
  const pinOffsets: Record<string, string> = {};
  let offset = '0rem';
  for (const c of columns) {
    if (!c.pinned) break;
    pinOffsets[c.key] = offset;
    offset = `calc(${offset} + ${c.width || PIN_DEFAULT})`;
  }
  const hasTotals = columns.some(c => c.total !== undefined);
  const toggle = (key: string) => {
    setPage(0);
    setSort(previous => previous?.key !== key ? { key, direction: 'asc' } : previous.direction === 'asc' ? { key, direction: 'desc' } : null);
  };
  const cellClass = (c: DataTableColumn<Row>, header = false) =>
    [header ? 'px-4 py-3 text-xs font-semibold text-[var(--text-muted)]' : 'px-4 py-3', c.align === 'end' ? 'text-right tabular-nums' : 'text-left'].join(' ');
  const pinStyle = (c: DataTableColumn<Row>, background: string) => c.pinned
    ? { position: 'sticky' as const, left: pinOffsets[c.key], zIndex: 1, background, minWidth: c.width || PIN_DEFAULT, maxWidth: c.width || PIN_DEFAULT }
    : c.width ? { minWidth: c.width } : undefined;
  const onKey = (event: KeyboardEvent<HTMLTableRowElement>, row: Row) => {
    if (onRowClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onRowClick(row); }
  };
  return (
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-[var(--entry-table-border)] bg-[var(--panel)] shadow-[var(--shadow-card)]">
      <div className="overflow-auto" style={maxHeight ? { maxHeight } : undefined}>
        <table className="w-full min-w-[600px] text-sm text-[var(--text)]">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-[var(--entry-table-border)]">
              {columns.map(c => (
                <th key={c.key} scope="col" aria-sort={sort?.key === c.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={cellClass(c, true)}
                  style={{ ...(pinStyle(c, 'var(--entry-table-header)') || {}), ...(maxHeight && !c.pinned ? { position: 'sticky', top: 0, zIndex: 1 } : {}), background: 'var(--entry-table-header)', ...(maxHeight && c.pinned ? { top: 0, zIndex: 2 } : {}) }}>
                  {c.sortable ? (
                    <button type="button" onClick={() => toggle(c.key)} className={`inline-flex items-center gap-1 whitespace-nowrap hover:text-[var(--text)] ${c.align === 'end' ? 'flex-row-reverse' : ''}`}>
                      <span>{c.label}</span>
                      {sort?.key === c.key ? <ChevronDown size={12} className={sort.direction === 'desc' ? 'rotate-180' : ''} aria-hidden="true" /> : <span className="inline-block w-3" aria-hidden="true" />}
                    </button>
                  ) : <span className="whitespace-nowrap">{c.label}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">{empty ?? 'Nothing to show.'}</td></tr>
            ) : shown.map(row => (
              <tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined} onKeyDown={onRowClick ? e => onKey(e, row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={`border-b border-[var(--entry-table-divider)] last:border-b-0 ${onRowClick ? 'cursor-pointer hover:bg-[var(--entry-table-row-hover)] focus-visible:bg-[var(--entry-table-row-hover)] focus-visible:outline-none' : ''}`}>
                {columns.map(c => (
                  <td key={c.key} className={cellClass(c)} style={pinStyle(c, 'var(--panel)')}>
                    {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {hasTotals ? (
            <tfoot>
              <tr className="border-t border-[var(--entry-table-border)] font-semibold">
                {columns.map(c => <td key={c.key} className={cellClass(c)} style={pinStyle(c, 'var(--entry-table-header)')}>{c.total}</td>)}
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      {pageSize && sorted.length > pageSize ? (
        <div className="flex items-center justify-between gap-2 border-t border-[var(--entry-table-divider)] bg-[var(--entry-table-toolbar)] px-4 py-2.5 text-xs text-[var(--text-muted)]">
          <span>Showing {current * pageSize + 1}–{Math.min(sorted.length, (current + 1) * pageSize)} of {sorted.length}</span>
          <span className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</Button>
            <Button size="sm" variant="outline" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>Next</Button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
