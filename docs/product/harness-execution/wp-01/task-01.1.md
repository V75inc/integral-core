# WP-01.1 — Immutable execution identity contract

**Status:** accepted

**Objective:** define the minimal versionable Core-owned identity payload for a single native Harness run and its tenant/session/run mapping.

**Owned source:** `backend/app/agentive/harness/contracts.py`; tests: `backend/tests/native_harness/wp_01/test_execution_scope.py`.

**Contract:** immutable, strict Pydantic schema; required tenant, principal, workspace, ChatThread, HarnessSession, run, permission-revision and capability-version identities; workspace is the tenant boundary; framework `conversation_id` maps to a server-owned session ID, and framework `run_id` maps to a server-owned physical invocation ID. Unknown fields, blank/padded keys and mismatched tenant/workspace are rejected.

**Acceptance:** deterministic JSON Schema and JSON round-trip; immutability and negative scope cases tested. This contract is not an authentication mechanism or proof of authorization. The persistence layer must construct it only after verifying workspace/thread access and must reload current permissions before resumed work.

**Handoff:** session schema and store adapters (WP-02) use these IDs as validated correlation fields. Request admission (WP-03) remains responsible for authoritative principal and lease checks.
