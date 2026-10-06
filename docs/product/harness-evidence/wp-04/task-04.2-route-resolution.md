# WP-04.2 — Trusted route resolution evidence

`resolve_native_model_route` accepts an authorized workspace ID and a default provider/model supplied by trusted runtime configuration. It delegates workspace BYOK resolution to `resolve_agent_model_override`, retains the composed LiteLLM model route, and wraps the transient API key in `SecretStr`. With no workspace override, it leaves deployment credentials in LiteLLM's environment. A local `ollama/` route carries no credential. Invalid default routes are rejected before querying credential state.

The adapter does not accept model route or key values from user text, thread metadata, or browser fields. The default route remains the caller's responsibility; WP-06 must bind it to the native resident model profile and preserve per-run isolation. This does not select the `integral_native` provider or prove that LiteLLM's runtime environment contains the configured platform key.

Validation on Python 3.14.3:

- `.venv/bin/pytest tests/native_harness/wp_04/test_model_route.py -q` — 3 passed.
- The combined WP-01 and WP-04 suites — 26 passed.
- Black, isort, and flake8 passed across `backend/app/agentive/harness` and `backend/tests/native_harness`.

Tests use fake resolver results only. No real workspace credential was fetched, no API key value was inspected, and no provider request was sent.
