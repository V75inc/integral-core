import { useLayoutEffect, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateFeedCaches } from '../../queryKeys';
import type { Entry } from '../../types';
import type { ContributionDetailEdit } from './contributionLifecycle';

/**
 * What an app-designed detail region needs to save fields of the record shown.
 * A save that finishes after the person has moved to another record refreshes
 * caches only; it never brings the earlier record back.
 */
export function useDetailRecordEdit(
  entry: Entry,
  canEdit: boolean,
  show: (update: (shown: Entry) => Entry) => void,
  onUpdate: (saved: Entry) => void,
): ContributionDetailEdit | undefined {
  const queryClient = useQueryClient();
  const shownId = useRef<string | null>(entry.id);
  const latest = useRef({ show, onUpdate });
  useLayoutEffect(() => {
    shownId.current = entry.id;
    latest.current = { show, onUpdate };
  });
  useLayoutEffect(() => () => { shownId.current = null; }, []);
  // Stable between renders: app-designed detail regions re-read when it changes.
  return useMemo(
    () => canEdit ? {
      readOnlyKeys: Array.from(new Set([
        ...Object.keys(entry.read_time_fields?.values || {}),
        ...Object.keys(entry.read_time_fields?.status || {}),
      ])),
      onSaved: (saved: Entry) => {
        void queryClient.invalidateQueries({ queryKey: ['entry', saved.id] });
        void queryClient.invalidateQueries({ queryKey: ['entries'] });
        void queryClient.invalidateQueries({ queryKey: ['track', saved.track_id, 'entries'] });
        void invalidateFeedCaches(queryClient);
        if (shownId.current !== saved.id) return;
        latest.current.show(shown => (shown.id === saved.id ? saved : shown));
        latest.current.onUpdate(saved);
      },
    } : undefined,
    [canEdit, entry.read_time_fields, queryClient]
  );
}
