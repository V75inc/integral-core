# WP-06.1 — Brokered capability tools for Pydantic AI

**Status:** implemented; awaiting integration review

**Objective:** expose Integral's declarative tool catalogue through Pydantic AI without transferring authority from the existing Core capability broker.

**Owned source:** `backend/app/agentive/harness/broker_tools.py`; tests: `backend/tests/native_harness/wp_06/test_broker_tools.py`.

**Contract:** the model sees only the provided catalogue names, descriptions and JSON Schemas. Tool closures hold an immutable execution scope and dispatch every call through `invoke_declared_capability` with the durable run, principal, workspace, session, source, operation class, and a deterministic run/tool-call idempotency key. Return `CapabilityResult.for_model()` so receipts and denials stay visible to the model. Re-check authorization and policy at invocation; the catalogue is not itself authority.

**Acceptance:** tests assert manifest schema preservation, stable per-tool identity despite loop construction, trusted scope injection, distinct idempotency keys, and execution through a real Pydantic Agent/TestModel call. User/model arguments cannot replace bound IDs.

**Limitations:** this composes with the existing broker but does not yet qualify per-thread permissions, native Harness session integration with Prompt Sheet/staging state, app/connector dynamic capability discovery, deferred approvals, or cross-process idempotency. Tool schemas use the pinned Pydantic AI `FunctionSchema` extension point to preserve current Integral JSON Schema and defer authoritative validation to the Core broker.

**Handoff:** WP-06.2 supplies an authorized, revisioned scope and capability profile per chat turn. WP-06.3 translates Pydantic AI event streams into Integral's normalized event contract and ties stream cancellation to the active Agent run.
