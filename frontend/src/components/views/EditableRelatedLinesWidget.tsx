/**
 * ``editable_related_lines`` — APEX-style editable grid of child entries that
 * reference the host entry via a reverse-relation field (independent tracks,
 * not anchored). Supports compose-time draft staging + post-create persist.
 *
 * Config (all keys caller-supplied — no domain tokens in Core):
 *   relation, child_entry_type, child_track_type, columns[]
 *   quantity_field?, rate_field?, amount_field?, parent_total_field?,
 *   parent_balance_field?, currency_field?
 *   catalog_relation_field?, catalog_autofill?, list_catalog_tool?
 *   persist_mode: 'tool' | 'entries_api', persist_tool?, require_at_least_one?,
 *   defaults?, title?
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { entriesApi, tracksApi } from '../../api';
import { extensionsApi } from '../../api/extensions';
import { toolsApi } from '../../api/tools';
import { useContributionLifecycle } from '../entries/contributionLifecycle';
import { slug } from '../entries/entryFormCustomFields';
import { Button } from '../ui/Button';
import { Text } from '../../ui';
import type { ViewWidgetProps } from './types';

type DraftLine = {
  local_id: string;
  entry_id?: string;
  fields: Record<string, unknown>;
};

type CatalogItem = {
  id: string;
  label: string;
  unit_price?: number;
  description?: string;
  item_name?: string;
};

type AutofillRule = { from: string; to: string };

function asStringArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(x => String(x)).filter(Boolean);
}

function money(n: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
    }).format(n);
  } catch {
    return n.toFixed(2);
  }
}

function lineAmount(
  line: DraftLine,
  quantityField?: string,
  rateField?: string,
  amountField?: string
): number {
  if (amountField) {
    const explicit = Number(line.fields[amountField]);
    if (Number.isFinite(explicit) && quantityField && rateField) {
      const qty = Number(line.fields[quantityField]) || 0;
      const rate = Number(line.fields[rateField]) || 0;
      return qty * rate;
    }
    if (Number.isFinite(explicit)) return explicit;
  }
  if (quantityField && rateField) {
    return (Number(line.fields[quantityField]) || 0) * (Number(line.fields[rateField]) || 0);
  }
  return 0;
}

function newDraftLine(defaults: Record<string, unknown>, n: number): DraftLine {
  return {
    local_id: `L${Date.now()}-${n}`,
    fields: { ...defaults },
  };
}

export function EditableRelatedLinesWidget({ view }: ViewWidgetProps) {
  const lifecycle = useContributionLifecycle();
  const config = (view.config || {}) as Record<string, unknown>;
  const bindings = (config.__bindings || {}) as Record<string, unknown>;

  const relation = String(config.relation || '').trim();
  const childEntryType = String(config.child_entry_type || '').trim();
  const childTrackType = String(config.child_track_type || '').trim();
  const columns = asStringArray(config.columns);
  const title = typeof config.title === 'string' ? config.title : 'Line items';
  const quantityField =
    typeof config.quantity_field === 'string' ? config.quantity_field : undefined;
  const rateField = typeof config.rate_field === 'string' ? config.rate_field : undefined;
  const amountField =
    typeof config.amount_field === 'string' ? config.amount_field : undefined;
  const parentTotalField =
    typeof config.parent_total_field === 'string' ? config.parent_total_field : undefined;
  const parentBalanceField =
    typeof config.parent_balance_field === 'string'
      ? config.parent_balance_field
      : undefined;
  const currencyField =
    typeof config.currency_field === 'string' ? config.currency_field : undefined;
  const catalogRelationField =
    typeof config.catalog_relation_field === 'string'
      ? config.catalog_relation_field
      : undefined;
  const listCatalogTool =
    typeof config.list_catalog_tool === 'string' ? config.list_catalog_tool : undefined;
  const persistMode = String(config.persist_mode || 'tool').toLowerCase();
  const persistTool =
    typeof config.persist_tool === 'string' ? config.persist_tool : undefined;
  const requireAtLeastOne = Boolean(config.require_at_least_one);
  const defaults =
    config.defaults && typeof config.defaults === 'object' && !Array.isArray(config.defaults)
      ? (config.defaults as Record<string, unknown>)
      : { ...(quantityField ? { [quantityField]: 1 } : {}) };
  const autofill = Array.isArray(config.catalog_autofill)
    ? (config.catalog_autofill as AutofillRule[]).filter(
        r => r && typeof r.from === 'string' && typeof r.to === 'string'
      )
    : [];

  const mode =
    (typeof bindings.__contributionMode === 'string'
      ? bindings.__contributionMode
      : lifecycle?.mode) || 'detail';
  const hostEntryId =
    (typeof bindings.entryId === 'string' && bindings.entryId) ||
    lifecycle?.entryId ||
    undefined;
  const appId =
    (typeof bindings.appId === 'string' && bindings.appId) ||
    lifecycle?.appId ||
    undefined;
  const entryValues = (
    (bindings.entryValues as Record<string, unknown> | undefined) ||
    lifecycle?.customFields ||
    {}
  ) as Record<string, unknown>;
  const currency =
    (currencyField && String(entryValues[currencyField] || '').trim()) || 'USD';
  const readOnly = mode === 'detail';

  const [lines, setLines] = useState<DraftLine[]>(() => [
    newDraftLine(defaults, 1),
  ]);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const linesRef = useRef(lines);
  linesRef.current = lines;
  const seqRef = useRef(1);

  const patchParentTotals = useCallback(
    (next: DraftLine[]) => {
      if (!lifecycle?.onDraftPatch || !parentTotalField) return;
      const total = next.reduce(
        (s, line) => s + lineAmount(line, quantityField, rateField, amountField),
        0
      );
      const custom_fields: Record<string, unknown> = { [parentTotalField]: total };
      if (parentBalanceField) custom_fields[parentBalanceField] = total;
      lifecycle.onDraftPatch({
        custom_fields,
        related: {
          relation,
          lines: next.map((line, idx) => ({
            ...line.fields,
            ...(amountField
              ? {
                  [amountField]: lineAmount(
                    line,
                    quantityField,
                    rateField,
                    amountField
                  ),
                }
              : {}),
            line_number: idx + 1,
            entry_id: line.entry_id,
          })),
        },
      });
    },
    [
      lifecycle,
      parentTotalField,
      parentBalanceField,
      quantityField,
      rateField,
      amountField,
      relation,
    ]
  );

  const setLinesAndPatch = useCallback(
    (updater: (prev: DraftLine[]) => DraftLine[]) => {
      setLines(prev => {
        const next = updater(prev);
        patchParentTotals(next);
        return next;
      });
    },
    [patchParentTotals]
  );

  // Load existing related rows when host entry exists.
  useEffect(() => {
    if (!hostEntryId || !relation) return;
    let cancelled = false;
    setLoading(true);
    entriesApi
      .listRelated(hostEntryId, relation, {
        limit: 200,
        entryType: childEntryType || undefined,
      })
      .then(res => {
        if (cancelled) return;
        const loaded = (res.entries || []).map((e, idx) => ({
          local_id: e.id || `E${idx}`,
          entry_id: e.id,
          fields: { ...(e.custom_fields || {}) },
        }));
        setLines(loaded.length ? loaded : [newDraftLine(defaults, 1)]);
        if (loaded.length) patchParentTotals(loaded);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load related lines');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hostEntryId, relation, childEntryType]);

  // Catalog for product/service picker.
  useEffect(() => {
    if (!listCatalogTool && !catalogRelationField) return;
    let cancelled = false;
    (async () => {
      try {
        if (listCatalogTool) {
          let items: Record<string, unknown>[] = [];
          if (appId) {
            const res = await extensionsApi.invokeOperation(appId, listCatalogTool, {});
            items = Array.isArray(res.output?.items)
              ? (res.output.items as Record<string, unknown>[])
              : [];
          } else {
            const res = await toolsApi.call(listCatalogTool, {});
            items = Array.isArray(res.output?.items)
              ? (res.output.items as Record<string, unknown>[])
              : [];
          }
          if (cancelled) return;
          setCatalog(
            items
              .map(raw => ({
                id: String(raw.id || ''),
                label:
                  String(raw.item_name || raw.title || raw.label || '').trim() ||
                  String(raw.id || ''),
                unit_price:
                  raw.unit_price != null ? Number(raw.unit_price) : undefined,
                description: String(raw.description || ''),
                item_name: String(raw.item_name || ''),
              }))
              .filter(i => i.id)
          );
          return;
        }
        if (!cancelled) setCatalog([]);
      } catch {
        if (!cancelled) setCatalog([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [listCatalogTool, catalogRelationField, appId]);

  useEffect(() => {
    if (!lifecycle) return;
    return lifecycle.register({
      requestValidate: async () => {
        const current = linesRef.current;
        if (requireAtLeastOne && current.length === 0) {
          return { ok: false, error: 'Add at least one line' };
        }
        for (let i = 0; i < current.length; i++) {
          const line = current[i];
          if (catalogRelationField) {
            const catId = String(line.fields[catalogRelationField] || '').trim();
            const salesId = String(line.fields.sales_item_id || '').trim();
            if (!catId && !salesId) {
              return {
                ok: false,
                error: `Line ${i + 1}: select a product / service`,
              };
            }
          }
          if (rateField != null) {
            const rate = Number(line.fields[rateField]);
            if (!(rate > 0)) {
              return { ok: false, error: `Line ${i + 1}: rate must be greater than 0` };
            }
          }
          if (quantityField != null) {
            const qty = Number(line.fields[quantityField]);
            if (!(qty > 0)) {
              return {
                ok: false,
                error: `Line ${i + 1}: quantity must be greater than 0`,
              };
            }
          }
        }
        return { ok: true };
      },
      requestSubmit: async entryIdArg => {
        const parentId = String(entryIdArg || hostEntryId || '').trim();
        if (!parentId) {
          return { ok: false, error: 'Missing parent entry id' };
        }
        const current = linesRef.current;
        const payloadLines = current.map((line, idx) => {
          const amount = lineAmount(line, quantityField, rateField, amountField);
          const fields = { ...line.fields };
          if (amountField) fields[amountField] = amount;
          // Finance persist tool expects sales_item_id naming.
          if (catalogRelationField && fields[catalogRelationField] && !fields.sales_item_id) {
            fields.sales_item_id = fields[catalogRelationField];
          }
          return {
            ...fields,
            quantity: quantityField ? Number(fields[quantityField]) || 0 : undefined,
            unit_price: rateField ? Number(fields[rateField]) || 0 : undefined,
            line_amount: amount,
            line_number: idx + 1,
            description: String(fields.description || ''),
          };
        });

        try {
          // Replace-by-parent: delete existing related children before recreate
          // so re-save does not append duplicates.
          if (relation) {
            const existing = await entriesApi.listRelated(parentId, relation, {
              limit: 500,
              entryType: childEntryType || undefined,
            });
            for (const e of existing.entries || []) {
              try {
                await entriesApi.delete(e.id);
              } catch {
                /* best-effort; persist may still recreate */
              }
            }
          }

          if (persistMode === 'tool') {
            if (!persistTool) {
              return { ok: false, error: 'persist_tool is required for tool mode' };
            }
            const input = {
              parent_entry_id: parentId,
              entry_id: parentId,
              lines: payloadLines,
            };
            const output = appId
              ? (await extensionsApi.invokeOperation(appId, persistTool, input)).output
              : (await toolsApi.call(persistTool, input)).output;
            if (output?.error) {
              return {
                ok: false,
                error: String(output.message || output.error_code || 'Persist failed'),
              };
            }
            return { ok: true, value: output };
          }

          // entries_api mode: create children with parent FK on relation field.
          let childTrackId: string | undefined;
          if (childTrackType) {
            const tracks = await tracksApi.list();
            const match = tracks.find(
              t => slug(String(t.template_id || t.title)) === slug(childTrackType)
            );
            childTrackId = match?.id;
          }
          if (!childTrackId) {
            return { ok: false, error: `Child track ${childTrackType} not found` };
          }
          const created: string[] = [];
          for (const pl of payloadLines) {
            const cf: Record<string, unknown> = {
              ...pl,
              [relation]: parentId,
            };
            delete cf.sales_item_id;
            if (pl.sales_item_id) cf[catalogRelationField || 'sales_item'] = pl.sales_item_id;
            const entry = await entriesApi.create({
              track_id: childTrackId,
              type: childEntryType || undefined,
              title: String(pl.description || 'Line').slice(0, 120),
              custom_fields: cf,
            });
            created.push(entry.id);
          }
          if (parentTotalField) {
            const total = payloadLines.reduce(
              (s, pl) => s + (Number(pl.line_amount) || 0),
              0
            );
            const patch: Record<string, unknown> = { [parentTotalField]: total };
            if (parentBalanceField) patch[parentBalanceField] = total;
            await entriesApi.update(parentId, { custom_fields: patch });
          }
          return { ok: true, value: { line_ids: created } };
        } catch (e) {
          return {
            ok: false,
            error: e instanceof Error ? e.message : 'Failed to persist lines',
          };
        }
      },
    });
  }, [
    lifecycle,
    requireAtLeastOne,
    catalogRelationField,
    rateField,
    quantityField,
    amountField,
    hostEntryId,
    appId,
    relation,
    childEntryType,
    childTrackType,
    persistMode,
    persistTool,
    parentTotalField,
    parentBalanceField,
  ]);

  const updateLineField = (localId: string, key: string, value: unknown) => {
    setLinesAndPatch(prev =>
      prev.map(line => {
        if (line.local_id !== localId) return line;
        const fields = { ...line.fields, [key]: value };
        if (catalogRelationField && key === catalogRelationField) {
          const item = catalog.find(c => c.id === value);
          fields.sales_item_id = value;
          fields.sales_item_name = item?.label || '';
          for (const rule of autofill) {
            const fromVal =
              rule.from === 'unit_price'
                ? item?.unit_price
                : rule.from === 'description'
                  ? item?.description
                  : rule.from === 'item_name'
                    ? item?.item_name || item?.label
                    : (item as Record<string, unknown> | undefined)?.[rule.from];
            const cur = fields[rule.to];
            if (
              fromVal != null &&
              fromVal !== '' &&
              (cur == null || cur === '' || cur === 0)
            ) {
              fields[rule.to] = fromVal;
            }
          }
        }
        return { ...line, fields };
      })
    );
  };

  const addLine = () => {
    seqRef.current += 1;
    setLinesAndPatch(prev => [...prev, newDraftLine(defaults, seqRef.current)]);
  };

  const removeLine = (localId: string) => {
    setLinesAndPatch(prev => {
      const next = prev.filter(l => l.local_id !== localId);
      return next.length ? next : [newDraftLine(defaults, ++seqRef.current)];
    });
  };

  const subtotal = useMemo(
    () =>
      lines.reduce(
        (s, line) => s + lineAmount(line, quantityField, rateField, amountField),
        0
      ),
    [lines, quantityField, rateField, amountField]
  );

  const displayColumns = columns.length
    ? columns
    : [
        ...(catalogRelationField ? [catalogRelationField] : []),
        'description',
        ...(quantityField ? [quantityField] : []),
        ...(rateField ? [rateField] : []),
        ...(amountField ? [amountField] : []),
      ].filter(Boolean);

  const columnLabel = (key: string) => {
    if (key === catalogRelationField) return 'Product / service';
    if (key === quantityField) return 'Qty';
    if (key === rateField) return 'Rate';
    if (key === amountField) return 'Amount';
    if (key === 'description') return 'Description';
    return key.replace(/_/g, ' ');
  };

  if (!relation) {
    return (
      <Text variant="body" tone="muted" as="p">
        editable_related_lines requires config.relation
      </Text>
    );
  }

  if (loading) {
    return (
      <Text variant="body" tone="muted" as="p">
        Loading lines…
      </Text>
    );
  }

  return (
    <div className="space-y-3" data-testid="editable-related-lines">
      <Text as="h3" variant="meta" weight="semibold" tone="muted" className="uppercase tracking-wide">
        {title}
      </Text>
      {error ? (
        <Text variant="body" tone="muted" as="p" className="text-[var(--danger)]">
          {error}
        </Text>
      ) : null}
      <div className="overflow-x-auto rounded-[var(--radius-card)] border border-[var(--panel-border)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--panel-border)] text-left text-[var(--text-muted)]">
              {displayColumns.map(col => (
                <th key={col} className="px-2 py-2 font-medium">
                  {columnLabel(col)}
                </th>
              ))}
              {!readOnly ? <th className="w-10 px-2 py-2" /> : null}
            </tr>
          </thead>
          <tbody>
            {lines.map(line => (
              <tr key={line.local_id} className="border-b border-[var(--panel-border)] last:border-0">
                {displayColumns.map(col => {
                  const isAmount = col === amountField;
                  const isCatalog = col === catalogRelationField;
                  const value = line.fields[col];
                  if (isAmount) {
                    return (
                      <td key={col} className="px-2 py-1.5 text-[var(--text-muted)]">
                        {money(
                          lineAmount(line, quantityField, rateField, amountField),
                          currency
                        )}
                      </td>
                    );
                  }
                  if (readOnly) {
                    if (isCatalog) {
                      const item = catalog.find(c => c.id === value);
                      return (
                        <td key={col} className="px-2 py-1.5">
                          {item?.label || String(value || '—')}
                        </td>
                      );
                    }
                    return (
                      <td key={col} className="px-2 py-1.5">
                        {String(value ?? '—')}
                      </td>
                    );
                  }
                  if (isCatalog) {
                    return (
                      <td key={col} className="px-2 py-1">
                        <select
                          className="w-full rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel-2)] px-2 py-1.5"
                          value={String(value || '')}
                          onChange={e =>
                            updateLineField(line.local_id, col, e.target.value)
                          }
                        >
                          <option value="">Select…</option>
                          {catalog.map(item => (
                            <option key={item.id} value={item.id}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                      </td>
                    );
                  }
                  const inputType =
                    col === quantityField || col === rateField ? 'number' : 'text';
                  return (
                    <td key={col} className="px-2 py-1">
                      <input
                        type={inputType}
                        step={inputType === 'number' ? 'any' : undefined}
                        className="w-full rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel-2)] px-2 py-1.5"
                        value={value == null ? '' : String(value)}
                        onChange={e => {
                          const raw = e.target.value;
                          const next =
                            inputType === 'number'
                              ? raw === ''
                                ? ''
                                : Number(raw)
                              : raw;
                          updateLineField(line.local_id, col, next);
                        }}
                      />
                    </td>
                  );
                })}
                {!readOnly ? (
                  <td className="px-1 py-1">
                    <button
                      type="button"
                      className="p-1.5 text-[var(--text-muted)] hover:text-[var(--danger)]"
                      aria-label="Remove line"
                      onClick={() => removeLine(line.local_id)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readOnly ? (
        <div className="flex items-center justify-between gap-3">
          <Button type="button" variant="ghost" size="sm" onClick={addLine}>
            <Plus size={14} className="mr-1" />
            Add line
          </Button>
          <div className="flex gap-4 text-sm text-[var(--text-muted)]">
            <span>
              Subtotal <strong className="text-[var(--text)]">{money(subtotal, currency)}</strong>
            </span>
            <span>
              Total <strong className="text-[var(--text)]">{money(subtotal, currency)}</strong>
            </span>
          </div>
        </div>
      ) : (
        <div className="flex justify-end gap-4 text-sm text-[var(--text-muted)]">
          <span>
            Total <strong className="text-[var(--text)]">{money(subtotal, currency)}</strong>
          </span>
        </div>
      )}
    </div>
  );
}
