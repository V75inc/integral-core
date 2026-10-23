# Multiple tag filters

## Behavior and repair

The feed filter stored a single tag in state/URL and used a single-select menu.
EntryCard also followed a single-tag hyperlink after its click callback, which
could overwrite the parent's selection. Track views already matched all chosen
tags; the feed now uses that same intersection rule.

The feed tag menu uses independently selectable checkboxes, with tag ownership
labels to distinguish identical names. Each active tag has its own removable
chip; clearing all remains available. Repeated `tag` URL parameters preserve
reload/back/deep-link behavior. Existing single-tag URLs remain valid. Plain tag
clicks use the parent's toggle once; modified clicks and copied links carry the
same tag selection in their destination and retain App/author URL filters.

`GET /feed`, `/feed_entries`, and `/entries` accept optional `tag_ids` as a
comma-separated list of exact tag IDs. Existing `tag_id` is retained and combined
with it. Duplicates/empty components are normalized. At most 20 distinct tag
filters and 4,096 characters of list input are accepted. The existing listing
service compiles the list into one `tags` membership `$all` predicate before
permission-scoped pagination and totals. No graph schema or write contract
changes are involved (I-ACCESS-01, I-ROLE-01/02 remain unchanged).

## Atomic reset

Reset filters previously called separate App and tag URL setters in one event.
React Router's search-parameter callbacks do not queue like React state updates;
a later write could restore the old App query parameter. Reset now uses the
same parent-owned atomic clear-all action as the main Clear all control. It clears
App, Track, entry type, all tags, author, search and retrieval results together.
It is available for any active filter, including an author/search-only state.

The component regression verifies the parent callback runs once without competing
individual URL writes. Live browser verification set App, Track, type, two tags,
author and search, then pressed Reset filters. The URL became `/feed`, App showed
All Apps, Track showed All tracks, type showed All types, search was empty,
all tag checkboxes were unchecked, author/tag chips vanished and two records
returned. Screenshot: `.local/design-review/feed-reset-all-filters.png`.

## Evidence

- HTTP smoke regression creates two records with both tags, one with only one
  tag and one untagged. All three endpoints return only the two matches across
  cursor pages with `limit=1`, preserve exact totals, accept legacy+new inputs,
  return no rows for a missing tag and reject more than 20 selections.
- Existing same-name/different-Track tag identity and pre-pagination tests pass.
- Menu regression selects two same-named tags from different Tracks, removes
  one without clearing the other, then clears the remaining selection.
- URL regression covers legacy links, repeated tags, duplicates and preserving
  App/author filters. Existing Track multi-tag tests remain green.
- Live preview on port 9155: two new synthetic tags were assigned to the QA
  records created for previous smoke checks. Team alone returns two records;
  adding Urgent by clicking the record tag returns one and keeps Team selected.
  Both chips and checkboxes remain selected after reload. Adding a second tag
  from the entry detail footer also preserves the first and the App scope. Removing Urgent
  restores two records while Team stays active. App and Track scopes also retain
  both selections and return the one matching record.
- Screenshots: `.local/design-review/multiple-tag-results.png` and
  `.local/design-review/multiple-tag-filter-menu.png`.
- Focused feed backend suite and full default backend suite (four xdist workers)
  passed. Both tag-filter HTTP regressions also passed on a separate PostgreSQL
  database. Final frontend run: 348 files / 2,100 tests;
  production build passed, including the additional reset regression.
- Final CI-style smoke passed with TESTING=1, DEBUG=false, no .env and xdist.
  Guards, all pre-commit checks, pinned formatters/lint and frontend types passed.

Original user Apps and records were not changed. Synthetic QA records alone
received the two new test tags. Hosted Business has not been deployed.
