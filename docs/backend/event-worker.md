# Fast record saves and event delivery

Record commands finish after authorization, validation, required business hooks,
record/relationship writes and durable event intent commit. They do not wait for
audit persistence, WebSocket fan-out, routine wakeups or consumer execution.
A save response confirms business state and durable intent; audit/feed visibility
is eventual. Required calculation and approval semantics are unchanged.

## Process ownership

Run the same Core artifact and database configuration in two processes:

```sh
integral worker events
integral worker events --status
integral worker events --check
integral worker events --once
integral worker events --replay EVENT_FACT_ID
```

The worker initializes the prime store and PostgreSQL logging store without
importing `app.main`, starting HTTP, or bootstrapping the resident harness. Its
independent database pools must reach the same configured stores as the API.
Use `JVSPATIAL_DB_TYPE`, `JVSPATIAL_POSTGRES_DSN`, `JVSPATIAL_LOG_DB_TYPE` and
`JVSPATIAL_LOG_POSTGRES_DSN`; use the deployment's established credential source.
Do not put credential values in reports or command examples.

Business Compose includes `events-worker`; the local-Core overlay selects the
same mounted source and image build as its API. Standalone hosts must supervise
this command beside their API. Starting only the API leaves events durably queued.
The API's recovery loop retains entry and bundle cleanup, not audit delivery.

## Durability and ordering

`OperationEventOutbox` intent shares the graph command's PostgreSQL transaction.
Enqueue failure rolls back the command. Worker failure does not invalidate a
committed command. Original actor identity, scope, snapshots, provenance and
occurrence time are retained; queued facts grant no new write permission.
Forensic `policy.deny` events retain independent logging-store persistence so
rejected graph transactions cannot erase refusal evidence.

The worker reads at most 100 eligible facts per pass, with a one-second idle
poll. Claims have unique owners, a 60-second renewable lease and fenced
acknowledgements. Failed attempts back off exponentially up to 60 seconds and
become dead letters after five attempts. Explicit replay retains event identity.
Crash recovery can repeat delivery, so audit IDs derive from durable fact IDs.
A completed audit row is reused after a crash before outbox acknowledgement.
Missing logging storage is a failed delivery, never a successful acknowledgement.

Audit persistence serializes a small delivery clock transaction in the logging
store. `DBLog.logged_at` is delivery order, while `log_data.ts` and the public
`ts` retain occurrence time. Historical rows fall back to their existing time.
Polling cursors use storage delivery time rather than public occurrence time;
late retries cannot fall behind a forward cursor. Response fields and confidential
principal/workspace/filter cursor bindings remain unchanged. Public timestamps
are therefore not guaranteed to increase across late-delivered events.

The API relay reads bounded persisted pages every 500 ms and enqueues events to
local sockets. Each client has a 100-event queue, fresh send-time permission
checks and bounded authorization/send waits. Overflow or stalled clients
reconnect and catch up through the authorized polling API. Relays never execute
server consumer hooks. Durable work triggers and registered hooks advance their
own checkpoints in independent worker tasks; a stalled consumer cannot block
audit delivery or other consumers. Successful consumer stages do not rerun merely
because another consumer failed. Registered hooks must make external effects
idempotent using event identity; the sample's graph write commits atomically with
its consumer checkpoint. Each callback event commits separately. Consumer failures
back off exponentially and stop that consumer after five attempts. Other consumers
and audit delivery continue. `--status` exposes `consumer_replay_ids`; pass one to
`--replay` to retry the blocked consumer without rewinding completed events.

## Observation and qualification

`--status` reports pending count, oldest pending age, dead letters and the last
heartbeat. The worker warns when pending age exceeds 30 seconds or any dead
letters exist. Normal-load delivery target is two seconds. `--check` exits nonzero on a missing/stale (over 30 seconds) heartbeat, old
pending events or dead letters; Compose uses it as the worker healthcheck. A dead
worker cannot report itself, so supervision must monitor that check.

Enable `INTEGRAL_PERF_TRACE=1` for content-free save, authorization, validation,
business-hook, enqueue and delivery spans plus existing database observations.
It also enables transaction/lock timing. Enable `VITE_PERF_TRACE=1` to collect
`entry.save` interaction samples in `window.__INTEGRAL_PERFORMANCE__.snapshot()`;
these include awaited related-record saves. Do not infer browser performance
from an operation receipt or a fake-context test.

Ordinary-save target is p95 below one second on populated fixtures, including
invoice header and lines. Bulk payroll generation is a separate workload. Test
worker-running, stopped, backlogged and slow-consumer cases against disposable
data and read back required business state. Keep performance acceptance separate
from functional test results; pending qualification is not a passing SLA.

For rollout, build matching API/worker artifacts, create indexes before enabling
traffic, start the worker, verify heartbeat/backlog and then compare save latency
and authorized event visibility. Retain old pending rows. Do not reverse to old
writers while a new worker is delivering events; coordinate rollback of both
processes and keep durable facts for recovery.

## Related-record editor compatibility

The optional `persist_line_id_field` setting sends existing row IDs under the
declared line-input field. Its neutral default omits IDs so existing third-party
tool schemas retain their original inputs. Finance opts into `entry_id`; owning
operations must validate parent ownership before mutating any referenced row.
Successful `line_ids` results refresh draft row identities for subsequent saves.

## Exact App field projections

`ToolContext.find_entry_values(query, fields)` returns canonical record IDs
and exact selected custom-field values under current workspace/entry access.
An App-bound `OperationContext` further restricts the audience to that App.
Workspace save-hook contexts do not carry an `app_id`; they use the same
current ordinary authority and workspace boundaries as `find_entries`.
It avoids full entry hydration and value truncation; up to twenty public field
keys are accepted, with `id` reserved for canonical identity. Candidate reads
advance in bounded authorized windows, but the result is exhaustive rather than
a paged API. Use `read_entry_records` for bounded App pages. Finance uses the
optional projection capability for number scans and retains a scoped-read
fallback when running against an older context.
