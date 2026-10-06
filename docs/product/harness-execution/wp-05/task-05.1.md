# WP-05.1 — Physical model request observations

**Status:** implemented; awaiting integration review

**Objective:** persist a durable intent before each LiteLLM SDK dispatch and append a terminal or uncertain transition without rewriting the intent.

**Owned source:** `backend/app/models/harness_records.py`, `backend/app/agentive/harness/model_observations.py`, and `backend/app/agentive/harness/litellm_model.py`; tests: `backend/tests/native_harness/wp_05/test_model_observations.py` and transport tests in WP-04.

**Contract:** use a jvspatial `Object` for log-shaped, scalar-keyed observations. Hash tenant/principal/thread/session into an indexed scope key; AES-GCM encrypt full records with record ID as AAD; deterministic identity includes request ID and transition. Persist `dispatch_intent` before SDK invocation and fail closed if persistence fails. Append `responded`, `failed`, `cancelled`, or `outcome_unknown` as a separate record; retain token/cost certainty as observed. Readback filters by exact scope/run and authenticates ciphertext.

**Acceptance:** intent-before-dispatch, response and partial-stream uncertainty paths, append-only transitions, exact-scope readback, idempotent duplicate, conflicting duplicate rejection, and ciphertext protection are tested offline.

**Limitations:** tests use mocked Object persistence and a test encryption key. PostgreSQL transaction/durability guarantees, external LiteLLM callback correlation, cost reconciliation, retention/deletion, and billing admission remain open. No monetary amount is a tariff or invoice line.

**Handoff:** WP-05.2 adds reconciliation and idempotent import of LiteLLM callback facts. WP-06 supplies the real tenant/run observer and ensures every production native model factory requires it.
