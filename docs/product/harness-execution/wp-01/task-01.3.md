# WP-01.3 — LiteLLM request and usage contracts

**Status:** implemented; awaiting integration review

**Objective:** freeze Core-owned, secret-safe contracts for a resolved model route, one physical provider request, and the usage facts observed from that response.

**Owned source:** `backend/app/agentive/harness/contracts.py`; tests: `backend/tests/native_harness/wp_01/test_execution_scope.py`.

**Contract:** resolved routes carry a provider/model pair, a transient masked key (or no key for local inference), and credential source/reference. Physical requests carry a unique Core request ID, immutable execution scope, route, attempt number, dispatch time, outcome, optional provider request ID, and optional usage observation. Usage counts distinguish input/output/cached/reasoning tokens; provider cost is nullable, and source/completeness are explicit. These contracts do not authorize dispatch or determine a commercial price.

**Acceptance:** strict frozen models round-trip; route secrets are masked in JSON; local routes reject API keys; zero cost is distinguishable from unavailable cost; immutable request scope and terminal evidence are test-covered.

**Handoff:** WP-04 maps Pydantic AI model requests to LiteLLM SDK calls using a `ResolvedModelRoute`; WP-05 persists `PhysicalModelRequest` observations append-only and reconciles callbacks/uncertain outcomes. No record may contain a plaintext credential.
