# WP-00.2 — Offline composition and continuity probe

**Status:** accepted

**Objective:** prove the pinned candidate's offline composition and step continuation behavior, then identify the exact tenant-store and deferred-action boundaries before production integration.

**Owned files:** this task brief and `docs/product/harness-evidence/wp-00/task-00.2-composition.md`. Probe code/environment stays under `/tmp`; no runtime dependency or lock changes.

**Candidate:** `pydantic-ai-harness==0.30.0`, resolved in `/tmp/integral-pydantic-harness-v1-probe` with `pydantic-ai-slim==2.54.0` and `PyYAML==6.0.3` (the Harness `skills` extra).

**Probe:** compose `Agent(TestModel)` with Instrumentation, ToolSearch, Planning, the selected Core Agent Skill and StepPersistence; stream a response, inspect run usage/history, and resume from the saved run. Inspect the public StepStore protocol for all access/list operations and note ordering/tenant arguments. No API key, external request, or production runtime is involved.

**Acceptance:** evidence records exact command/result, surfaced frontmatter and run-identity behavior, every StepStore boundary, and unresolved lifecycle semantics. An offline success is limited to API composition and test-model persistence; it does not qualify real model compatibility, a durable multi-tenant store, cancellation, deferred approvals, or costs.

**Handoff:** findings constrain WP-00.3 and WP-01 contracts. A production tenant store must receive immutable server-derived scope per instance and qualify every protocol method against an adversarial shared backend. Durable user approval must become a persisted Integral continuation, not an awaiting task.
