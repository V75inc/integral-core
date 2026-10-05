# WP-02.2 — Encrypted scoped StepStore backend

**Status:** accepted for encrypted scoped persistence and PostgreSQL idempotency/CAS behavior; retention, migration, and key-rotation gates remain in WP-02 package qualification.

**Objective:** persist Pydantic AI Harness runs, events, tool-effect transitions, and continuable message snapshots through Core-owned encrypted, tenant-scoped storage.

**Owned paths:** `backend/app/models/harness_records.py`, `backend/app/agentive/harness/scoped_store.py`, `backend/app/agentive/harness/jvspatial_store.py`, runtime factory wiring, and `backend/tests/native_harness/wp_02/`.

**Behavior:** framework identifiers are mapped to an immutable tenant/principal/thread/session namespace. A second namespace check is applied in each database query. Event and snapshot writes use deterministic IDs when the framework provides idempotency keys. Tool effects are append-only status transitions. Every serialized payload, including transcripts, is AES-GCM encrypted with the durable record ID as associated data. Missing encryption configuration and invalid ciphertext fail closed. The factory defaults to this Core backend and permits an injected StepStore for tests.

The Planning capability uses a Core PlanStore scoped to the same immutable tenant/principal/thread/session identity. It stores encrypted plan state in an Object record and uses a database revision CAS for mutations. jvspatial's JSON backend implements the generic update with a read/write pair and remains single-process development storage; PostgreSQL concurrency qualification remains an open acceptance gate.

**Verification:** encrypted Postgres run/event/checkpoint round-trip; cross-principal isolation; concurrent idempotent run/event/checkpoint writes; conflicting run/event/checkpoint key reuse rejection; and concurrent PostgreSQL PlanStore CAS adds with no lost item. Core graph/session lifecycle and full-root reachability are qualified in WP-02.1. These probes exercise independent database transactions in concurrent tasks, not a multi-host deployment. Migration/backfill behavior, encryption key rotation, retention, and public export remain open at package level.

**Required invariants:** I-GRAPH-02, I-HARNESS-01, I-WORK-01..06. The detailed checkpoint remains an Object, never an unattached Node.

**Acceptance:** passed for encrypted scoped persistence, collision protection, PostgreSQL idempotency/CAS probes, and the WP-02.1 session contract. This does not accept overall WP-02 or establish multi-host runtime fencing.
