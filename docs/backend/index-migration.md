# Index migration: per-record-kind indexes (INTEGRAL-15)

Core stores every record kind in three shared tables (`object`, `node`, `edge`).
jvspatial builds, for each indexed field, an index that leads with the entity column
and covers **every row of the table**. A write of one kind therefore updates the
indexes of every other kind too.

## Measured

`backend/scripts/index_usage_report.py --dsn ...` (read only) on two local databases:

| database | table | indexes | size | never scanned | unused size | partial |
|---|---|---|---|---|---|---|
| benchmark (`integral`) | object | 129 | 14.1 GB | 82 | 9.6 GB | 17 |
| benchmark (`integral`) | node | 66 | 959 MB | 34 | 518 MB | 3 |
| benchmark (`integral`) | edge | 11 | 577 MB | 1 | 8 KB | 1 |
| 9106 instance | object | 145 | 4.8 GB | 116 | 4.1 GB | n/a |

Statistics reset on restore, so "never scanned" is a lead, not proof: judge it after a
representative period of real traffic.

Experiment (scratch database, 300 000 rows of three kinds, 50 000 mixed inserts):

| layout | insert 50k rows | query for the indexed kind (avg of 30) | same under a forced generic plan |
|---|---|---|---|
| entity-leading index (today) | 2.27 s | 0.31 ms | n/a |
| partial index for that kind | 1.34 s (41% faster) | 0.31 ms | 0.38 ms (still used) |

The partial index is chosen by the planner and the query is not slower; only the
writes of other kinds stop paying for it.

## Plan (expand, verify, switch, remove)

1. **Expand.** Set `INTEGRAL_PARTIAL_INDEXES=1` before the API boots. Core's own
   declarations (`backend/app/models/indexing.py`) then ask jvspatial for per-kind
   (`WHERE entity = 'Kind'`) indexes; `ensure_indexes` ADDS them beside the existing ones.
   Unique indexes are untouched. Nothing is dropped. Off by default.
   - jvspatial creates indexes with a plain `CREATE INDEX IF NOT EXISTS`, which blocks
     writes to the table while it builds. On a large table pre-create the same index
     out of band with `CREATE INDEX CONCURRENTLY` under the same name (the boot step
     then skips it). Take the name and definition from the boot log or from
     `python -c` on `GraphContext.get_indexes()`.
2. **Verify.** For each hot query, compare `EXPLAIN (ANALYZE)` before and after, then
   re-run `index_usage_report.py` after a week of traffic: the new partial indexes should
   show scans and the entity-leading twins should stop.
3. **Switch.** Nothing to switch: the planner picks the cheaper index. Keep the old ones
   while verifying.
4. **Remove (a later release, with a verified backup).** `DROP INDEX CONCURRENTLY` only
   the entity-leading indexes the report shows as never scanned over the verification
   period and whose per-kind twin exists. The report script lists candidates; it never
   drops.

## Not Core's to change

The `DBLog` indexes (log level, path, event code, status code, logged time) are declared
inside jvspatial (`jvspatial/logging/models.py`) and account for most of the unused size.
`jvspatial-dblog-partial-indexes.patch` (beside this file) makes them per-kind; it applies
cleanly to jvspatial 0.2.5 and needs to go upstream. Core cannot change them.

## Left as they are

The five indexes `change_event_logger.py` creates directly are already per-kind
(`partial_by_entity=True`, added with the events fix).
