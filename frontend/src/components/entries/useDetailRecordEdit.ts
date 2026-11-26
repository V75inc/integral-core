import { useLayoutEffect, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateFeedCaches } from '../../queryKeys';
import type { Entry } from '../../types';
import type { ContributionDetailEdit } from './contributionLifecycle';
import { isOlderRevision } from './savedEntryRead';

/**
 * What an app-designed detail region needs to save fields of the record shown.
 * A save that finishes after the person has moved away, or that is older than
 * the record now shown, refreshes caches only.
 */
export function useDetailRecordEdit(
  entry: Entry,
  canEdit: boolean,
  show: (update: (shown: Entry) => Entry) => void,
  onUpdate: (saved: Entry) => void,
): ContributionDetailEdit | undefined {
  const queryClient = useQueryClient();
  const shownEntry = useRef<Entry | null>(entry);
  const latest = useRef({ show, onUpdate });
  useLayoutEffect(() => {
    shownEntry.current = entry;
    latest.current = { show, onUpdate };
  });
  useLayoutEffect(() => () => { shownEntry.current = null; }, []);
  // Stable between renders: app-designed detail regions re-read when it changes.
  return useMemo(
    () => canEdit ? {
      readOnlyKeys: Array.from(new Set([
        ...Object.keys(entry.read_time_fields?.values || {}),
        ...Object.keys(entry.read_time_fields?.status || {}),
      ])),
      onSaved: (saved: Entry, { current }: { current: boolean }) => {
        void queryClient.invalidateQueries({ queryKey: ['entry', saved.id] });
        void queryClient.invalidateQueries({ queryKey: ['entries'] });
        void queryClient.invalidateQueries({ queryKey: ['track', saved.track_id, 'entries'] });
        void invalidateFeedCaches(queryClient);
        const accepts = (shown: Entry) =>
          shown.id === saved.id && !isOlderRevision(saved, shown);
        const shown = shownEntry.current;
        if (!current || !shown || !accepts(shown)) return;
        latest.current.show(now => (accepts(now) ? saved : now));
        latest.current.onUpdate(saved);
      },
    } : undefined,
    [canEdit, entry.read_time_fields, queryClient]
  );
}
