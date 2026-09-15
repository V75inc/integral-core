import { useCallback, useEffect, useMemo, useState } from 'react';
import { SeamlessField } from '../entries/SeamlessField';
import type { RelationChoice } from '../entries/EntryFormExpanded';
import { slug } from '../entries/entryFormCustomFields';
import { entriesApi, entryTypesApi, tracksApi } from '../../api';
import { toolsApi } from '../../api/tools';
import { useToast } from '../../context/ToastContext';
import { fieldEntryKey, fieldEntryVisibleIf, isVisible, useLiveValues, type ConditionalFieldEntry } from './regionConditions';
import type { ViewWidgetProps } from './types';
import type { ContentProfileFieldSpec, Entry, EntryTypeNode } from '../../types';

/**
 * Config-driven subset-of-fields form region — the reusable Oracle-APEX-
 * style "Form" region. Always-editable inline (no separate view/edit mode),
 * built on ``SeamlessField`` (the same per-field-type renderer the entry
 * composer uses), so every field type renders with its normal editor with
 * zero new per-type code.
 *
 * Widget config (``view.config``): ``{fields: ConditionalFieldEntry[], title?,
 * columns?}``. Each field entry is either a bare key (always shown) or
 * ``{key, visible_if: {field, equals}}`` to gate that ONE field on another
 * field's current value on the same target entry — e.g. Period 2-5 date
 * fields only shown when ``schedule_type`` is "Weekly", Period 1 always
 * shown. See ``regionConditions.ts`` (shared with LayoutContainerWidget's
 * region-level gate and EditableTableWidget's per-column gate).
 *
 * Bind mode (``view.config.__bindings``, forwarded from
 * ``related_views[].bind`` by ``RelatedViewsSection``/``ComposableViewSlot``)
 * selects WHICH entry the fields are read from and persisted to:
 *
 *   - ``{entry: 'self'}`` (default) — the host entry itself
 *     (``bindings.entryId``). Fetched directly via ``entriesApi.get`` (the
 *     widget does not trust a possibly-stale/filtered ``entries`` prop),
 *     persisted via ``entriesApi.update`` on change — same self-persisting
 *     shape as ``EditableTableWidget.commitCell``.
 *   - ``{entry: 'tool', tool, input_field}`` — a related entry resolved by
 *     calling the named workspace tool (``POST /api/tools/{tool}``) with
 *     ``{[input_field]: bindings.entryId}``. Expects the tool's JSON output
 *     to include ``entry_id`` (editable) and/or ``custom_fields`` (fallback
 *     read-only display when no ``entry_id`` is returned).
 *   - ``{entry: 'anchored'}`` — the first entry already fetched by
 *     ``ComposableViewSlot`` for this view (``entries[0]``), used when
 *     ``form_region`` itself is placed via a ``:anchored_track/...``
 *     related_views reference.
 */
export function FormRegionWidget({ view, entries, isLoading }: ViewWidgetProps) {
  const { showToast } = useToast();
  const config = (view.config || {}) as Record<string, unknown>;
  const bindings = (config.__bindings || {}) as Record<string, unknown>;
  const bindMode = typeof bindings.entry === 'string' ? bindings.entry : 'self';
  const bindTool = typeof bindings.tool === 'string' ? bindings.tool : undefined;
  const bindInputField =
    typeof bindings.input_field === 'string' ? bindings.input_field : 'entry_id';
  const hostEntryId = typeof bindings.entryId === 'string' ? bindings.entryId : undefined;

  const fieldEntries = useMemo<ConditionalFieldEntry[]>(
    () => (Array.isArray(config.fields) ? (config.fields as ConditionalFieldEntry[]) : []),
    [config.fields]
  );
  const fieldKeys = useMemo<string[]>(() => fieldEntries.map(fieldEntryKey), [fieldEntries]);
  const title = typeof config.title === 'string' ? config.title : undefined;
  const columns = typeof config.columns === 'number' ? config.columns : 1;

  const [entryTypes, setEntryTypes] = useState<EntryTypeNode[]>([]);
  const [loadingTypes, setLoadingTypes] = useState(true);

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

  const allFields = useMemo<ContentProfileFieldSpec[]>(
    () => activeEntryType?.form_schema?.fields ?? [],
    [activeEntryType]
  );
  const fieldByKey = useMemo(() => {
    const out = new Map<string, ContentProfileFieldSpec>();
    for (const f of allFields) out.set(f.key, f);
    return out;
  }, [allFields]);
  const fieldVisibleIfByKey = useMemo(() => {
    const out = new Map<string, ReturnType<typeof fieldEntryVisibleIf>>();
    for (const entry of fieldEntries) out.set(fieldEntryKey(entry), fieldEntryVisibleIf(entry));
    return out;
  }, [fieldEntries]);
  const displayFields = useMemo<ContentProfileFieldSpec[]>(() => {
    if (!fieldKeys.length) return allFields;
    return fieldKeys
      .map(k => fieldByKey.get(k))
      .filter((f): f is ContentProfileFieldSpec => Boolean(f));
  }, [fieldKeys, allFields, fieldByKey]);

  // Relation-typed fields need choices — same trimmed port of
  // EditableTableWidget's relation-choices effect (itself ported from
  // EntryFormExpanded.tsx).
  const [relationChoices, setRelationChoices] = useState<
    Record<string, RelationChoice[]>
  >({});
  const [relationLoading, setRelationLoading] = useState(false);
  const relationFieldsFetchSig = displayFields
    .filter(f => f.type === 'relation')
    .map(f => `${f.key}:${JSON.stringify(f.relation ?? {})}`)
    .join('\x1e');

  useEffect(() => {
    const relationFields = displayFields.filter(f => f.type === 'relation');
    if (!relationFields.length) {
      setRelationChoices(prev => (Object.keys(prev).length === 0 ? prev : {}));
      return;
    }
    let cancelled = false;
    setRelationLoading(true);
    (async () => {
      try {
        const allTracks = await tracksApi.list();
        const choicesByField: Record<string, RelationChoice[]> = {};
        await Promise.all(
          relationFields.map(async relField => {
            const relation = relField.relation || {};
            const allowCrossTrack = Boolean(relation.allow_cross_track);
            const targetEntryTypes = new Set(
              (relation.target_entry_types || []).map(x => slug(String(x)))
            );
            const targetTrackTypes = new Set(
              (relation.target_track_types || []).map(x => slug(String(x)))
            );
            const candidateTracks = allowCrossTrack
              ? allTracks.filter(t => {
                  if (!targetTrackTypes.size) return true;
                  const typeKey = slug(String(t.template_id || t.title));
                  return targetTrackTypes.has(typeKey);
                })
              : allTracks.filter(t => t.id === view.track_id);
            const entryLists = await Promise.all(
              candidateTracks.map(async t => {
                try {
                  const es = await entriesApi.list({ track_id: t.id, limit: 200 });
                  return { track: t, entries: es };
                } catch {
                  return { track: t, entries: [] };
                }
              })
            );
            const deduped = new Map<string, RelationChoice>();
            for (const group of entryLists) {
              for (const item of group.entries) {
                if (
                  targetEntryTypes.size &&
                  !targetEntryTypes.has(slug(String(item.type)))
                ) {
                  continue;
                }
                const primary =
                  String(item.title || '').trim() ||
                  String(item.body || '').trim().slice(0, 80) ||
                  `Entry ${item.id.slice(-6)}`;
                deduped.set(item.id, {
                  value: item.id,
                  label: `${primary} (${group.track.title})`,
                });
              }
            }
            choicesByField[relField.key] = Array.from(deduped.values());
          })
        );
        if (!cancelled) setRelationChoices(choicesByField);
      } finally {
        if (!cancelled) setRelationLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relationFieldsFetchSig, view.track_id]);

  // Target entry resolution — the record the fields actually read from /
  // write to. Independent of `entries` prop trust: fetched directly for
  // 'self' and 'tool' modes; only 'anchored' mode uses the entries prop,
  // matching what ComposableViewSlot already resolved for this view.
  const [targetEntry, setTargetEntry] = useState<Entry | null>(null);
  const [targetEditable, setTargetEditable] = useState(true);
  const [loadingTarget, setLoadingTarget] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoadingTarget(true);

    async function resolve() {
      if (bindMode === 'anchored') {
        setTargetEntry(entries[0] || null);
        setTargetEditable(true);
        return;
      }
      if (bindMode === 'tool') {
        if (!bindTool || !hostEntryId) {
          setTargetEntry(null);
          return;
        }
        try {
          const { output } = await toolsApi.call(bindTool, {
            [bindInputField]: hostEntryId,
          });
          const relatedEntryId =
            typeof output.entry_id === 'string' ? output.entry_id : undefined;
          if (relatedEntryId) {
            const fetched = await entriesApi.get(relatedEntryId);
            if (!cancelled) {
              setTargetEntry(fetched);
              setTargetEditable(true);
            }
            return;
          }
          const customFields = (output.custom_fields || {}) as Record<string, unknown>;
          if (!cancelled) {
            setTargetEntry({
              id: '',
              title: '',
              custom_fields: customFields,
            } as Entry);
            setTargetEditable(false);
          }
        } catch {
          if (!cancelled) setTargetEntry(null);
        }
        return;
      }
      // 'self' (default)
      if (!hostEntryId) {
        setTargetEntry(null);
        return;
      }
      try {
        const fetched = await entriesApi.get(hostEntryId);
        if (!cancelled) {
          setTargetEntry(fetched);
          setTargetEditable(true);
        }
      } catch {
        if (!cancelled) setTargetEntry(null);
      }
    }

    resolve().finally(() => {
      if (!cancelled) setLoadingTarget(false);
    });
    return () => {
      cancelled = true;
    };
  }, [bindMode, bindTool, bindInputField, hostEntryId, entries]);

  // Shared live-values channel from an enclosing LayoutContainerWidget, if
  // any (null when this widget renders standalone) — see
  // regionConditions.ts's useLiveValuesProvider docstring. A sibling
  // region's own visible_if needs to see THIS region's commits the moment
  // they happen, not after a reload; `live.commit` is how that reaches them.
  const live = useLiveValues();

  const commitField = useCallback(
    async (key: string, value: unknown) => {
      if (!targetEntry || !targetEditable || !targetEntry.id) return;
      setTargetEntry(prev =>
        prev
          ? { ...prev, custom_fields: { ...(prev.custom_fields || {}), [key]: value } }
          : prev
      );
      live?.commit(key, value);
      try {
        const updated = await entriesApi.update(targetEntry.id, {
          custom_fields: { [key]: value },
        });
        setTargetEntry(updated);
      } catch {
        showToast('Failed to save field', 'error');
      }
    },
    [targetEntry, targetEditable, showToast, live]
  );

  if (isLoading || loadingTypes || loadingTarget) {
    return (
      <div className="h-24 bg-[var(--panel-2)] rounded-[var(--radius-input)] animate-pulse" />
    );
  }

  if (!targetEntry) {
    return null;
  }

  // Merge the shared channel on top of this region's own entry snapshot —
  // a sibling region's more-recent commit wins for visible_if purposes,
  // while fields this region itself owns still read from its own state.
  const values = { ...(targetEntry.custom_fields || {}), ...(live?.values || {}) };
  const visibleFields = displayFields.filter(f => isVisible(fieldVisibleIfByKey.get(f.key), values));

  return (
    <div
      className="bg-[var(--panel)] rounded-lg border border-[var(--panel-border)] p-4"
      data-testid="form-region-widget"
    >
      {title && (
        <h3 className="text-sm font-semibold text-[var(--text)] mb-3">{title}</h3>
      )}
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: `repeat(${Math.max(1, columns)}, minmax(0, 1fr))` }}
      >
        {visibleFields.map(field => (
          <SeamlessField
            key={field.key}
            field={targetEditable ? field : { ...field, readonly: true }}
            value={values[field.key]}
            onChange={next => commitField(field.key, next)}
            entryId={targetEntry.id || undefined}
            relationChoices={relationChoices[field.key]}
            relationLoading={relationLoading}
          />
        ))}
      </div>
    </div>
  );
}
