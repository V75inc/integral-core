import { Fragment, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { MarkdownContent } from '../ui';
import type { OperationalModelFieldSpec, Track } from '../../types';
import {
  formatCustomFieldValue,
  shouldRenderMetaField,
  sortFieldsByOrder,
} from '../../utils/entryMetaFields';
import { entriesApi, tracksApi } from '../../api';
import { useScope } from '../../context/ScopeContext';
import { InlineFieldEditor } from './InlineFieldEditor';
import { JsonTableEditor, isJsonTableShape } from './JsonTableEditor';
import { MemberValue } from './members';
import { RelationValue } from './relations';
import type { RelationNavContext } from './relations/routeForRelationTarget';
import { formatChecklistSummary } from './fieldTypes/ChecklistField';
import {
  SeamlessField,
  type SeamlessFieldRelationChoice,
} from './SeamlessField';

import {
  detailsTrackIdsForProjects,
  projectIdsFromRelationValue,
  sprintLinkedToProject,
} from './relationChoiceLoaders';

/** Field types that support hover-to-reveal inline editing in the detail panel. */
const INLINE_EDITABLE_TYPES = new Set(['text', 'number']);
const RELATION_PICKER_STALE_MS = 60_000;
const TRACKS_PICKER_STALE_MS = 5 * 60_000;

function slug(value: string): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export interface EntryMetaFieldsProps {
  fields: OperationalModelFieldSpec[];
  values: Record<string, unknown>;
  variant: 'detail' | 'card';
  className?: string;
  /** Card only: show full meta values (feed expanded state). */
  expanded?: boolean;
  /**
   * Detail only. When provided, text and number fields render with a
   * hover-to-reveal inline edit affordance. Relation fields also become
   * editable (Sprint picker, etc.). The callback receives the field key
   * and the new value. Returning a rejected promise surfaces an error
   * border on the input.
   */
  onCommitField?: (key: string, value: unknown) => Promise<void>;
  /** When true, the inline editor is visible but non-interactive (no edit affordance). */
  readOnly?: boolean;
  /** Kanban workflow field key → enum key → display label (for select / multi_select). */
  workflowEnumLabels?: Record<string, Record<string, string>>;
  /** Called before relation link navigation (e.g. dismiss host modal). */
  onNavigate?: () => void;
  /** Preserve parent entry in breadcrumbs when drilling into a related entry. */
  navContext?: RelationNavContext | null;
  /** Track that owns this entry — required to load cross-track relation choices. */
  trackId?: string;
  /**
   * When editing a Task on an anchored details track, the parent Project
   * entry id — used to filter Sprint choices to that project's sprints.
   */
  anchorProjectId?: string;
}

/**
 * Read-only profile fields integrated into the entry surface (no panel chrome).
 * Card and detail share the same grid, group headers, and label-above-value layout.
 *
 * In detail variant, text/number/relation fields gain inline edit when
 * `onCommitField` is supplied (and not readOnly).
 */
export function EntryMetaFields({
  fields,
  values,
  variant,
  className = '',
  expanded = false,
  onCommitField,
  readOnly = false,
  workflowEnumLabels,
  onNavigate,
  navContext,
  trackId,
  anchorProjectId = '',
}: EntryMetaFieldsProps) {
  const ordered = sortFieldsByOrder(fields);
  const activeTrackId = trackId || navContext?.fromTrackId || '';
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId ?? '';
  const queryClient = useQueryClient();
  // Primitive deps only — parent often passes a new `fields` array /
  // `onCommitField` every render. Basing the fetch effect on those
  // identities re-listed tracks+entries in a tight loop and surfaced as
  // "Too many requests".
  const relationsEnabled = Boolean(
    variant === 'detail' && onCommitField && !readOnly
  );
  const relationFieldsSig = relationsEnabled
    ? fields
        .filter(f => String(f.type || '').toLowerCase() === 'relation')
        .map(f => `${f.key}:${JSON.stringify(f.relation ?? {})}`)
        .join('\x1e')
    : '';
  const projectIdsSig = projectIdsFromRelationValue(values.project).join(',');
  const fieldsRef = useRef(fields);
  fieldsRef.current = fields;
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const [relationChoices, setRelationChoices] = useState<
    Record<string, SeamlessFieldRelationChoice[]>
  >({});
  const [relationLoading, setRelationLoading] = useState(false);

  const tracksQueryEnabled = Boolean(
    relationFieldsSig && activeTrackId && workspaceId
  );
  const {
    data: cachedTracks = [],
    isFetching: tracksFetching,
    isError: tracksError,
  } = useQuery({
    queryKey: ['tracks', 'list', 'relation-picker', workspaceId] as const,
    // Relation pickers only need id/title/template_id — skip the per-track
    // entry_count probe fan-out that tracksApi.list() does by default.
    queryFn: () =>
      tracksApi.list({ limit: 100, skipEntryCounts: true }),
    enabled: tracksQueryEnabled,
    staleTime: TRACKS_PICKER_STALE_MS,
  });

  useEffect(() => {
    if (!relationFieldsSig || !activeTrackId) {
      setRelationChoices(prev => (Object.keys(prev).length === 0 ? prev : {}));
      setRelationLoading(false);
      return;
    }
    if (tracksFetching && !cachedTracks.length) {
      setRelationLoading(true);
      return;
    }
    if (tracksError) {
      setRelationChoices({});
      setRelationLoading(false);
      return;
    }
    const relationFields = fieldsRef.current.filter(
      f => String(f.type || '').toLowerCase() === 'relation'
    );
    let cancelled = false;
    setRelationLoading(true);
    (async () => {
      try {
        const allTracks = cachedTracks;
        const choicesByField: Record<string, SeamlessFieldRelationChoice[]> = {};
        await Promise.all(
          relationFields.map(async field => {
            const relation = field.relation || {};
            const allowCrossTrack = Boolean(relation.allow_cross_track);
            const targetEntryTypes = new Set(
              (relation.target_entry_types || []).map(x => slug(String(x)))
            );
            const targetTrackTypes = new Set(
              (relation.target_track_types || []).map(x => slug(String(x)))
            );
            const currentTrack = allTracks.find(
              (t: Track) => t.id === activeTrackId
            );

            if (field.key === 'tasks') {
              const projectIds = projectIdsFromRelationValue(
                valuesRef.current.project
              );
              if (!projectIds.length) {
                choicesByField[field.key] = [];
                return;
              }
              const detailIds = await detailsTrackIdsForProjects(projectIds);
              if (!detailIds.length) {
                choicesByField[field.key] = [];
                return;
              }
              const entryLists = await Promise.all(
                detailIds.map(async dId => {
                  try {
                    const entries = await queryClient.fetchQuery({
                      queryKey: [
                        'entries',
                        'track',
                        dId,
                        'relation-picker',
                        200,
                      ] as const,
                      queryFn: () =>
                        entriesApi.list({
                          track_id: dId,
                          limit: 200,
                        }),
                      staleTime: RELATION_PICKER_STALE_MS,
                    });
                    const trackObj = allTracks.find((t: Track) => t.id === dId);
                    return { trackTitle: trackObj?.title || 'Project tasks', entries };
                  } catch {
                    return { trackTitle: 'Project tasks', entries: [] };
                  }
                })
              );
              const deduped = new Map<string, SeamlessFieldRelationChoice>();
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
                    `Entry ${String(item.id).slice(-6)}`;
                  deduped.set(item.id, {
                    value: item.id,
                    label: `${primary} (${group.trackTitle})`,
                  });
                }
              }
              choicesByField[field.key] = Array.from(deduped.values());
              return;
            }

            const candidateTracks = allowCrossTrack
              ? allTracks.filter((t: Track) => {
                  if (!targetTrackTypes.size) return true;
                  const typeKey = slug(String(t.template_id || t.title || ''));
                  const titleKey = slug(String(t.title || ''));
                  return (
                    targetTrackTypes.has(typeKey) ||
                    targetTrackTypes.has(titleKey)
                  );
                })
              : currentTrack
                ? [currentTrack]
                : [];

            const entryLists = await Promise.all(
              candidateTracks.map(async t => {
                try {
                  const entries = await queryClient.fetchQuery({
                    queryKey: [
                      'entries',
                      'track',
                      t.id,
                      'relation-picker',
                      200,
                    ] as const,
                    queryFn: () =>
                      entriesApi.list({
                        track_id: t.id,
                        limit: 200,
                      }),
                    staleTime: RELATION_PICKER_STALE_MS,
                  });
                  return { track: t, entries };
                } catch {
                  return { track: t, entries: [] };
                }
              })
            );
            const deduped = new Map<string, SeamlessFieldRelationChoice>();
            for (const group of entryLists) {
              for (const item of group.entries) {
                if (
                  targetEntryTypes.size &&
                  !targetEntryTypes.has(slug(String(item.type)))
                ) {
                  continue;
                }
                if (
                  field.key === 'sprint' &&
                  anchorProjectId &&
                  !sprintLinkedToProject(item, anchorProjectId)
                ) {
                  continue;
                }
                const primary =
                  String(item.title || '').trim() ||
                  String(item.body || '').trim().slice(0, 80) ||
                  `Entry ${String(item.id).slice(-6)}`;
                const label = `${primary} (${group.track.title})`;
                deduped.set(item.id, { value: item.id, label });
              }
            }
            choicesByField[field.key] = Array.from(deduped.values());
          })
        );
        if (!cancelled) setRelationChoices(choicesByField);
      } catch {
        if (!cancelled) setRelationChoices({});
      } finally {
        if (!cancelled) setRelationLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    relationFieldsSig,
    activeTrackId,
    projectIdsSig,
    anchorProjectId,
    cachedTracks,
    tracksFetching,
    tracksError,
    queryClient,
  ]);

  type Row = {
    field: OperationalModelFieldSpec;
    text: string;
    /** Raw value, kept so JSON-table dispatch can read structure (not the stringified `text`). */
    raw: unknown;
    group?: string;
    inlineEditable: boolean;
    relationEditable: boolean;
  };
  const rows: Row[] = [];

  for (const field of ordered) {
    if (field.key.startsWith('_')) continue;
    const raw = values[field.key];
    const t = String(field.type || '').toLowerCase();
    const isInlineEditable =
      variant === 'detail' &&
      !!onCommitField &&
      INLINE_EDITABLE_TYPES.has(t) &&
      !field.readonly;
    const isRelationEditable =
      variant === 'detail' &&
      !!onCommitField &&
      !readOnly &&
      t === 'relation' &&
      !field.readonly;

    // Inline-editable fields always appear in the detail panel even when
    // the current value is empty — the widget itself shows the "—" placeholder.
    if (isInlineEditable) {
      const text = formatCustomFieldValue(field, raw, workflowEnumLabels?.[field.key]);
      rows.push({
        field,
        text,
        raw,
        group: (field.group || '').trim() || undefined,
        inlineEditable: true,
        relationEditable: false,
      });
      continue;
    }

    // Checklist json fields render a progress summary instead of raw JSON.
    if (t === 'json' && field.key === 'checklist') {
      const summary = formatChecklistSummary(raw);
      if (!summary && variant === 'card') continue;
      rows.push({
        field,
        text: summary || '—',
        raw,
        group: (field.group || '').trim() || undefined,
        inlineEditable: false,
        relationEditable: false,
      });
      continue;
    }

    if (!shouldRenderMetaField(field, raw, variant)) continue;

    // Relation / member fields skip the text rendering path — dedicated
    // resolvers hydrate ids to labels asynchronously in the JSX branch below.
    if (t === 'relation' || t === 'member') {
      rows.push({
        field,
        text: '',
        raw,
        group: (field.group || '').trim() || undefined,
        inlineEditable: false,
        relationEditable: isRelationEditable,
      });
      continue;
    }

    const text = formatCustomFieldValue(field, raw, workflowEnumLabels?.[field.key]);
    if (!text.trim()) continue;
    rows.push({
      field,
      text,
      raw,
      group: (field.group || '').trim() || undefined,
      inlineEditable: false,
      relationEditable: false,
    });
  }

  if (!rows.length) return null;

  const valueClampClass =
    variant === 'card' && !expanded ? 'line-clamp-2 overflow-hidden' : '';

  return (
    <section
      className={`${variant === 'card' ? 'mt-2' : 'mt-4'} ${className}`.trim()}
      aria-label={variant === 'card' ? 'Entry details' : 'Entry fields'}
    >
      <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
        {rows.map((row, idx) => {
          const prev = idx > 0 ? rows[idx - 1] : undefined;
          const showGroupHeader = Boolean(row.group && row.group !== prev?.group);
          const t = String(row.field.type || '').toLowerCase();
          // Array-of-objects JSON values (rubric_lines, line_items, …)
          // render as a structured read-only table instead of mono JSON.
          // Caller already shows ``row.field.name`` above the value, so the
          // editor's own ``label`` prop is left undefined.
          const jsonTable = t === 'json' && isJsonTableShape(row.raw);
          const fullBleed = t === 'markdown' || t === 'json';

          return (
            <Fragment key={row.field.key}>
              {showGroupHeader && row.group ? (
                <div
                  className={`col-span-1 sm:col-span-2 ${
                    idx > 0 ? 'border-t border-[var(--panel-border)] pt-3' : ''
                  }`.trim()}
                >
                  <p className="text-[12px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                    {row.group}
                  </p>
                </div>
              ) : null}
              <div className={`min-w-0 ${fullBleed ? 'sm:col-span-2' : ''}`.trim()}>
                <div className="text-[13px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
                  {row.field.name}
                </div>
                {row.inlineEditable && onCommitField ? (
                  <div className="mt-0.5">
                    <InlineFieldEditor
                      field={row.field}
                      value={values[row.field.key] ?? null}
                      readOnly={readOnly}
                      onCommit={newValue => onCommitField(row.field.key, newValue)}
                    />
                  </div>
                ) : row.relationEditable && onCommitField ? (
                  <div className="mt-0.5">
                    <SeamlessField
                      field={row.field}
                      value={values[row.field.key] ?? null}
                      onChange={async newValue => {
                        try {
                          await onCommitField(row.field.key, newValue);
                        } catch {
                          /* parent toasts */
                        }
                      }}
                      relationChoices={relationChoices[row.field.key] || []}
                      relationLoading={relationLoading}
                      onNavigate={onNavigate}
                      navContext={navContext}
                    />
                  </div>
                ) : jsonTable ? (
                  <div className="mt-0.5">
                    <JsonTableEditor
                      value={row.raw}
                      onChange={() => {
                        /* read-only — caller's commit pipeline is the
                           InlineFieldEditor / EntryComposer surface. */
                      }}
                      readonly
                    />
                  </div>
                ) : t === 'markdown' ? (
                  <div className={`mt-0.5 break-words text-[var(--text)] ${valueClampClass}`.trim()}>
                    <MarkdownContent compact={variant === 'card'}>{row.text}</MarkdownContent>
                  </div>
                ) : t === 'relation' ? (
                  // RelationValue resolves ids to labels asynchronously and
                  // hyperlinks each target (chips on cards, inline on detail).
                  <div
                    className={`mt-0.5 text-sm leading-relaxed ${valueClampClass}`.trim()}
                  >
                    <RelationValue
                      value={row.raw}
                      relation={row.field.relation}
                      variant={variant === 'card' ? 'chips' : 'inline'}
                      stopPropagation
                      onNavigate={onNavigate}
                      navContext={navContext}
                      emptyFallback={
                        <span className="italic opacity-60">
                          {row.field.key === 'sprint'
                            ? 'Not assigned to a sprint'
                            : '—'}
                        </span>
                      }
                    />
                  </div>
                ) : t === 'member' ? (
                  <div
                    className={`mt-0.5 text-sm leading-relaxed ${valueClampClass}`.trim()}
                  >
                    <MemberValue
                      value={row.raw}
                      variant={variant === 'card' ? 'chips' : 'inline'}
                      stopPropagation
                      emptyFallback={
                        <span className="italic opacity-60">—</span>
                      }
                    />
                  </div>
                ) : (
                  <div
                    className={`mt-0.5 text-sm leading-relaxed text-[var(--text)] whitespace-pre-wrap break-words ${valueClampClass}`.trim()}
                  >
                    {row.text}
                  </div>
                )}
              </div>
            </Fragment>
          );
        })}
      </div>
    </section>
  );
}
