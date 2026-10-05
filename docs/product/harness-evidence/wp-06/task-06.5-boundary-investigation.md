# WP-06.5 — Initial boundary investigation

**Status:** unresolved after browser qualification

- `tests/native_harness/wp_04/test_model_route.py` now verifies local Ollama selects `ollama_chat/<model>` and carries the configured local API base.
- `tests/native_harness/wp_06/test_events.py` verifies private `ThinkingPartDelta` is not translated into Integral chat events.
- Focused checks: **8 passed**.
- Direct local reproduction with the full tool catalogue, `ToolSearch`, `Planning`, `Skills`, and `StepPersistence` emitted only answer text through the current event translator.
- Repeated browser smoke on a synthetic account still displayed a JSON-like thought/assistant-message envelope as ordinary assistant text. Run status and duration telemetry rendered successfully.

The browser symptom is the authority for user-visible qualification. The direct reproduction narrows the fault but does not identify whether the discrepancy is caused by the route selected for the browser's workspace, its request context, the live worker version, or transcript/SSE normalization. Capture the browser run's selected provider/model and typed event sequence without recording prompt/completion content, then resolve the cause before claiming answer-boundary compliance.
