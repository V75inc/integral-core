# Conversation sidebar usability smoke check

Date: 2026-10-07. Local disposable Integral Business workspace at localhost:9108.

## Changes

- Search active conversations by title, matching all entered words regardless of case. Filtering keeps the open transcript unchanged. Clear or Escape restores the list.
- Rename from the conversation options menu. Save persists through the existing scoped chat endpoint; Cancel or Escape abandons the draft. Empty names are disabled and failed saves retain the editor with an error.
- Today and yesterday are split into overnight, morning, afternoon and evening using the browser's local time. The preceding seven days show individual dates. Each row shows its last activity time; the full name and timestamp remain available on hover.
- The system notification bar was initially changed to muted slate blue. The user subsequently selected the previous warm graphite color; it is restored in the entry utility-strip follow-up.
- The full-page entry header aligns its status dot, title and provenance badge on one line, with the title bounded by the available width.

## Evidence

The frontend build, type check, repository pre-commit guards and all 1,495 frontend tests passed. Grouping tests include same-day time periods and calendar boundaries.

In-browser checks on the existing populated history:

1. Searching `regenerate` narrowed the list to two conversations.
2. Renamed the successful PDF regeneration conversation to `Volunteer rota · PDF regeneration` through its options menu.
3. Searching `volunteer regeneration` found that record; after reload, the saved name and transcript were retained.
4. An unmatched search showed the empty state; Clear restored the full history.
5. Visually confirmed the slate-blue notification bar and inline entry heading.

Screenshots are in the local smoke evidence directory: `slate-bar-inline-entry-title.jpg`, `conversation-search-renamed-persisted.jpg`, and `conversation-sidebar-time-groups.jpg`.

Search covers authorized active conversation names, not message bodies or archived conversations. This is local browser evidence, not a benchmark for thousands of conversations or broader Venture Journey acceptance.
