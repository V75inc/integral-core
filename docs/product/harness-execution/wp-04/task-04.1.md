# WP-04.1 — LiteLLM SDK-backed Pydantic model adapter

**Status:** implemented; awaiting integration review

**Objective:** route Pydantic AI's OpenAI-compatible chat requests through Integral's existing in-process LiteLLM SDK using a trusted, immutable workspace route.

**Owned source:** `backend/app/agentive/harness/litellm_model.py`; tests: `backend/tests/native_harness/wp_04/test_litellm_sdk_transport.py`.

**Contract:** construct Pydantic AI `OpenAIChatModel` with the installed `LiteLLMProvider` and a custom `httpx.AsyncBaseTransport` that translates requests to `litellm.acompletion`. Bind provider/model/key to `ResolvedModelRoute`; enforce exact model-route equality; disable LiteLLM internal retries so Core can assign a distinct ID per physical dispatch; append a secret-free `dispatch_intent` before the SDK call and response/uncertainty evidence afterward; convert response chunks to OpenAI-compatible JSON/SSE. The `.invalid` HTTP base is intercepted by the in-process transport and must never resolve over the network.

**Acceptance:** fake-SDK tests prove Pydantic Agent invocation, text and tool-call response handling, streamed deltas and final token usage, provider/cost observation, secret injection from the trusted route, retry setting, and pre-dispatch route mismatch rejection. No vendor API call is allowed in this task.

**Limitations:** the adapter currently targets Chat Completions; native provider-specific tools, non-chat endpoints, multimodal payload compatibility, hidden cost fields, SDK exception/unknown-outcome paths, actual LiteLLM behavior, and durable attempt observation require separate coverage. This does not register or expose a chat provider, broker Integral tools, or qualify production accounting.

**Handoff:** WP-04.2 resolves workspace model policy/credentials into `ResolvedModelRoute` and owns client lifecycle. WP-05 persists observed physical attempts and reconciles ambiguous failures. WP-06 connects the model and Harness runtime to normalized Integral chat events.
