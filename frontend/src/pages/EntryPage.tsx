import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { entriesApi } from '../api';
import { EntryDetail } from '../components/entries/EntryDetail';
import { trackPath } from '../utils/resourcePaths';
import type { Entry } from '../types';

/**
 * Full-page entry view — the route entry types opt into via
 * `form_schema.open_as_page: true` navigate to instead of the default
 * track-modal overlay (see TrackDetailPage.tsx's `handleEntryOpen`).
 * Thin wrapper: fetches the entry, then renders the SAME EntryDetail every
 * other app already uses, just with `variant="page"` swapping Modal for
 * EntryDetailPageChrome. "Closing" a page means navigating back to the
 * entry's track, not unmounting an overlay.
 */
export function EntryPage() {
  const { entryId } = useParams<{ entryId: string }>();
  const navigate = useNavigate();
  const [entry, setEntry] = useState<Entry | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!entryId) return;
    let cancelled = false;
    setEntry(null);
    setError(null);
    entriesApi
      .get(entryId)
      .then(e => {
        if (!cancelled) setEntry(e);
      })
      .catch(() => {
        if (!cancelled) setError('Entry not found or you no longer have access.');
      });
    return () => {
      cancelled = true;
    };
  }, [entryId]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <p className="text-sm text-[var(--text-muted)]">{error}</p>
      </div>
    );
  }

  if (!entry) {
    return (
      <div className="min-h-screen">
        <div className="h-14 border-b border-[var(--panel-border)] bg-[var(--panel)]" />
        <div className="mx-auto max-w-page px-4 sm:px-6 md:px-10 py-4 sm:py-5">
          <div className="h-40 animate-pulse rounded-[var(--radius-input)] bg-[var(--panel-2)]" />
        </div>
      </div>
    );
  }

  return (
    <EntryDetail
      key={entry.id}
      entry={entry}
      variant="page"
      onClose={() => navigate(trackPath(entry.track_id))}
      onUpdate={setEntry}
      // Guyana Payroll register redesign — surfaced a pre-existing gap
      // affecting EVERY open_as_page entry type (including NIS/PAYE
      // filings, which predate this fix): EntryDetail's delete button
      // only renders when `onDelete` is passed (see its `{onDelete ? ...
      // : null}` guard), and this page wrapper never wired it. The modal
      // path's own onDelete only patches a list cache — there's no list
      // here, so navigating back to the track (the entry no longer
      // exists to show) is this variant's equivalent.
      onDelete={() => navigate(trackPath(entry.track_id))}
    />
  );
}
