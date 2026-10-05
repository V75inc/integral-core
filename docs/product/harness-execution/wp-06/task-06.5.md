# WP-06.5 — Native answer and reasoning boundary

**Status:** implemented; guarded browser qualification passed

**Objective:** make the full Integral Native path deliver only user-facing assistant content to the chat transcript, without leaking private reasoning or provider/message envelopes.

**Owned source:** `backend/app/agentive/harness/events.py`, `backend/app/services/chat_providers/pydantic_ai_provider.py`, `backend/app/agentive/harness/model_route.py`, `frontend/src/features/ai-chat/providers/IntegralNativeProvider.ts`; tests under `backend/tests/native_harness/wp_04/` and `wp_06/`.

**Root cause and correction:** the native provider previously forwarded every Pydantic `TextPartDelta` as user-visible text. The live local Ollama model returned two text-shaped control envelopes in separate runs: an assistant object with private `thought` plus final `content`, and a serialized function-call shape. Neither was a typed provider tool call. The stateful event translator now holds only those structural prefixes until `PartEndEvent`; it emits only the assistant envelope's `content`, rejects serialized function-call envelopes without dispatch, and preserves unrelated JSON text. Dedicated `ThinkingPartDelta` remains omitted. The guard does not log model output.

**Route and event path:** the isolated workspace configured local `ollama/gemma4:26b` without a provider key. `resolve_native_model_route` resolves that local-only route to LiteLLM `ollama_chat/gemma4:26b` and the local Ollama API base. The provider passes Pydantic AI's typed stream events to `PydanticAIEventTranslator`; safe text deltas continue through `chat_streaming`, SSE, frontend `text-delta` normalization, and Core `ChatMessage` persistence. The `PydanticAIProvider` regression fixture exercises this same event-to-chat path with deterministic Pydantic AI events. The browser run confirmed `provider_id: integral_native`; route, run diagnostics, and exact visible/persisted output are in the linked evidence file.

**Requirements:**

1. Trace the actual route and typed Pydantic AI event sequence from the real provider call through `translate_event`, `chat_streaming`, SSE, frontend normalization, and `ChatMessage` persistence.
2. Keep private `ThinkingPartDelta` content out of SSE, transcript state, logs, debug payloads, and persisted messages. Run/usage/tool telemetry may remain visible under existing redaction rules.
3. Preserve legitimate user-requested JSON answers. Any normalization must be structural and narrowly scoped; do not strip arbitrary text by keyword or silently invent an answer.
4. Add deterministic adapter/event tests and a full-provider regression fixture for the discovered cause.
5. Repeat the exact-response browser smoke on the isolated local PostgreSQL environment. The rendered assistant answer must be exactly the requested string, with no thought field or serialized assistant envelope in the transcript or debug message-content view.

**Exit evidence:** 19 focused route/event/provider tests pass; the guarded browser run completed with timing/token diagnostics, rendered only the exact requested answer, and retained that exact assistant message after page reload. The private-envelope and function-call-envelope paths have deterministic regression coverage. `make verify` passes guards, hooks, backend format/lint/mypy, the frontend suite, wheel validation, and CI-faithful backend smoke; the full backend suite retains the three unrelated failures recorded in the main evidence. Other WP-06 acceptance tasks remain tracked separately, and the native provider remains opt-in/non-default.
