# History direction/index experiment — 2026-10-16

## Scope

This disposable experiment used `7e16cda1` and the pinned jvspatial 0.2.2 public
index/query APIs. No production database, adapter source, dependency pin or
application runtime was changed. Both experimental source edits were reverted.
The two-shape, 20,000-row native fixture retained its original pagination and
descending/tied continuation no-sort assertion. No planner setting was forced.

## Hypothesis and exact change

Ascending `NULLS LAST` ordering cannot in general be satisfied by reversing a
descending `NULLS LAST` companion index: that reverses null placement too. The
experiment added a separate ascending workspace/log-level/timestamp/id index
and an ascending legacy timestamp/id index through `create_index`. The legacy
index was partial to DBLog, absent workspace attribution and CHANGE_EVENT.
A different fixed-prefix field order kept the new workspace index's canonical
name distinct from the existing descending index; no alternating repair of a
single canonical index was introduced. Singleton workspace/log-level membership
predicates were changed to scalar equality, exposing both the adapter's text
and typed companion equality constraints.

## Terminal evidence

The fixture run ended **one failed / one passed**, with two existing JUnit
`record_property` warnings. Source:
`/tmp/core-history-ascending-index-experiment.log` and `.xml`.

The tied fixture failed the existing descending workspace-continuation plan
assertion: PostgreSQL chose a bitmap heap scan followed by a top-N sort. The
sort consumed **906 matching rows** for a **100-row** page. The test was not
weakened to accept this regression.

The varying-timestamp shape preserved exact page IDs and deleted-boundary
continuation, but every sampled workspace plan and every ascending legacy plan
still included a sort. Its workspace plans did have workspace index conditions;
selectivity alone did not provide an ordered bounded-work plan. Its measured
two-page logger times were 164.99 ms ascending and 68.29 ms descending on this
shared disposable test host. These include connection/hydration overhead and
concurrent verification load; they are not production latency estimates or
stable comparison benchmarks.

## Conclusion and remaining work

Adding direction-specific indexes plus scalar equality is insufficient. The
pinned adapter still translates string ranges to text extraction and nullable
sort keys to JSONB extraction; query expression alignment and planner prefix
selection need a jointly qualified solution. A returned-row limit does not bound
rows scanned or sorted. R5 remains open. Neither a partial index nor a local
adapter monkeypatch should be shipped based on this experiment, and a passing
correctness fixture must not be reported as closing the query-plan requirement.
