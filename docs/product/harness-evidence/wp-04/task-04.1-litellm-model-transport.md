# WP-04.1 — LiteLLM SDK-backed model transport evidence

`backend/app/agentive/harness/litellm_model.py` provides a Pydantic AI `OpenAIChatModel` over the installed `LiteLLMProvider`. An injected `httpx.AsyncBaseTransport` intercepts the OpenAI-compatible chat request and calls LiteLLM's in-process `acompletion`, with the API key taken only from `ResolvedModelRoute`. The fixed scope is attached to observer receipts; the request body cannot change the selected route. LiteLLM internal retries are set to zero so each dispatch receives a fresh Core request ID. JSON responses and streaming chunks are converted back to OpenAI-compatible response/SSE shapes.

Offline validation uses an injected fake `acompletion` callable; the `.invalid` endpoint is not contacted. The tests prove: a real Pydantic `Agent.run` traverses the model bridge; text and function-tool-call messages remain parseable; streaming deltas and the final usage chunk reach the OpenAI client; observed token categories and response cost are normalized; the trusted route key reaches only the fake SDK; retries are disabled; and a route mismatch is rejected before SDK dispatch.

Validation on the Python 3.14.3 backend environment:

- `.venv/bin/pytest tests/native_harness/wp_04/test_litellm_sdk_transport.py -q` — 5 passed.
- Combined native Harness regression `.venv/bin/pytest tests/native_harness/wp_00 tests/native_harness/wp_01 tests/native_harness/wp_02 tests/native_harness/wp_04 tests/test_chat_stream_interrupted_persist.py -q` — 42 passed.
- Black, isort, and flake8 passed over `backend/app/agentive/harness` and `backend/tests/native_harness`.

This does not qualify real LiteLLM SDK response variants, provider APIs, multimodal messages, native Pydantic tools, callbacks, retry behavior beyond the explicit `num_retries=0` argument, uncertain outcomes, persistent observations, or monetary reconciliation. It does not yet register a chat provider or provide the Integral broker toolset. No provider key was inspected and no external/paid request was made.
