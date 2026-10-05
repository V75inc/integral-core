# WP-04.2 — Trusted native model route resolution

**Status:** implemented; awaiting integration review

**Objective:** adapt Integral's existing workspace-owner credential resolver into an immutable model route for the Pydantic AI adapter.

**Owned source:** `backend/app/agentive/harness/model_route.py`; tests: `backend/tests/native_harness/wp_04/test_model_route.py`.

**Contract:** the caller supplies a trusted default `provider/model` route from the resident model profile. Core resolves the workspace owner BYOK override with `resolve_agent_model_override`. If present, the exact LiteLLM model route and transient key are retained; the route prefix identifies the LiteLLM provider. If absent under hybrid/platform-only, deployment credentials remain in LiteLLM's environment and are not copied into Core route state. A keyless `ollama/` route is classified as local. Missing strict-mode BYOK continues to raise the resolver's `ModelKeyRequiredError`.

**Acceptance:** tests cover workspace BYOK override, deployment fallback with no copied key, and malformed configured model rejection before credential lookup. Existing resolver owns owner lookup, key-mode behavior, decryption, and last-used updates.

**Limitations:** model profile selection is intentionally an input from a trusted host; this task does not parse jvagent YAML, define model preferences for `integral_native`, choose light/heavy models, or change the active chat provider. No API key value was read from settings or written to evidence.

**Handoff:** WP-06 must resolve the resident model profile and this route per authorized turn, then construct one Pydantic AI agent with a fresh transport. WP-05 persists the physical attempt observer records.
