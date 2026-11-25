import { useState } from 'react';
import { entriesApi } from '../../api';
import { errorMessageFromAxios } from '../../api/helpers';
import type { Entry } from '../../types';

interface DateDraft {
  entryId: string;
  values: Record<string, unknown>;
  recordRevision: number;
  schemaRevision: number;
}

/**
 * Explicit-save draft for date fields on a record's reading view, matching the
 * per-field editing of the standard detail view: picking a date changes only
 * this draft, and Save writes it against the revisions the person was looking
 * at. Nothing is saved by blur or by closing the picker.
 */
export function useDetailDateDraft(
  entry: Entry | null,
  onSaved: (entry: Entry) => void,
) {
  const [stored, setDraft] = useState<DateDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A draft never follows the widget to another record.
  const draft = stored && stored.entryId === entry?.id ? stored : null;

  function change(patch: Record<string, unknown>) {
    if (saving || !entry?.record_revision || !entry.schema_revision) return;
    setError(null);
    setDraft({
      entryId: entry.id,
      recordRevision: draft?.recordRevision ?? entry.record_revision,
      schemaRevision: draft?.schemaRevision ?? entry.schema_revision,
      values: { ...draft?.values, ...patch },
    });
  }

  function cancel() {
    if (saving) return;
    setDraft(null);
    setError(null);
  }

  async function save() {
    if (!draft || saving) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await entriesApi.update(draft.entryId, {
        custom_fields: draft.values,
        expected_record_revision: draft.recordRevision,
        expected_schema_revision: draft.schemaRevision,
      });
      // The write succeeded; a failed re-read must not offer it for replay.
      const complete = await entriesApi.get(draft.entryId).catch(() => saved);
      setDraft(null);
      onSaved(complete);
    } catch (cause) {
      setError(errorMessageFromAxios(cause, 'Could not save. Your change is still here.'));
    } finally {
      setSaving(false);
    }
  }

  return { values: draft?.values, saving, error, change, save, cancel };
}
