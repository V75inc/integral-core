# WP-06.3 — Pydantic AI event stream adapter

**Status:** implemented; integration review required

**Objective:** translate Pydantic AI's typed stream events into the normalized event envelopes consumed by Integral chat, transcript persistence, and the browser.

**Owned source:** `backend/app/agentive/harness/events.py`, `backend/app/services/chat_providers/pydantic_ai_provider.py`, `frontend/src/features/ai-chat/providers/IntegralNativeProvider.ts`; tests: `backend/tests/native_harness/wp_06/test_events.py`, `test_provider_stream.py`, and `frontend/src/features/settings/sections/AgentsSection.test.tsx`.

**Mapping:** text deltas become `text-delta`; private thinking deltas are deliberately omitted; function calls/results become `tool-call` running/complete/error events; Pydantic AI usage becomes Integral's `step` event; the provider emits `message-finish` with elapsed timing. Private provider metadata and raw exceptions are never sent to the browser. The existing `chat_streaming` layer owns cancellation, checkpoints, transcript persistence, and SSE encoding.

**Acceptance:** TestModel's real `run_stream_events` output covers text, tool start/result, usage, and final timing. Disconnect checks raise cancellation inside the provider generator so the existing stream layer closes the Agent context and executes its cancellation path.

**Limitations:** the native provider is registered only when explicitly enabled at process startup and is never default. The settings option is visible in the current UI even when the backend flag is off; thread creation then returns the normal unknown-provider response. Attachments, vision, deferred approval/resume, and recovery of interrupted tool effects are not yet enabled. No external model request has been made.
