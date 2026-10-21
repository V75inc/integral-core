# The Integral App extension contract

This contract defines how domain Apps use Core. The versioned filename denotes the extension boundary; it does not mean App authoring YAML is version 1. Current authoring YAML is version 3 and runtime manifests are schema version 2.

## Core responsibilities

Core owns authenticated identity, workspace scope, resource access, schema validation, graph integrity, generic lifecycle, execution controls, transcripts, and receipts. Apps supply domain meaning through declarations and reviewed implementations. Pricing, checkout, hosting, and commercial account rules remain in the host or App.

Core must not import `app.packages` or `app.plugins` from services, APIs, models, or schemas. It must not branch on App slug, EntryType name, or Track identity. Core-only boot must work without domain packages.

## Commercial hosts

The [commercial host integration guide](commercial-host-integration.md) defines extension loading, workspace entitlements, temporary host access, quota and usage hooks, and access-loss recovery. Host registrations are trusted process configuration, separate from App manifest hooks and resource authorization.

## Information and capabilities

The preferred facade supplies `get(object_ref)`, `query(query_spec)`, and `invoke(operation_key, payload)`. Query modes are `declared_capability` and `core_open`; mode selects a contract, never a permission bypass. Field and revision contracts distinguish base values, custom fields, relation definitions, and record/schema revisions.

### Recorded HTTP operation completion

For `POST /extensions/{app_id}/operations/{operation_key}`, retain one
`Idempotency-Key` and the exact input across retries of the same intent. A new
intent needs a different key. Never generate a fresh key merely because a
response was interrupted.

On a native journal store, Core checks for original completion before current
capability execution admission. A known completion never invokes the handler
again. Missing journal evidence is not execution authority: ordinary current
authorization still governs fresh work. Incomplete or conflicting receipts and
storage outages remain typed failures, not an absent operation.

An available response has `output_disposition: available` and the ordinary
`output`, references and evidence, subject to current function and result access.
Older servers may omit that discriminator. A withheld response instead contains
only `output_disposition: withheld`, `current_state_verified: false`, and the
original `operation_receipt` (`id`, `status: succeeded`, `replayed: true`). It
proves past completion, not current domain state, current execution permission,
or a verified business outcome. Do not substitute empty output, display a fresh
success claim, request another approval, or retry the effect with a new key.
Show a neutral recorded-completion message and use ordinary authorized reads to
refresh current state. Withheld completion never discloses the original
principal, private key, saved titles, output or current denial reason.

For extension bridge v1, withheld output uses `operation.result` with
`ok: false`, `error_code: operation_output_withheld`, `retryable: false`, a
neutral explanation, and the opaque completion DTO in `value`. This prevents
older clients from treating the `ok` flag as a fresh business success. It does
not mean that the original operation failed. Do not retry the effect with a new
key. New clients may inspect the typed status value; HTTP retains the explicit
available/withheld response union. The Core host also presents a neutral status.

Asynchronous bridge replies stay bound to the receiving sandbox, App, workspace,
view and handshake token. A scope switch cannot redirect a delayed reply to a
replacement frame. Reusing an old token while changing its owning scope does
not reauthorize the old frame. Pending host form requests are refused when the
frame closes or changes; an interrupted waiter is not a successful submission.
This does not cancel or replay a server operation that was already admitted.

The Python SDK exports `OperationResponse`, `AvailableOperationResponse` and
`WithheldOperationResponse` for the response value. These types do not alter
the handler-injected `ToolContextV2.invoke` contract or grant access. Other
transport and approved-worker recovery integration is qualified separately.

### Retained tool source observations

On distributions supporting this contract, a successful native connector read
that returns bounded HTTP source text includes `_observation_ref`. Pass that
server-generated dictionary to `ctx.get(ref)` to obtain the authenticated
`source` projection: exact `text`, `url`, `retrieved_at`, `truncated`, optional
title/type metadata, and `content_trust: untrusted`. The result also includes the
server receipt identity and output fingerprint. A verified retrieval is not
proof of publisher accuracy, customer demand or any domain outcome.

Core requires the original actor, current workspace access, private thread/session
containment and current authority on the original connector row. A shared App
does not grant access to another actor's tool transcript. Revoked, missing,
unbound legacy, corrupted, incomplete or oversized observations return `None`;
the operation must report unavailable evidence rather than trust a model copy.
Lookup never refetches or reexecutes a tool. Apps own quote/claim validation,
interpretation and approved persistence through their declared operations.

This is an input/read reference, not an owning-App mutation `object_refs` value.
Keep it as structured machine data. See the [implementation and invariant
review](../backend/tool-source-observations.md). Older distributions may
return `None` for this kind; package adoption requires qualification against a
Core release carrying the contract.

### Background App jobs

The [how-to guide](background-app-jobs.md) walks through an example; this section is the reference.

A tool can hand long work to Core with `ctx.enqueue_job(tool_key, payload,
idempotency_key=..., label=...)`. Core records a durable work item (kind
`app_tool`) and runs that tool later on a dedicated App job worker, as the same
person in the same workspace. The job survives restarts, is retried when the
tool returns `{ok: false, retry: true}`, and fails with the tool's `reason`
otherwise. An App may queue only its own tools; repeating an idempotency key
returns the existing job. Inside the job, `ctx.job_id` is set and
`ctx.report_progress(done, total, label)` records progress; `ctx.job_status(id)`
reads a job the App queued. Work item status and the background tasks list
show the App's label and reported progress.

Enqueue and each execution attempt require current workspace access; privileged
tools additionally require owner or admin authority. Job status is visible only
to its initiator or a workspace owner/admin who retains workspace access, and
only through the owning App's workspace context. Unauthorized status reads
return `None`.

Jobs keep their steps short and resumable: save progress on the record, make
every step safe to repeat, and treat a job as one worker's sequential run, not a
transaction. A tool may update its own App's records in the workspace where the
App is installed; the workspace comes from Core, never from the tool.

OperationContext is injected by Core. Its identity includes principal, workspace, scope, bundle slug, installed App, operation key, and optional idempotency/correlation keys. It offers validated create/update helpers, conditional updates, audit emission, deduplicated notifications, and the preferred facade. Raw private Core imports are forbidden.

## Declaration requirements

Packages declare input/output schemas, operation policy and staging behavior, query shape, tools, hooks, skills, and presentation assets. Python implementations require their trust tier and signature posture. An installed App resolves the immutable active ApplicationDefinition before using its effective capability contract.

A tool may declare `schedule` as a cron string or `{cron, timezone}`. Core
queues it as a background App job once per due slot, as the App's installing
person; slots missed while the service is down are skipped.

The frozen hook catalog contains `entry.transform`, `entry.public_share`, `entry.precompute`, `entry.create`, `entry.validate`, `entry.update`, `connector.dedup`, `connector.auto_link`, and `email.sent`. A new point requires a reviewed decision and invariant update. Registration collisions fail rather than silently replacing another package's tool.

## Presentation

App-owned declarative composites use registered Core primitives. Sandboxed iframe extension views use declared assets, hosted paths, and the view handshake; a view's `config.min_height` lets a compact view size below the default frame height. This is an implemented extension surface; arbitrary remote React plugins or inline executable manifest code are not substitutes.

Frame responses permit embedding by the API origin (`'self'`) and the explicit
HTTP(S) origins configured in `FRONTEND_ORIGIN` and `APP_BASE_URL`. Set the latter
to the public SPA URL when hosting the API and UI separately. Paths are removed,
default ports and host names are normalized, and duplicate origins are omitted.
Malformed origins, credentials, wildcards and header delimiters are ignored;
an invalid setting cannot remove the remaining valid origins or crash rendering.
Use ASCII (punycode for international domains) DNS host names. For a separate
IPv6 UI, use a DNS host name: CSP host-source syntax does not
support IPv6 literals. An invalid configuration alone leaves only `'self'`.
These parent origins do not grant App access: frame navigation still verifies
the scoped grant and current workspace/App access; scripts remain hashed and
network access remains disabled inside the sandbox.

Package App Home uses declared queries/actions under the active definition. Dashboards are instance projections. Unavailable data must remain distinguishable from empty results; action preparation does not silently submit a chat turn.

## Evidence

Use `examples/reference-hello-app/` and `backend/tests/contract/` for executable examples. Test Core-only isolation, input validation, current permissions, stale revisions, capability withdrawal, signature rejection, and browser readback. The [governance guide](extension-contract-governance.md) explains contract changes.


## Agent invocation review policy

An operation defaults to `agent_invocation: staged`. Agent mutations then require
human approval before dispatch. A package author may explicitly opt a specific
operation into `agent_invocation: direct`; this allows immediate invocation by an
agent under its current human principal. Reads remain immediate. Unsupported
values are rejected at compilation.

Direct invocation retains the canonical dispatcher, current workspace/App grants,
entitlements, policy decisions (including any independent approval requirement),
validated and resolved input, declaration fingerprint and durable idempotency.
Changing the declaration between preparation and dispatch rejects the call.
This flag grants no permission or autonomous-work admission. Installing an App
with direct operations accepts unattended agent writes for those operations;
operation discovery exposes the effective `agent_invocation` value.

## App-owned computed relation updates

The owning App mutation facade may maintain readonly relation fields after active-App ownership, workspace, principal, policy, revision and protected-state admission. Readonly continues to block direct record edits. A changed relation target still requires current read access and canonical declared containment; the unchanged-legacy-reference exception does not authorize new bindings.
