# Dashboard pie chart layout and reference labels

Pie widgets used a fixed 160 px canvas, fixed radii and an in-canvas legend
inside a card that clips overflow. Generated/saved short cards and long legends
could hide the plot. Charts now fill the available area with proportional radii.
Pie cards have a minimum rendered size based on the dashboard row height; wide
cards place the legend beside the plot, narrow cards below it. The legend has
its own scrolling area, readable values and percentages. The contributing-record
action retains reserved space. Existing saved dashboards benefit on reload.

Grouped chart data also discarded the distinction between identity and display
label. Charts now retain their raw group key for drill-through and resolve Entry
and Track identities through the existing recipient/workspace admission service.
Custom grouping fields are treated as references only when the visible schema
declares a relation/member field. Ordinary text and diagnostic values are not
reinterpreted. Denied, missing or unavailable reference labels get readable
fallback text; values and totals remain intact. Label reads are bounded to 100
unique identities per chart. No additional model request is needed.

This preserves I-ACCESS-01 and I-FIELD-RESOURCE-01: reference names require
current read permission and matching workspace scope, and canonical IDs remain
execution inputs rather than ordinary display labels. No persisted graph types,
mutation authority, query predicates or App-specific behavior were added.

## Browser qualification

The reported Personal Life Manager App is absent from the isolated QA profile.
A temporary browser fixture used the production chart/layout components with
18 synthetic named categories and short/tall cards. It was removed afterward;
existing Apps and user records were not modified.

- Desktop: both complete pie shapes fit their cards; wide legends sit beside
  the plot and scroll independently. The short saved card expands to fit.
- Mobile (390 px): both complete pies fit within 344 px cards. No horizontal
  page overflow (384 px document width). Long labels wrap and legends scroll.
- Automatic-height Home card: a 300 px card retained a complete 121 px pie.
- Selecting the final legend item succeeds and emits its original group key.
- Screenshots: `.local/design-review/pie-chart-desktop.png` and
  `.local/design-review/pie-chart-mobile.png` (local QA evidence).

## Regression coverage

Focused dashboard backend tests cover the aggregate projection and real graph
label admission, revoked Entry access, workspace mismatch, typed versus ordinary
text fields and lookup failure. The four new label tests also pass against a
separate PostgreSQL test database. Frontend coverage checks named slice
drill-through, values/percentages, hidden legends and minimum card sizes across
different dashboard row heights. The full frontend run passed 347 files / 2,095 tests; the production build,
pinned checks, guards and wheel/import qualification passed.

The complete default backend suite was exercised. Two failures in that development
snapshot were corrected: the new fixture now uses the aggregate contract’s bare
custom-field key, and ordinary date/category series keep their existing shape
when a separate display key is unnecessary. The complete 62-test dashboard/query/
A09 contract group then passed against the corrected source. Final-source CI-style smoke also passed before commit.
