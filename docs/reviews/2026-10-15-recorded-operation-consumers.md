# Recorded operation HTTP and view consumers

Status: source-qualified HTTP/SDK/view increment for PR #136; no deployment
or complete-goal claim. Based on the committed `edd7817d` internal coordinator.
The next approval-contract and intent work remains isolated.

## HTTP boundary

The authenticated extension operation endpoint derives its Subject from its
server-resolved ExecutionScope, then looks for original completion when an exact
Idempotency-Key is supplied and a native journal is available. This happens
before loading a capability snapshot or invoking the broker, because the current
App declaration may legitimately be absent after original completion.

Known completion returns either currently admitted saved output or an opaque
withheld DTO. Journal absence retains the entire ordinary current broker
admission path; it never grants execution. Journal conflict, incompleteness,
transaction unavailability and unexpected storage faults never fall through.
Stores without native mutation journals retain their existing read compatibility
path; native errors are never treated as unsupported storage or absent outcomes.

HTTP model input rejects caller-supplied root subject/workspace overrides. This
binding supplements current identity/workspace/history admission; it is not a
new authentication mechanism. Native endpoint tests exercise real primary
identity and workspace facts after trusted request-state admission, not the JWT
middleware or an expired transport credential. Credential and ASGI transport
qualification remain independent requirements.

## View and SDK contract

The TypeScript API and public Python SDK expose available versus withheld HTTP
responses. Missing available discriminators remain compatible with older
servers. Output consumers must not substitute an empty object for withholding.
The generic output accessor raises a distinct non-retryable recorded-result
condition; native save contributions retain that condition for neutral UI copy.
Kanban action completion uses an informational message, not a fresh success
claim. The extension bridge preserves the opaque withheld value without retrying it,
but marks its existing v1 `ok` field false with a distinct nonretryable status.
Existing clients often use that flag as operation success; they must not
interpret a withheld value as an empty successful business result.

A tool-backed related-line save now leaves the entire mutation to its declared
operation. Client-side preliminary deletes could run before receipt recovery,
repeating effects even when the tool itself correctly observed an original
completion. Tool handlers must own replacement/reconciliation of their rows;
packages that depended on implicit client cleanup need explicit qualification
of that contract. The separate entries-api persistence path is not relabeled
transactional or recoverable by this change. This is a generic lifecycle rule,
not a custom App handler or field mapping.

The SDK additions describe only HTTP/extension response values. They do not
silently change the handler-injected facade invoke protocol. Other producers
must establish their own transport/run binding and invoke recovery before an
effect transaction; this candidate does not close broker, MCP, resident or
approved-worker reconciliation, or the remaining executor family inventory.

## Qualification

Before final source formatting, 13 HTTP/scope unit cases and 12 HTTP/SDK cases
passed (overlapping sets). Three native endpoint cases passed for readable
original output, declaration removal, and a refused foreign workspace hint.
The original receipt and single handler effect remain unchanged.

Focused frontend: four files, 19 cases passed, including withheld bridge
forwarding and tool persistence without preliminary deletion or automatic retry.
TypeScript passed after extending the form's existing toast callback contract
with the existing generic informational variant. Pinned format hooks adjusted
three new test files; all remaining hook checks passed. Final combined HTTP/scope/SDK contracts passed 16 cases. Pinned hooks and staged
architecture guards passed after formatting. The full frontend passed 1,824 cases
across 321 files, and TypeScript plus the production build passed. The public SDK
wheel built and independently imported its response types and typed marker
without importing Core modules. Relevant evidence is retained under
`/tmp/core-recorded-consumer-*` and `/tmp/core-recorded-consumers-sdk-artifact-proof.json`.

The source-qualified internal coordinator is now committed as `edd7817d`.
The preliminary consumer delta required reconciliation onto that exact source
and full default backend/CI-faithful qualification before commit or push. The
final combined record below completes those gates.

No browser or live deployment acceptance is claimed. No runtime data, credentials,
Business source, deployment stack or existing PR merge was changed.

## Bridge release-boundary follow-through

Read-only inspection of the existing reference and external App clients confirmed
that some use the v1 `ok` flag as success before inspecting output. The candidate
now sends withheld completion with `ok: false`, the distinct
`operation_output_withheld` code, `retryable: false` and the original opaque DTO.
The generic Core host shows informational copy; no App-specific handler or UI
was imported into Core.

The existing bridge also posted delayed replies to whichever iframe was current
after an await. Replies now retain the original frame and scope/token generation.
Incoming messages require a frame actually attached under that generation; an
old token is not relabeled for a different App/workspace/view. Scope changes or
frame closure refuse pending host draft waiters and do not cancel/replay an
admitted backend effect. Optional package presentation metadata does not change
the authorization scope; a context-only refresh keeps the existing frame.

Twenty-four focused tests passed after adding operation/query/capability delay,
stale token and pending-submission closure cases. Full frontend, production
build and lint are rerunning on these revised UI sources. The patched MCP
backend/dependency source hashes remain unchanged; their running full/CI/native
suites are qualified separately from this UI revision.

Remaining lifecycle limitation: the older optional draft protocol still treats
a live frame that ignores validate/submit messages as nonparticipating after
its timeout. Explicit capability negotiation and unknown submission outcomes
need separate qualification; this change does not claim that a silent frame
confirmed related-data persistence. No browser or deployment acceptance is claimed.

## Final combined qualification

The reconciled source passed 6,527 default backend cases with 781 skips and
1,778 CI-faithful smoke cases with 22 skips; both had zero failures/errors.
The final frontend passed 1,829 cases across 321 files, production build and
TypeScript. Final bridge scope publication occurs at commit rather than render,
so an abandoned React render cannot relabel an active frame. Current App/token/
workspace/view identity and the actual frame binding still gate every reply.

The affected operation PostgreSQL selection passed 106 cases on unchanged
operation source; all 46 updated graph cases passed separately on PostgreSQL.
Full native, live-browser and all optional journey acceptance are not implied.
Pinned hooks, staged architecture guards and all four independent artifact gates
passed on the reconciled source. Exact evidence is recorded in the companion
MCP remediation report and `/tmp/core-consumers-final-*` manifests/logs.

An earlier full run failed one timing-sensitive JSON graph semantic test. That
failure is retained; the final rerun follows the explicit deadline classification
regressions and the complete SQLite fixture for cross-workspace semantics. The
original failure did not retain root stop metadata, so its precise cause is not
relabeled as proven. No failing gate or advisory exception was bypassed.
