# List filters and pagination

Every table list in Integral can be filtered, sorted and paged by page number, for any App, with no App code. This
page says what a person sees, what an App author controls, how it works, and where it stops.

## What a person sees

- **Funnel on a column heading.** Opens a menu made for that column's type:

  | Column holds | Menu |
  | --- | --- |
  | Text | Pick values from what exists (checkboxes with counts, searchable), or **Type to match** (contains / is exactly) |
  | Choice (select) | Checkboxes for the choices, shown with the field's display wording |
  | Link to another record (Department, Employee) | Checkboxes of the linked records by name, with counts |
  | Number | At least / at most |
  | Date, date and time | Quick ranges (Today, Last 7 days, This month, Last month, This year) or From / To |
  | Yes / No | Yes, No |

- **Filters button in the track toolbar.** The same menus for every field of the track, whichever view is open
  (table, board, gallery, feed, calendar). Entry type and tag choices sit above them; the entry type choice is
  hidden when the list has only one type.
- **Chips above the list.** Each active filter is a removable chip ("Gross: 200,000 – 300,000", "Department:
  Design, Sales"), with the exact result count and **Clear all**. A funnel turns blue while its column is filtered.
- **Several filters combine with AND.** Several picks inside one column combine with OR.
- **Sorting.** Click a heading to sort the whole list (not only the rows on screen); click again to reverse.
- **Page controls** under a table list: `26–50 of 986 entries`, first / previous / page numbers / next / last,
  and **Rows per page** (10, 25, 50, 100; remembered in the browser). Changing a filter, the order or the page
  size returns to page 1. Rows stay on screen while the next page loads.
- **A pay run's related lists** (for example its payslips) page the same way.
- **Row density and columns.** A **Columns** menu shows or hides columns; **Comfortable / Compact** sets row
  height (remembered). Cells are one line with the full value on hover.

## Is it automatic for other Apps?

Yes. The menus are built from the Track's own field definitions (`type`, `enum`, `enum_labels`, `relation`), so
a new App, track or field gets filters and paging the moment it has a table view. Nothing is registered.

What an App author can influence, all in `operational-model.yaml`:

- `type` of a field decides the menu (see the table above). Fields typed `markdown`, `json`, files and the like
  have no filter. Hidden fields have none.
- `enum_labels` on a select field changes the wording shown (`biweekly` shown as *Fortnightly*); the stored value,
  the filter and the rules still use the stored value.
- Columns of the table view decide which funnels appear on headings; the toolbar **Filters** menu lists every
  non-hidden field.

Not covered yet: board, gallery, feed and calendar views have the toolbar filters and chips but not page numbers
(they keep scrolling to load more); an App's own React screens use the SDK `DataTable`, which pages but does not
filter.

## How it works

The browser sends the filters; the **server applies them before it pages**, so totals, page contents and counts are
always consistent and nothing is filtered in the browser.

| Call | Purpose |
| --- | --- |
| `GET /tracks/{id}/entries?dashboard_filters=[…]&view_id=…&offset=…&limit=…&sort=…&include_total=…` | One page. `dashboard_filters` is a JSON list of `{field, op, value}` (at most 8). `field` is the stored path: `custom_fields.<key>`, `title`, `created_at`. `op` is `eq`, `neq`, `in`, `not_in`, `contains`, `gt`, `gte`, `lt`, `lte`, `exists`. `sort` is `field` or `-field`. `offset` pages by position and replaces the cursor. The view's own entry types and filters still apply. |
| `GET /tracks/{id}/facets?field=…&dashboard_filters=[…]` | The values one field holds, with counts, for the pick-lists (`{values:[{value,count}], truncated, distinct}`). Counts follow the other active filters. |
| `GET /entries/{id}/related?relation=…&offset=…&limit=…` | A related list's page; the response carries `total`. |

Details that matter:

- **Access.** Only records the caller may read are listed or counted; facets use the same admission as the list.
- **Link columns** store ids. The pick-list asks the server for the ids and counts, then resolves names with the
  existing access-checked `POST /entry-lookup`, only for the ids listed.
- **Totals.** The list is counted on page 1 and whenever the filters change; moving between pages reuses it.
- **Chip wording and picked names** are client-side only (`labels` on a filter is never sent).
- **Cache.** Each page is its own cached read keyed by filters, order, page and size, so optimistic edits show in
  a filtered or paged list.

## Performance and what it does not do

- **No new indexes.** Nothing here creates a database index. Filters use the existing record indexes or scan the
  track's records; sorting by a custom field orders the filtered set in the database. Very large lists (hundreds of
  thousands of records) sorted by an unindexed field will be slow; narrow with a filter first.
- **Pick-lists are bounded.** One request per open menu (cached 30 s) reads one field from at most 20,000 records and
  returns the 200 most common values; `truncated` / `distinct` say when it stopped early. Searching a pick-list
  happens in the browser. Use **Type to match** for values beyond the 200 most common.
- **Deep pages** cost more than early ones (the database skips rows). Prefer a filter to paging past page ~100.
- Relation columns that hold several records are matched when any of them is picked.

## Where the code is

| Piece | File |
| --- | --- |
| Filter model (what a column offers, chip wording, date presets) | `frontend/src/components/views/columnFilterModel.ts` |
| Column menu | `frontend/src/components/views/ColumnFilterMenu.tsx` |
| Pick-list | `frontend/src/components/views/FacetList.tsx` |
| Chips | `frontend/src/components/views/FilterChips.tsx` |
| Page controls | `frontend/src/components/ui/ListPager.tsx` |
| Popover | `frontend/src/components/ui/AnchoredPopover.tsx` |
| Table (funnels, sorting, columns, density) | `frontend/src/components/views/TableWidget.tsx` |
| Track toolbar filters | `frontend/src/components/tracks/TrackFilterStrip.tsx` |
| Page state (filters, page, size, sort, totals) | `frontend/src/pages/TrackDetailPage.tsx` |
| Entries list, sort, offset | `backend/app/api/tracks.py`, `backend/app/services/entry_listing.py` |
| Pick-list values | `backend/app/services/entry_facets.py` |
| Related list paging | `backend/app/api/entry_relations.py` |

Tests: `columnFilters.test.tsx`, `ListPager.test.tsx`, `TableWidget.test.tsx`, `TrackFilterStrip.test.tsx`,
`backend/tests/test_track_list_paging.py`.
