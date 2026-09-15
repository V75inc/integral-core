import { useCallback, useEffect, useMemo, useState } from 'react';
import { SeamlessField } from '../entries/SeamlessField';
import { entriesApi, entryTypesApi } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Surface, Text } from '../../ui';
import type { ViewWidgetProps } from './types';
import type { ContentProfileFieldSpec, Entry, EntryTypeNode } from '../../types';

/**
 * Guyana Payroll's tabular register — grouped (Employees/Consultants)
 * spreadsheet-style view over a Pay Run's Pay Run Lines, laid out to match
 * the real payroll sheet: identity columns resolved through the row's
 * ``employee`` relation (Employee Number, Role, Names, Address, DOB,
 * National ID, TIN, NIS #, Bank, Bank Account #), editable per-period
 * input columns (Bonus %, Taxable Deduction, Child Allowance, Deduction,
 * Reimbursement, Remarks), server-computed read-only columns (Basic
 * through Take-Home), a subtotal row per group, and one grand-total row.
 *
 * Deliberately a NEW widget rather than an extension of
 * ``EditableTableWidget`` — that widget renders one flat ``<tbody>`` with
 * no grouping/subtotal concept, and several other apps (NIS/PAYE filing
 * lines) already depend on it staying exactly as it is. This one reuses
 * its two genuinely-reusable pieces: ``SeamlessField`` for inline cell
 * editing, and the same ``commitCell`` API-call shape (optimistic update,
 * PATCH custom_fields, rollback on failure).
 *
 * All math is server-computed (``pay_run_line_calc.py``'s
 * ``recalc_pay_run_line`` hook, fired on every save) — this widget never
 * computes gross-to-net itself; subtotal/grand-total rows here are
 * display-only sums of whatever the server already wrote, same
 * "don't trust the client for money" posture as the rest of this app
 * (the persisted total of record is the backend rollup onto the parent
 * Pay Run's gross_total/headcount).
 */

interface IdentityColumnConfig {
  key: string;
  label: string;
  source_field: string;
  relation_field?: string;
}

interface SimpleColumnConfig {
  key: string;
  label?: string;
}

type ColumnEntry = string | SimpleColumnConfig;

function columnKey(entry: ColumnEntry): string {
  return typeof entry === 'string' ? entry : entry.key;
}

function columnLabel(entry: ColumnEntry, fieldByKey: Map<string, ContentProfileFieldSpec>): string {
  if (typeof entry !== 'string' && entry.label) return entry.label;
  const key = columnKey(entry);
  return fieldByKey.get(key)?.name || key;
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(value);
}

/**
 * Columns that are legitimately always (or almost always) zero under
 * current rules read as broken/blank otherwise — a bare "0.00" gives no
 * hint that it's expected, not a missed calculation. `govt_allowance` is
 * the first case: under real GRA rules the personal allowance is folded
 * into the PAYE bands' own 0%-rate first bracket, so this field (kept on
 * the schema for back-compat) computes to 0 for every row. Swap in a
 * short explanatory note instead of the raw number when it's exactly 0 —
 * a genuinely nonzero value (e.g. if the rule ever changes) still renders
 * as a normal number, nothing here hides real data.
 */
const ZERO_EXPLANATIONS: Record<string, string> = {
  govt_allowance: 'Incl. in PAYE',
};

function formatComputedValue(key: string, value: unknown): { text: string; note?: string } {
  const explanation = ZERO_EXPLANATIONS[key];
  if (explanation && typeof value === 'number' && value === 0) {
    return { text: explanation };
  }
  return { text: formatValue(value) };
}

/** Payroll lines are persisted as cents; sum cents, never binary floats. */
export function sumMoney(values: unknown[]): number {
  return values.reduce<number>((totalCents, value) => {
    const amount = Number(value);
    return Number.isFinite(amount) ? totalCents + Math.round(amount * 100) : totalCents;
  }, 0) / 100;
}


export function PayrollRegisterWidget({ entries, view, isLoading }: ViewWidgetProps) {
  const { showToast } = useToast();
  const [entryTypes, setEntryTypes] = useState<EntryTypeNode[]>([]);
  const [loadingTypes, setLoadingTypes] = useState(true);
  const [rows, setRows] = useState<Entry[]>(entries);
  const [savingCell, setSavingCell] = useState<string | null>(null);
  const [employeesById, setEmployeesById] = useState<Record<string, Entry>>({});

  useEffect(() => setRows(entries), [entries]);

  useEffect(() => {
    let cancelled = false;
    setLoadingTypes(true);
    entryTypesApi
      .list({ track_id: view.track_id })
      .then(types => {
        if (!cancelled) setEntryTypes(types);
      })
      .catch(() => {
        if (!cancelled) setEntryTypes([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingTypes(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view.track_id]);

  const activeEntryType = useMemo<EntryTypeNode | undefined>(() => {
    if (!entryTypes.length) return undefined;
    const wantKey = view.default_entry_type_key;
    if (wantKey) {
      const byManifestKey = entryTypes.find(
        et => et.form_schema?._manifest_entry_type_key === wantKey
      );
      if (byManifestKey) return byManifestKey;
    }
    return entryTypes[0];
  }, [entryTypes, view.default_entry_type_key]);

  const fields = useMemo<ContentProfileFieldSpec[]>(
    () => activeEntryType?.form_schema?.fields ?? [],
    [activeEntryType]
  );
  const fieldByKey = useMemo(() => {
    const out = new Map<string, ContentProfileFieldSpec>();
    for (const f of fields) out.set(f.key, f);
    return out;
  }, [fields]);

  const config = (view.config || {}) as Record<string, unknown>;
  const groupBy = typeof config.group_by === 'string' ? config.group_by : 'category';
  const groupOrder = Array.isArray(config.group_order) ? (config.group_order as string[]) : [];
  const groupLabels = (config.group_labels || {}) as Record<string, string>;
  const identityColumns = (config.identity_columns || []) as IdentityColumnConfig[];
  const inputColumns = (config.input_columns || []) as ColumnEntry[];
  const computedColumns = (config.computed_columns || []) as ColumnEntry[];
  const subtotalColumns = (config.subtotal_columns || []) as string[];

  // Resolve identity columns through each row's `employee` relation —
  // batch-fetch the distinct set of linked employees once, index by id.
  // Same "self-sufficient, fetch its own data" posture EditableTableWidget
  // already takes for its hostEntry fetch.
  const employeeIds = useMemo(() => {
    const ids = new Set<string>();
    for (const row of rows) {
      const raw = (row.custom_fields || {})[identityColumns[0]?.relation_field || 'employee'];
      if (typeof raw === 'string' && raw) ids.add(raw);
    }
    return Array.from(ids);
  }, [rows, identityColumns]);

  useEffect(() => {
    if (!identityColumns.length || !employeeIds.length) {
      setEmployeesById({});
      return;
    }
    let cancelled = false;
    Promise.all(
      employeeIds.map(id =>
        entriesApi
          .get(id)
          .then(e => [id, e] as const)
          .catch(() => null)
      )
    ).then(results => {
      if (cancelled) return;
      const map: Record<string, Entry> = {};
      for (const pair of results) {
        if (pair) map[pair[0]] = pair[1];
      }
      setEmployeesById(map);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeIds.join('\x1e')]);

  const commitCell = useCallback(
    async (entry: Entry, key: string, value: unknown) => {
      const cellId = `${entry.id}:${key}`;
      setSavingCell(cellId);
      setRows(prev =>
        prev.map(r =>
          r.id === entry.id
            ? { ...r, custom_fields: { ...(r.custom_fields || {}), [key]: value } }
            : r
        )
      );
      try {
        const updated = await entriesApi.update(entry.id, { custom_fields: { [key]: value } });
        setRows(prev => prev.map(r => (r.id === entry.id ? updated : r)));
      } catch {
        showToast('Failed to save cell', 'error');
        setRows(entries);
      } finally {
        setSavingCell(null);
      }
    },
    [entries, showToast]
  );

  const groups = useMemo(() => {
    const byKey = new Map<string, Entry[]>();
    for (const row of rows) {
      const groupValue = String((row.custom_fields || {})[groupBy] ?? '');
      const bucket = byKey.get(groupValue) || [];
      bucket.push(row);
      byKey.set(groupValue, bucket);
    }
    const orderedKeys = [
      ...groupOrder.filter(k => byKey.has(k)),
      ...Array.from(byKey.keys()).filter(k => !groupOrder.includes(k)),
    ];
    return orderedKeys.map(key => ({ key, label: groupLabels[key] || key || 'Ungrouped', rows: byKey.get(key) || [] }));
  }, [rows, groupBy, groupOrder, groupLabels]);

  const sumColumn = useCallback(
    (rowsForSum: Entry[], key: string): number =>
      sumMoney(rowsForSum.map(row => (row.custom_fields || {})[key])),
    []
  );

  if (isLoading || loadingTypes) {
    return (
      <Surface tone="panel-2" radius="input" padding="none" className="h-24 animate-pulse">
        {null}
      </Surface>
    );
  }

  const allColumns: { entry: ColumnEntry; editable: boolean; identity?: IdentityColumnConfig }[] = [
    ...identityColumns.map(c => ({ entry: c as ColumnEntry, editable: false, identity: c })),
    ...inputColumns.map(c => ({ entry: c, editable: true })),
    ...computedColumns.map(c => ({ entry: c, editable: false })),
  ];

  return (
    <Surface tone="panel" radius="card" padding="none" className="overflow-hidden">
      <div className="overflow-x-auto" data-testid="payroll-register-widget">
        <table className="text-sm" style={{ width: 'max-content', minWidth: '100%' }}>
          <thead>
            <tr className="border-b border-[var(--panel-border)]">
              {allColumns.map(col => (
                <th key={columnKey(col.entry)} className="text-left px-3 py-2 whitespace-nowrap">
                  <Text as="span" variant="meta" weight="semibold" tone="muted" className="uppercase tracking-wide">
                    {col.identity ? col.identity.label : columnLabel(col.entry, fieldByKey)}
                  </Text>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map(group => (
              <>
                <tr key={`${group.key}-header`} className="border-b border-[var(--panel-border)]">
                  <td colSpan={allColumns.length} className="px-3 py-1.5">
                    <Text as="span" variant="meta" weight="bold" className="uppercase tracking-wide">
                      {group.label}
                    </Text>
                  </td>
                </tr>
                {group.rows.map(row => {
                  const relationField = identityColumns[0]?.relation_field || 'employee';
                  const employeeId = (row.custom_fields || {})[relationField];
                  const employee = typeof employeeId === 'string' ? employeesById[employeeId] : undefined;
                  return (
                    <tr key={row.id} className="border-b border-[var(--panel-border)]">
                      {allColumns.map(col => {
                        if (col.identity) {
                          const value = (employee?.custom_fields || {})[col.identity.source_field];
                          return (
                            <td key={col.identity.key} className="px-3 py-2 whitespace-nowrap">
                              <Text as="span" variant="body-sm" tone="muted">
                                {formatValue(value)}
                              </Text>
                            </td>
                          );
                        }
                        const key = columnKey(col.entry);
                        const field = fieldByKey.get(key);
                        const value = (row.custom_fields || {})[key];
                        if (!col.editable || !field) {
                          const { text } = formatComputedValue(key, value);
                          const isExplained = text !== formatValue(value);
                          return (
                            <td key={key} className="px-3 py-2 whitespace-nowrap">
                              <Text
                                as="span"
                                variant="body-sm"
                                tone={isExplained ? 'muted' : undefined}
                                className={isExplained ? 'italic' : undefined}
                                title={isExplained ? field?.help : undefined}
                              >
                                {text}
                              </Text>
                            </td>
                          );
                        }
                        const cellId = `${row.id}:${key}`;
                        return (
                          <td
                            key={key}
                            className={`px-1 py-1 align-top ${savingCell === cellId ? 'opacity-60' : ''}`}
                          >
                            <SeamlessField
                              field={field}
                              value={value}
                              onChange={next => commitCell(row, key, next)}
                              entryId={row.id}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
                <tr key={`${group.key}-subtotal`} className="border-b-2 border-[var(--panel-border)]">
                  {allColumns.map((col, i) => {
                    const key = col.identity ? col.identity.key : columnKey(col.entry);
                    if (i === 0) {
                      return (
                        <td key={key} className="px-3 py-2">
                          <Text as="span" variant="body-sm" weight="semibold">
                            Subtotal
                          </Text>
                        </td>
                      );
                    }
                    if (!col.identity && subtotalColumns.includes(columnKey(col.entry))) {
                      return (
                        <td key={key} className="px-3 py-2">
                          <Text as="span" variant="body-sm" weight="semibold">
                            {formatValue(sumColumn(group.rows, columnKey(col.entry)))}
                          </Text>
                        </td>
                      );
                    }
                    return <td key={key} className="px-3 py-2" />;
                  })}
                </tr>
              </>
            ))}
            <tr className="border-t-2 border-[var(--panel-border)]">
              {allColumns.map((col, i) => {
                const key = col.identity ? col.identity.key : columnKey(col.entry);
                if (i === 0) {
                  return (
                    <td key={key} className="px-3 py-2">
                      <Text as="span" variant="body-sm" weight="bold">
                        Grand Total
                      </Text>
                    </td>
                  );
                }
                if (!col.identity && subtotalColumns.includes(columnKey(col.entry))) {
                  return (
                    <td key={key} className="px-3 py-2">
                      <Text as="span" variant="body-sm" weight="bold">
                        {formatValue(sumColumn(rows, columnKey(col.entry)))}
                      </Text>
                    </td>
                  );
                }
                return <td key={key} className="px-3 py-2" />;
              })}
            </tr>
          </tbody>
        </table>
      </div>
      {rows.length === 0 && (
        <div className="p-6 text-center">
          <Text as="span" variant="body-sm" tone="muted">
            No Payroll Register lines yet — active employees are added automatically when this Pay Run is created.
          </Text>
        </div>
      )}
    </Surface>
  );
}
