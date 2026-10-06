# WP-01.3 — LiteLLM request and usage contract evidence

`backend/app/agentive/harness/contracts.py` now defines three strict, frozen contracts: `ResolvedModelRoute` for transient SDK routing, `PhysicalModelRequest` for one physical dispatch, and `ModelUsageObservation` for source-reported quantities and cost certainty. The route masks its API key during model serialization and forbids keys for local inference. The observation preserves missing cost as `null`, distinguishes cached/reasoning tokens, and requires a cost source and completeness flag. Physical attempts bind to an immutable `HarnessExecutionScope` and retain provider request IDs when available.

These are correlation and measurement contracts. They do not authorize a model call, provide retry deduplication on their own, or set a customer price. They carry no transcript or prompt payload. The adapter must create a new physical request identity for each actual LiteLLM SDK dispatch and the accounting service must persist facts append-only without credentials.

The route investigation confirmed the installed Pydantic AI `LiteLLMProvider` accepts an OpenAI-compatible HTTP client and targets a LiteLLM endpoint. Integral has no configured Proxy URL in the inspected source and currently resolves workspace-owned credentials for the in-process LiteLLM SDK. WP-04 therefore needs an Integral adapter over that SDK; direct vendor SDK calls and an assumed Proxy route are excluded. LiteLLM retry/fallback must be disabled or surfaced as separately identified physical attempts.

Validation on the Python 3.14.3 backend environment:

- `.venv/bin/pytest tests/native_harness/wp_01/test_execution_scope.py -q` — 13 passed.
- `.venv/bin/black --check` and `.venv/bin/isort --check-only` on `contracts.py` and the test — passed.
- `.venv/bin/flake8` on the same paths — passed.

No provider key was read or recorded and no external model request was made. Physical LiteLLM transport, callbacks, streaming, tool conversion, retries, and live usage/cost reconciliation remain WP-04/WP-05 qualification.
