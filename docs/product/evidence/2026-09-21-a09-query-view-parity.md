# A09 query and rendered-view parity evidence

**Acceptance item:** A09, exact query and every rendered view agree above page
limits and date boundaries.

## Fixture

`frontend/src/fixtures/a09QueryProjectionFixture.json` is the single source for
this proof. It contains 101 eligible Asset entries: 51 due on `2026-09-30` and
50 due on `2026-10-01`. It also contains one otherwise matching record on each
adjacent date (`2026-09-29` and `2026-10-02`). The 101st record crosses the
agent query's page size of 100.

## Assertions

`backend/tests/contracts/test_a09_query_view_parity.py` creates a real App and
Track graph, attaches each fixture Entry to that Track, and proves:

- the resident agent's bounded `QuerySpec` path returns records `a09-001`
  through `a09-100` on its first page and `a09-101` on its second;
- its inclusive date predicates exclude the two neighbouring-date records;
- the App dashboard's exact scoped metric returns 101; and
- its date-grouped chart returns `2026-09-30: 51` and `2026-10-01: 50`.

The same contract now also proves the WP-05 hardening closeout:

- a user who inherits App access but is explicitly excluded from `a09-001`
  receives 100 records from the agent query and a dashboard metric of 100;
- clearing policy and compiled-schema caches rebuilds the first exact query
  page with the identical ordered projection and cursor; and
- source-read failures raise a query failure (`query_unavailable` for Core
  open queries; a deterministic QuerySpec failure for the resident path)
  rather than yielding an empty successful result.

`frontend/src/components/views/__tests__/A09ViewParity.test.tsx` renders the
same fixture through production view components and proves:

- the table has 102 rows including its header, with both boundary records;
- the board has one `Available` column with a count of 101 and its last card;
- the calendar displays `+48 more` on September 30 and, after moving forward,
  `+47 more` on October 1; and
- the dashboard metric renders 101.

The board test substitutes only the pointer-collision transport from
`@dnd-kit`; card projection, grouping, and the rendered production board are
unchanged. This keeps a data-parity proof independent of jsdom geometry.

## Reproduction

```bash
PATH="$(pwd)/backend/.venv/bin:$PATH" \
  pytest backend/tests/contracts/test_a09_query_view_parity.py -q

cd frontend
npm run test:run -- --run src/components/views/__tests__/A09ViewParity.test.tsx
```

The release gate is still `make verify`; this focused evidence is retained so
future changes can diagnose a disagreement by surface rather than by a broad
suite failure.
