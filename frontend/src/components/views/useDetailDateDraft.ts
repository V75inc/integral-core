import { useLayoutEffect, useRef, useState } from 'react';
import { entriesApi } from '../../api';
import { errorMessageFromAxios } from '../../api/helpers';
import { hasRevisions, readSavedEntry } from '../entries/savedEntryRead';
import type { Entry } from '../../types';

interface DateDraft {
  entryId: string;
  /** The values on screen when editing began, from the same revision. */
  base: Record<string, unknown>;
  values: Record<string, unknown>;
  recordRevision: number;
  schemaRevision: number;
}

export interface DetailDateSave {
  entry: Entry;
  /** False when the person has moved to another record since the save began. */
  stillShown: boolean;
}

type Notice = { entryId: string; message: string } | null;

/**
 * Explicit-save draft for date fields on a record's reading view, matching the
 * per-field editing of the standard detail view: picking a date changes only
 * this draft, and Save writes it against the revisions the person was looking
 * at. Nothing is saved by blur or by closing the picker. A save belongs to the
 * record it began on: finishing late never touches another record's draft.
 */
export function useDetailDateDraft(
  entry: Entry | null,
  onSaved: (result: DetailDateSave) => void,
) {
  const [stored, setDraft] = useState<DateDraft | null>(null);
  const [savingIds, setSavingIds] = useState<readonly string[]>([]);
  const [failure, setFailure] = useState<Notice>(null);
  const [stale, setStale] = useState<Notice>(null);
  const [refreshing, setRefreshing] = useState(false);
  // Changes whenever another record is shown, and when the widget goes away.
  const session = useRef(0);
  const entryId = entry?.id;
  useLayoutEffect(() => () => { session.current++; }, [entryId]);

  const draft = stored && stored.entryId === entryId ? stored : null;
  const saving = Boolean(entryId && savingIds.includes(entryId));

  function change(patch: Record<string, unknown>, shown: Record<string, unknown>) {
    if (saving || !entry?.record_revision || !entry.schema_revision) return;
    setFailure(null);
    setDraft({
      entryId: entry.id,
      base: draft?.base ?? shown,
      recordRevision: draft?.recordRevision ?? entry.record_revision,
      schemaRevision: draft?.schemaRevision ?? entry.schema_revision,
      values: { ...draft?.values, ...patch },
    });
  }

  function cancel() {
    if (saving) return;
    setDraft(null);
    setFailure(null);
  }

  async function save() {
    if (!entry || !draft || saving) return;
    const writing = draft;
    const token = session.current;
    setSavingIds(ids => [...ids, writing.entryId]);
    setFailure(null);
    try {
      const saved = await entriesApi.update(writing.entryId, {
        custom_fields: writing.values,
        expected_record_revision: writing.recordRevision,
        expected_schema_revision: writing.schemaRevision,
      });
      const { complete, refreshError } = await readSavedEntry(writing.entryId, saved, () => entry);
      setDraft(current => (current === writing ? null : current));
      const stillShown = token === session.current;
      if (stillShown) {
        setStale(refreshError ? { entryId: writing.entryId, message: refreshError } : null);
      }
      onSaved({ entry: complete, stillShown });
    } catch (cause) {
      if (token === session.current) {
        setFailure({ entryId: writing.entryId,
          message: errorMessageFromAxios(cause, 'Could not save. Your change is still here.') });
      }
    } finally {
      setSavingIds(ids => ids.filter(id => id !== writing.entryId));
    }
  }

  /** Read the saved record again. Never writes. */
  async function refresh() {
    if (!entryId || refreshing) return;
    const token = session.current;
    setRefreshing(true);
    try {
      const current = await entriesApi.get(entryId);
      if (current.id !== entryId || !hasRevisions(current)) throw new Error('Unexpected refreshed record');
      if (token === session.current) {
        setStale(null);
        onSaved({ entry: current, stillShown: true });
      }
    } catch (cause) {
      if (token === session.current) {
        setStale({ entryId,
          message: errorMessageFromAxios(cause, 'The latest entry could not be loaded.') });
      }
    } finally {
      setRefreshing(false);
    }
  }

  return {
    base: draft?.base,
    values: draft?.values,
    saving,
    error: failure && failure.entryId === entryId ? failure.message : null,
    refreshError: stale && stale.entryId === entryId ? stale.message : null,
    refreshing,
    change,
    save,
    cancel,
    refresh,
  };
}
