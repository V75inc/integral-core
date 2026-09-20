# Module seam qualification

**Status:** WP-01 in progress, 2026-09-20.

## First seam: identity and policy

`ExecutionScope` is the transport-independent, immutable binding of a
principal to a workspace for an effect or governed read. It rejects missing
principal or workspace identity at construction. Transport adapters remain
responsible for authenticating the principal and resolving the workspace; the
module contract prevents downstream code from silently substituting either.

`app.modules.policy.PolicyModule` is the first public modular-monolith
adapter. It owns the module-facing authorization call and delegates to the
existing policy engine, preserving the current policy rules during migration.
Typed App operation and declared-query dispatch now construct
`ExecutionScope` before any app lookup or effect and authorize through this
module seam.

Every declared App query and operation result now carries a
`policy_revision` fingerprint. It represents the evaluated principal,
workspace, action, resource and policy decision at the result boundary. The
legacy engine still resolves policy afresh for every request (its memoization
is request-local), so callers must re-authorize before every direct read,
query, queued effect or extension call. The fingerprint gives result caches,
receipts and consumers an explicit key for comparing that evaluated state; it
does not create a cross-request authorization cache or substitute for the
effect-boundary check.

## Deliberate limits

This is not a directory migration or a second policy engine. HTTP, MCP and
resident entry points still resolve scope through their existing adapters.
The next WP-01 slices must route those adapters through the same contract,
define structured module errors, add a finite import-boundary allowlist, and
prove normal Core use with no model provider available.

## Import boundary gate

`.ci/module_boundary_check.sh` prevents new module adapters from importing the
HTTP, agentive, model, profile, plugin, or unapproved service layers. The
current policy adapter has one explicit legacy-service exception:
`app.services.policy_engine`. Each further exception must be named in the
gate, making transitional coupling visible and finite rather than normalizing
it across future modules.

## Optional intelligence composition

The resident harness is now an optional boot component. A missing harness
configuration or bootstrap failure records availability in the intelligence
module and does not prevent the database-backed Core from starting. Readiness
continues to represent database availability and reports intelligence status as
an additional field; it does not turn an unavailable model provider into a
false infrastructure outage.

## Evidence

- `backend/tests/contracts/test_execution_scope.py` proves normalization and
  rejection of incomplete identity.
- `backend/tests/contracts/test_policy_module.py` proves the module delegates
  the scope principal and policy request unchanged, and gives different
  fingerprints to different effective decisions.
- `backend/tests/contracts/test_app_query_policy_revision.py` and
  `backend/tests/contract/test_app_operations_hello.py` prove that query and
  operation envelopes expose that fingerprint.
- `backend/tests/test_optional_intelligence_boot.py` proves both that a
  resident-harness bootstrap failure does not abort Core startup and that
  `/health/ready` remains ready while accurately reporting that unavailable
  intelligence state.
- Existing typed App operation and query contracts preserve transport behavior.
