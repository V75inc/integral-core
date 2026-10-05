# WP-02.1 — Rooted HarnessSession graph contract

**Status:** accepted; PostgreSQL transaction, generation race, rooted reachability, rollback, and lifecycle evidence pass.

**Objective:** represent native Harness continuity as an Integral graph participant attached to its owning ChatThread, with a versioned binding generation and explicit lifecycle.

**Owned paths:** `backend/app/models/nodes.py`, `backend/app/models/edges.py`, the core node registration in `backend/app/main.py`, `docs/INVARIANTS.md`, and this task/evidence record. Preserve unrelated edits in these dirty files.

**Implemented in this slice:** `HarnessSession` is a Node; `HAS_HARNESS_SESSION` is a typed ChatThread relationship; the node is included in Core registration; I-HARNESS-01 defines tenant binding, lifecycle continuity, active-pointer-cache semantics, encrypted Object checkpointing, and the required production fencing posture. `ensure_harness_session` rechecks private owner/workspace, rejects session-ID collision, fails closed without a transactional shared store, conditionally updates the ChatThread generation, suspends a previous binding, and writes the new Node and edge inside one graph transaction. `transition_harness_session` supports suspend/close/expire/revoke, retains the graph node/edge, and atomically clears the active pointer.

**Qualification evidence:** the pinned PostgreSQL backend passes activation/rollback probes, a forced two-transaction generation race, graph traversal from `Root`, owner-scoped session export, and hard-delete cleanup. Activation uses the pre-existing ChatThread `updated_at` as its CAS token so legacy rows do not require a field backfill before the first binding.

**Required invariants:** I-GRAPH-01/02, I-ACCESS-01, I-CHAT-01, I-WORK-01..06, I-HARNESS-01, I-SUBSTRATE-01 and I-EXT-01. Every persisted HarnessSession must be rooted directly through its ChatThread edge in the same transaction. Log/checkpoint rows remain Object records.

**Acceptance:** passed for this graph/lifecycle contract. WP-02 remains running while StepStore uniqueness/concurrency, public export surface, retention, and remaining package-level proof are completed.
