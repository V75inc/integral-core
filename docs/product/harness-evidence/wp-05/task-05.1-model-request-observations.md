# WP-05.1 — Physical model request observation evidence

`HarnessModelRequestRecord` is a scalar-keyed jvspatial `Object` consistent with I-GRAPH-02. `persist_model_request_observation` stores a hashed tenant/principal/thread/session scope and an encrypted payload. Each deterministic record ID contains the Core request identity plus transition name. Replayed identical transitions are idempotent; reusing a transition ID for different evidence is rejected. Scope/run listing decrypts and validates every result.

`LiteLLMSDKTransport` now persists the `dispatch_intent` transition before calling `acompletion`. If that append fails, the OpenAI client receives a connection error and the fake SDK is not called. A complete response appends usage facts. A partial stream appends one `outcome_unknown` transition; stream close does not append a duplicate. This represents potential provider billing honestly when a dispatch's result is uncertain.

Validation on Python 3.14.3:

- `.venv/bin/pytest tests/native_harness/wp_05/test_model_observations.py -q` — 2 passed.
- WP-04 plus WP-05 targeted offline tests — 12 passed.
- Black, isort, and flake8 passed for the Harness runtime/tests.
- The encrypted-store fixture uses a test-only key and fake Object persistence. No API credential or external model was used.

Still open: PostgreSQL/shared-store qualification, real LiteLLM response variants and callback correlation, unresolved outcome settlement, retention, and Integral Business pricing/entitlements.
