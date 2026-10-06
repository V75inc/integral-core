# WP-06.3 — Event stream evidence

`translate_event` maps typed Pydantic AI deltas and tool lifecycle events into the existing Integral provider envelope. Usage is emitted as a `step`; the provider emits the terminal timing envelope. It does not serialize raw Pydantic messages, route credentials, or exception contents.

Offline verification: `backend/.venv/bin/pytest backend/tests/native_harness/wp_06/test_events.py backend/tests/native_harness/wp_06/test_provider_stream.py -q` — passed. The tests drive the actual Pydantic AI `run_stream_events` interface with TestModel and assert text, tool start/result, usage, stable session identity, complete checkpoint creation, and next-turn history restoration.

This validates the adapter contract only. It does not prove SSE behavior in a browser, external LiteLLM routing, provider cancellation under a real network stall, or user-visible error UX.
