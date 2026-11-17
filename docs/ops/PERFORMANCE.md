# Performance diagnostics and relation pickers

Finance is the initial consumer; Core owns generic tracing and relation reads.
The approved investigation covers forms and resident chat, starting locally.
No model, database, dependency pin, or deployment should be replaced on the
strength of a slow spinner alone.

## Runtime fingerprint and local tracing

Plain dashboard count cards use the shared entry-query planner's `count_only`
execution hint. Canonical workspace, App lifecycle, generic-read boundaries and
filter normalization run before the existing permission-aware count executor.
Native aggregates recheck current authority; unsupported audiences retain bounded
admitted-record scans. No authority result is shared between widgets. This
removes page selection, sorting and display-row hydration from unfiltered counts.
Status, tag, type, date, keyword, profile-filter and pinned-result-set queries
keep their canonical evaluator; the hint does not promise empty `entries` for
those fallback plans. Grouped charts and aggregate calculations keep their
existing semantics.

A scalar count measures admitted entities, not renderable display payloads.
An authorized record with an invalid display field can still contribute to its
count without constructing an Entry for display. Conflicting containment cannot
contribute an entity; unavailable authority still refuses the read. Outages and incomplete
sources must never become a successful zero or partial total. Query-budget
regressions cover count-only and mixed-widget dashboards. Their server timings
do not establish browser loading or deployed latency: retain separate waterfall,
rendering, host-load and exact installed-artifact evidence before claiming a
speedup.

Exact App/Track activity summaries discover target candidates before inventory
admission. App child discovery pages to exhaustion; canonical workspace/App
containment is checked before candidates enter the digest. The same Track
inventory listability rules decide which parent metadata is readable. Scoped,
currently admitted direct Entry grants can add otherwise unreadable parents,
whose titles/actions remain masked. Inaccessible children without admitted
leaves do not affect summary counts or refusal output. Counts and recent reads
remain independently admitted; no widget or request authority cache is added.
User-wide/name-based discovery and the legacy unresolved-App fallback retain
their previous behavior. Candidate selection is never a read grant.

Track-list default metadata uses a bounded projection of named model attachments
for the admitted page. A complete projection can establish that a model has at
most one attachment without a separate count per Track. Possible shared models,
overflow and malformed projection rows retain exact distinct-neighbour checks.
Shared defaults still resolve from each Track's own saved Views. These facts are
used only during that page's read projection; mutation checks remain fresh, and
no permission decision or cross-request sharing cache is introduced.

Run `backend/scripts/performance_snapshot.py` with the actual API interpreter
(or stream it into `docker exec -i <api> python -`). Capture Core/Business/library
commits, mounts and frontend source alongside its allowlisted output. Repeat
`--repository /absolute/checkout` and `--artifact /absolute/reviewed-wheel` to
record commits, dirty tracked-diff hashes and selected build hashes. The script
never dumps environment variables or connection strings. Distribution metadata
and source imports are separate evidence: mounted source can differ from the
installed wheel version.

The initial 2026-11-05 inspection found mounted Core backend/frontend source,
Core distribution rc38, and installed jvspatial 0.2.2, while the checkout pins
jvspatial 0.2.4. Existing tracing flags were unset. This establishes artifact
drift, not a measured explanation of Finance latency. The initial temporary
snapshot was lost when the environment restarted. Recapture the deployed runtime
before rollout; no deployed-versus-candidate latency claim is made here. Keep
subsequent local evidence in ignored `backend/.pytest_cache/performance/`, rather
than `/tmp`, and explicitly archive it when distributing a qualification.

A subsequent read-only inspection of the stopped Business API image found Core
rc45, jvspatial 0.2.4 and SDK 0.2.0. That image is a different artifact from the
initial observation and is not evidence of which code served an earlier request.
The live API was not restarted for this investigation.

For a diagnostic candidate, install Core's `observability` extra, set
`INTEGRAL_PERF_TRACE=1`, and optionally set `INTEGRAL_PERF_HEADER_ENABLED=1`.
`INTEGRAL_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces` enables bounded batch
export. The optional `docker-compose.performance.yml` starts a local Jaeger
viewer on port 16686 ([upstream setup](https://www.jaegertracing.io/docs/2.22/getting-started/)); it is an independent opt-in service, never started by API
boot. Use the service name rather than localhost when exporting across containers.
Keep `JVSPATIAL_OBSERVABILITY_ENABLED=1` for adapter metrics.

Build the browser with `VITE_PERF_TRACE=1` for a bounded 200-sample, content-free
ring accessible as `window.__INTEGRAL_PERFORMANCE__.snapshot()`. Samples include
HTTP response, normalization and picker usable-options durations. Browser trace
IDs correlate with server `X-Trace-ID`; no analytics endpoint receives them.

`perf.request` and `perf.span` log JSON in the message as well as structured
LogRecord extras, so existing text formatters retain the data. Request records
include route templates, outcomes, time to headers, full stream lifetime,
service phases, invocation counts and available DB counters. Nested phases aggregate in the request summary to avoid one log per permission call; standalone phases still emit `perf.span`. `Server-Timing: app` is time to response
start, not stream completion. Nested phase and concurrent operation totals must
not be added together as elapsed time. Unmatched routes use a fixed label.

Only W3C trace context is propagated; credentials, request/response bodies,
search strings, tool inputs/results, customer names and model messages are not
captured. Export queues are bounded and exporter setup is optional. SDK export
failures must not affect application work. Core uses the supported OpenTelemetry
SDK/API boundaries; see [the upstream model](https://opentelemetry.io/docs/languages/python/instrumentation/).

Existing chat preparation/discovery phases and milestones now produce spans.
Physical model spans use the observed dispatch/completion interval and expose attempt/outcome/role, not provider messages. Registered tool names identify tool spans without arguments/results. `firstDomainResultMs` marks completion of the first authorized
App or connector read; generic Core discovery does not satisfy it. The durable
worker includes queue time in this milestone. Tool spans connect DB work. Durable chat acceptance stores
only active server `performance_traceparent` in WorkItem input, separate from
request fingerprints and encrypted input capsules. Workers restore it solely as
observability context; it never supplies identity, policy, scope or replay authority.

## Compact relation-options interface

`GET /api/relation-options` takes `track_id`, `entry_type_id`, `field_key`, optional
`entry_id`, literal search `q` (200 characters), opaque `cursor`, and `limit` (1–50,
default 50). Explicit workspace scope follows the normal authenticated perimeter.
The response is `{options: [{id, label, track_id, track_title}], next_cursor,
has_more}`. No exact count or full-entry enrichment is performed.
An independently granted Entry does not expose its private parent Track title;
parent metadata requires its own current read decision.

The server verifies canonical source scope and read permission, confirms that the
EntryType belongs to the source's attached model, and derives target restrictions
from that saved field. Source checks reuse the supported computation-local fact scope only while resolving the source and schema; the scope ends before candidate-record admission. Target tracks remain within the declared App/workspace;
foreign Apps require an explicit resolvable declaration and active lifecycle.
Current canonical Entry admission precedes page filling; cross-App custom labels
use the existing restricted-label boundary. Filters precede pagination, and
continuations repeat current authorization. More than 1,000 candidate Tracks or
model EntryTypes fails explicitly instead of presenting a truncated complete pool.

Expanded forms, form regions and editable tables use the same independent
remote picker. Queries load on opening, debounce search by 250 ms, and use
React Query for cancellation/sharing. Keys include principal, workspace, source,
schema, field, source entry and query. Each picker retains at most 20 pages;
selected values resolve separately when outside that window. Mutations and
applied ChangeEvents invalidate choices; identity/workspace switches clear caches.
Failures show Retry per picker instead of silently becoming empty results.
The opening/search debounce coalesces immediate typing, and a one-second fresh
window shares results between sibling pickers. Creation controls fetch only the
configured operation descriptor when opened, through the existing operations
endpoint's optional `operation_key` filter. Its 30-second display cache is scoped
by principal/workspace/App/operation and invalidated on changes; invocation still
checks current authority. Filtered metadata does not discover unrelated workspace
capabilities.
Successful extension-operation responses include optional server-derived
`operation_class: read|execute`. The client uses it only to avoid invalidating
choices after reads such as document-number previews. Missing metadata and
mutation responses still invalidate conservatively; it is never a write permit
or a current-state claim. Python/UI SDK response types expose the optional hint.
The optional source entry is supplied only for a persisted entry on the source
Track; tool-bound forms do not pass an unrelated upload/host entry as authority.

Expanded-form tasks, sprints and manager fields retain their pre-existing
specialized dependent loaders. Track-anchor and public/member form APIs retain
their separate authority contracts. These are explicit migration boundaries;
this change does not assert that every legacy relation loader has been replaced.
Customer creation and label-copy behavior continue through existing field controls.

## Measured repeated discovery

The real Chromium journey exposed two additional costs that the isolated picker
endpoint did not reveal. Customer-creation controls replaced the remote search
callback; they now compose both callbacks. Generic POST invalidation also treated
read-only `preview_document_number` completions as writes, repeatedly cancelling
and restarting picker requests. The server-derived response hint above fixes
that classification without trusting a client-selected operation name.

A diagnostic CPU profile showed repeated YAML parsing and definition checks
during metadata reads. Core's capability snapshot read 2,800 lines of its own
tool manifest just to obtain its version. That source-only version is now reused
under a bounded cache keyed by path, inode, size, mtime and ctime; the existing
dispatchable catalogue builder and all live caller authorization remain in place.
This is Core source metadata, not custom model/harness execution behavior.
Operation-list reads reuse verified definition facts only inside the existing
computation scope. Subsequent lists recheck grants; invocation still obtains its
normal live permit. Discovery, definition verification and actual extension
handlers also have named spans so the remaining cost stays visible.

These changes preserve the root/typed-entry graph boundary, server scope,
ordinary resource/action authority, revision/approval checks and idempotency.
Diagnostic context and metadata never grant access or prove a business outcome.

## Scoped Entry and App discovery

An exact `n.Track.*` selector narrows candidate discovery before entry reads or
grouped charts. It does not grant Track or Entry access. Canonical containment,
workspace scope, App lifecycle/query boundaries and current per-Entry admission
still apply. A direct Entry grant beneath an unreadable Track remains usable;
Track labels and recovery hints require separate current Track access. Names
and unscoped queries retain their existing discovery and matching behavior.

Scoped App listings resolve the requested workspace before candidate discovery
and request App inventory only. Final canonical admission, active lifecycle,
ordering, cursor pagination and exact totals remain in the listing path. These
changes introduce no caller-authority cache or retained cross-widget grants.
Each widget performs its own live reads, including after another worker revokes
access. Unfiltered count widgets ask the shared query executor for an exact
total with a one-row `created_at` window, avoiding the two `updated_at`
fallback partitions; typed profile filters retain their existing canonical
evaluation. Refused, failed or unavailable totals remain visible errors with
the original refusal/error code, rather than zero or a partial aggregate.

Run `tests/test_scoped_query_budget.py` against disposable PostgreSQL to compare
authenticated ten-widget dashboard and App-list HTTP requests before and after
adding 1,000 unrelated rooted Tracks with attached Operational Models. Record the imported source paths, logical adapter operations, driver statements,
pool wait, summed database duration and elapsed request duration. Summed
concurrent durations are not elapsed time. This fixture measures the HTTP serving
path; browser and deployed-product acceptance remain separate gates. Larger
filtered `query_all_entries` scans still use the existing offset-page contract;
this change does not claim a shared cross-page snapshot or authorization cache.

## Reproducible qualification

Use a disposable PostgreSQL service, never a production database. Core's test
bootstrap drops/recreates its `integral_test` database on the supplied service.
Select candidate jvspatial source explicitly with PYTHONPATH or install a reviewed
wheel into an isolated environment; record imported paths and distribution metadata.
A sibling checkout is not evidence that the running API consumes a foundation fix.

Run `tests/test_relation_options.py`, `tests/test_performance_tracing.py` and
`tests/test_perf_query_budget.py`. Opt-in `tests/test_relation_options_scale.py`
uses 100/1,000/10,000 rooted synthetic records and compares legacy full listings (the existing endpoint caps its requested 200 at 100)
with compact reads, at concurrency 1/10 and tracing off/on. Set
`INTEGRAL_TEST_DB=postgres`, `INTEGRAL_TEST_POSTGRES_DSN` to the disposable service,
`INTEGRAL_RUN_SLOW_TESTS=1`, and select `-m slow`. `PERF_SAMPLES` defaults to 20;
`PERF_REPORT` selects the JSON output directory. The synthetic benchmark bypasses only the per-minute throughput limiter inside its fixture; live limits and authorization are unchanged. It refreshes PostgreSQL planner statistics after seeding. Failures fail the run; they are
never counted as fast successful samples. Reports include payload size, logical
operation count and individual timings. First-request samples are not proof of
cold database caches or a cold container. Browser timing must additionally include
debounce and rendering before claiming the 500 ms warm/1.5 s cold p95 UI targets.

Business's `test_finance_relation_options.py` verifies the actual installed
Finance schema and create-customer flow with Core's PostgreSQL contract runner.
Use its existing Finance transactional scenarios to verify mutation/readback and
approval behavior. No benchmark may bypass approval to create an invoice through
resident chat. Live provider timing needs a configured synthetic workspace and
stable model configuration; deterministic harness tests validate mechanics only.

For slow query shapes, collect `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on the
same disposable dataset. Distinguish returned rows, adapter candidates and actual
server scan work. Logical adapter counts and driver statements are different
metrics; missing driver metrics on an older selected artifact remain unavailable.

## Foundation decision

Retain jvspatial provisionally. Confirmed request fan-out, shared loading barriers
and unnecessary enrichment justify targeted fixes independently of persistence.
There is currently no qualified evidence that a wholesale replacement would solve
the remaining critical path. If aligned, optimized traces still show persistence
as the reason UI targets fail, compare an equivalent parameterized PostgreSQL
read behind Core's service boundary with identical admission, data, projection,
pagination and hardware. Record performance plus compatibility costs before
choosing retention, a narrow read path, or a separately planned replacement.
The comparison must not remove authorization/containment work from only one side.

## Local candidate evidence and remaining gates

The 2026-11-06 synthetic PostgreSQL run selected Core rc45 source, the built
jvspatial 0.2.4 wheel and SDK 0.2.0 wheel explicitly. It used 20 successful warm
requests per cell, 100/1,000/10,000 rooted records and concurrency 1/10. The legacy
and compact endpoints ran against the **same candidate**, not different deployed
versions. On the 10,000-record fixture, with diagnostic tracing disabled:

| Request | Logical operations | Response bytes | p95, concurrency 1 | p95, concurrency 10 |
| --- | ---: | ---: | ---: | ---: |
| Full entry list, capped at 100 | 131 | 84,004 | 282 ms | 1,412 ms |
| Compact options, page of 50 | 32 | 7,968 | 66 ms | 276 ms |

The separate equivalent candidate-reader comparison measured jvspatial p95
55/241 ms and parameterized SQL 62/292 ms at concurrency 1/10. The direct SQL
probe retained Core admission and hydration and produced identical options.
This gives no evidence for replacing the foundation. Raw local JSON and planner
statistics live under `.pytest_cache/performance/scale-final/`; execution-plan expression
text is removed because PostgreSQL can embed parameter values in it. The shared
development host and short runs limit precision; rerun on deployment-equivalent
hardware before setting a release budget.

Full diagnostic recording exceeded the 5% overhead target. Keep it opt-in for
investigations. The supported SDK `OTEL_TRACES_SAMPLER` and
`OTEL_TRACES_SAMPLER_ARG` controls can bound routine sampling; browser diagnostic
interactions request sampled traces. Sampling does not by itself establish the
overhead target. Browser usable-options SLOs and live-provider Finance chat
budgets require separate qualification; API timings exclude debounce/rendering,
and deterministic harness tests cannot establish provider latency.

### Finance browser results: correctness passes, latency gates remain open

The selected candidate frontend and Core source at `180ad347` were exercised
against 1,000 synthetic Finance customers in Chromium 156. The run verified 40
customer selections, with 20 observations per cell and no workflow failures or
full-entry-list requests. Browser event-to-render timing includes debounce; Node
automation time is reported separately. Search uses a fresh query for each phase.

| Session | Concurrency | Open p95 | Search p95 |
| --- | ---: | ---: | ---: |
| Cold context | 1 | 1,912 ms | 689 ms |
| Cold context | 10 | 17,782 ms | 8,434 ms |
| Warm picker | 1 | 355 ms | 400 ms |
| Warm picker | 10 | 3,569 ms | 4,898 ms |

Only the warm concurrency-one cells meet their 500 ms target. These results do
not qualify the overall cold or concurrent journey. The separate Finance test
passed customer/account/item selection, invoice creation and persisted draft
readback. Browser coverage remains customer-only. The parent-title permission
hardening was added after this browser run and has a dedicated regression test;
these timings must not be represented as a final release qualification.

Diagnostic traces and CPU samples identify repeated permission/definition reads,
JSON hydration and metadata discovery as remaining contributors. Under concurrent
startup, requests outside the options endpoint also compete for the event loop.
The evidence does not yet isolate a safe next optimization that closes the full
gap. Live-provider chat budgets, complete integration gates and the under-5%
instrumentation-overhead target remain outstanding. Keep diagnostic export
optional and retain jvspatial while resolving these measured gaps.

## Frontend bootstrap and App read scheduling

Session boot and capability consumers share one public deployment-metadata read,
outside authenticated renewal. A failed read can retry when a capability consumer
remounts. Identical API reads stay shared while pending, even beyond their reuse
window; freshness starts on completion. Both the API wrapper and transport layers
observe credential/workspace boundaries. Scope changes and credential setters
invalidate pending/recent sharing synchronously, and wrapper calls also detect
credential changes made by another tab. Late responses cannot restore invalidated
cache entries. Writes and live graph-change invalidation remain in force.

Reasserting a confirmed or pending workspace does not send another scope PUT;
failed writes remain retryable, and account changes discard confirmation. This
avoids unnecessary write-triggered cache invalidation during App/Track navigation.

App content reads proceed independently of the collaborator directory. Editing
waits for current access details; transient failures leave readable content with
a separate retry. Explicit authentication, authorization or missing-resource
responses retire content, breadcrumbs, workspace publication and chat context,
including during a failed retry or late primary response. App/account navigation
also fences late responses. The backend continues to authorize each read/write.

Track-link pickers fetch only when opened. Dashboard widget choices fetch only
while editing, with loading/error/retry states. Initial dashboard data selects the
default directly and retains that selection across background default changes.
These request-budget and authority properties have regression coverage. Validate
installed browser request counts and user-visible timings separately: a source
build or fixture result does not establish a deployed latency improvement.

Event-history catch-up coalesces up to ten pages into one reconciliation, rather
than refetching dashboards and lists after each page. An older in-flight read
still receives one trailing refresh. Completed pages reconcile even if a later
page fails; hidden tabs defer the batch. Commit the protected history cursor
only after reconciliation, share that completion through credential renewal,
and discard pending replay when its principal/workspace subscription changes.
Measure both initial rendering and the first safety-poll window: a fast initial
render can conceal repeated aggregate reads several seconds later.
