# WP-01.2 — Provider event and cancellation contract

**Status:** running

**Objective:** make host cancellation route through the selected chat provider, preserving jvagent behavior and enabling a native Pydantic provider to connect its cancellation token to the host turn registry.

**Owned source:** `backend/app/services/chat_providers/base.py`, `backend/app/services/chat_streaming.py`, and the jvagent provider adapter's cancel hook. Tests: `backend/tests/native_harness/wp_01/test_provider_cancel.py` plus existing interrupted-stream coverage. Keep any pre-existing edits in these files; this slice owns only provider cancellation dispatch and adjacent formatting required by the touched code.

**Checks:** native provider mock proves cancel dispatch is provider-specific and does not import jvagent; legacy jvagent fallback remains; existing interrupted chat persistence path remains green; black/isort/flake8 pass on touched files.

**Acceptance:** host turn cancellation invokes exactly the selected provider's cancellation adapter when available; legacy provider doubles retain the jvagent fallback; no provider cancellation hook is invoked for unrelated provider IDs without an implementation. This contract does not prove the future Pydantic adapter cancels LiteLLM network I/O; its concrete cancellation behavior is a separate acceptance item.

**Handoff:** WP-06 native provider implements `cancel_turn(thread_id=...)` and connects it to Pydantic AI `CancellationToken`/active stream. WP-03 still owns durable cross-process cancellation and worker fencing.
