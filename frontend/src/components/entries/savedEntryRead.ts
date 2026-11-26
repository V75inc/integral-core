import { entriesApi } from '../../api';
import { errorMessageFromAxios } from '../../api/helpers';
import type { Entry } from '../../types';

export function hasRevisions(entry: Entry): boolean {
  return Number.isSafeInteger(entry.record_revision) && (entry.record_revision ?? 0) > 0 &&
    Number.isSafeInteger(entry.schema_revision) && (entry.schema_revision ?? 0) > 0;
}

/** True when ``next`` is an earlier revision of the record than ``shown``. */
export function isOlderRevision(next: Entry, shown: Entry): boolean {
  return (next.record_revision ?? 0) < (shown.record_revision ?? 0);
}

/**
 * Read a record back after a successful write. A successful write must never be
 * reported as a failed save or offered for blind replay: when the full read
 * fails, structural context is retained, live projections become explicitly
 * unknown (and stay protected), and ``refreshError`` asks for a read-only retry.
 */
export async function readSavedEntry(
  entryId: string,
  saved: Entry,
  prior: () => Entry,
): Promise<{ complete: Entry; refreshError: string | null }> {
  // The server still enforces authorization, field validation, approvals
  // and both revisions. Only an authoritative response changes read mode.
  if (saved.id !== entryId) throw new Error('Unexpected saved record');
  try {
    const complete = await entriesApi.get(entryId);
    if (complete.id !== entryId || !hasRevisions(complete) ||
        (complete.record_revision ?? 0) < (saved.record_revision ?? 0)) {
      throw new Error('Unexpected refreshed record');
    }
    return { complete, refreshError: null };
  } catch (cause) {
    const before = prior();
    const keys = Array.from(new Set([
      ...Object.keys(before.read_time_fields?.values ?? {}),
      ...Object.keys(before.read_time_fields?.status ?? {}),
    ]));
    const complete: Entry = { ...before, ...saved, ...(keys.length ? { read_time_fields: {
      values: Object.fromEntries(keys.map(key => [key, null])),
      status: Object.fromEntries(keys.map(key => [key, 'unavailable' as const])),
    } } : {}) };
    return { complete, refreshError: errorMessageFromAxios(cause, 'The latest entry could not be loaded.') };
  }
}
