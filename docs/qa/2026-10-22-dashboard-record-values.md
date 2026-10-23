# Dashboard contributing-record values

## Reproduction and repair

In the isolated preview, Task Overview's status pie contained two synthetic
Tasks with saved `In progress` values. Selecting that slice displayed the
values; choosing **View contributing records** for the whole chart displayed
`—` for the same entries. The all-records query did not select the grouping
field unless a slice was chosen. Separately, the table ignored selected
platform fields such as `status` and creation/update dates.

The drill-through query now selects its grouping field for both entry points,
including custom fields and date buckets, without adding a group filter to the
all-records action. Selected filter fields and explicit status filters also
retain their values. The table displays selected status/date/tag values alongside
custom values, with readable field-key labels. Zero and false remain visible;
null and empty strings use an empty-value marker.

The existing broker, QuerySpec schema checks, workspace/resource admission,
bounded projection and governed paging remain authoritative (I-ACCESS-01).
No graph writes, permission changes or query-predicate broadening are involved.
The table remains a view of the fields used by the widget, not a full Entry editor.

## Qualification

- Six new backend compiler cases failed against the old implementation and pass
  with the repair: platform/custom groups, business-date groups and filter fields.
- The HTTP regression uses real App/Track/Entry fixtures and the governed broker
  to verify an unfiltered status chart returns the saved value and no group filter.
- The component regression failed before the repair and now verifies saved status,
  zero, false and null, without rendering internal Entry/Track IDs as values.
- Live browser readback after restarting the isolated API confirms both synthetic
  Tasks display `Status: In progress` when opening the whole chart's contributors.
  Screenshot: `.local/design-review/dashboard-record-values.png`.
- Focused backend dashboard tests: 43 service tests and seven HTTP tests passed.
- Full frontend suite: 347 files / 2,096 tests passed; production build passed.
- Full default backend suite passed with four isolated xdist workers.
- Pinned formatting/lint, frontend types, guards and all pre-commit checks passed.
- Final CI-style smoke run passed (`TESTING=1`, `DEBUG=false`, no `.env`, xdist).
- New compiler cases and the HTTP regression are part of the PR smoke marker.
- The governed HTTP regression also passed on a separate PostgreSQL test database.

The preview is isolated on UI port 9155/API port 4473. No original user App or
record was changed to reproduce or verify this issue.
