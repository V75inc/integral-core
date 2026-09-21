# WP-05 query and projection contract

WP-05 makes an exact operational question mean the same thing wherever it is
asked. This note records the current contract for the governed Core query and
App dashboard paths. It is an implementation contract, not completion evidence
for WP-05.

## One field and filter vocabulary

Core-open queries and dashboards use the shared field resolver and filter
operators. Entry fields use their explicit paths, including
`custom_fields.<field_key>`; a missing or incompatible value is not treated as
a match. Dashboard legacy filter maps are normalized to the same typed filter
expressions before they are persisted or evaluated.

Core `track` and `app` queries permit only their documented resource fields.
An unsupported filter, sort, or projection field is a bad request, rather than
an empty response or a silently broadened scan. App rows use `name`; they do
not invent a `title` alias.

## Ordering, projections, and pages

`integral_governed_query` accepts an allowed Core field for ascending order and
the same field prefixed by `-` for descending order. Rows are ordered
deterministically with their identifier as the tie breaker. A projection emits
only the requested supported fields plus the resource kind.

The Core query response retains its opaque cursor. Candidate collection itself
walks every graph page before applying exact filters and the requested result
page, so a record after the former fixed first-page limit remains queryable.
The same rule applies to the dashboard's App track traversal and activity
digest: a count or digest is not silently truncated after a convenient first
page or first group of tracks.

## Dashboard scope

A dashboard belongs to one App. `track_id` and `track_ids` therefore select
only tracks attached to that App. An identifier outside the App is an explicit
widget-data error. A declared multi-track scope is honoured by counts, rows,
charts, activity digests, and track breakdowns; it must never quietly expand to
the entire App.

Permission filtering remains in the underlying entry and activity services.
Aggregates are calculated only from records that the requesting principal may
read. A resolver failure is returned as an error payload by dashboard data
resolution, never presented as a zero count or "none found."

## Remaining proof

The contract still needs the WP-05 exit fixture: a known dataset larger than a
page, rendered table/board/calendar/dashboard assertions across a date
boundary, and an agent exact-query trace over the same fixture. That evidence
will decide A09 in the acceptance ledger.
