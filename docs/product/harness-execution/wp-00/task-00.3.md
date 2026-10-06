# WP-00.3 — LiteLLM route and usage boundary

**Status:** accepted for dependency dispatch; live paid-provider qualification remains unqualified

**Objective:** establish how Pydantic AI requests traverse Integral's current LiteLLM SDK path, preserve credentials and physical-request usage, and identify the exact remaining qualification work without making an unapproved paid model call.

**Owned files:** this task brief, `docs/product/harness-evidence/wp-00/task-00.3-provider-route.md`, and any isolated probe under `/tmp`. Runtime adapter source belongs to WP-04 after the route contract is established.

**Required checks:** inventory the existing user/workspace credential resolver, model routing and LiteLLM callback/usage capture; compare Pydantic AI's supported LiteLLM integration API against the in-process SDK; prototype a no-network callback/usage adapter where possible; record multimodal, streaming, retry, route and failure gaps; inspect whether required API credentials exist without printing secret values.

**Acceptance:** report a supported no-bypass routing design and test evidence for normalized route/model/request/usage fields, or mark it blocked on a specific architecture/input decision. The custom LiteLLM SDK transport, route contracts, normalized usage fixtures, retry boundary (`num_retries=0`), physical-request intent/outcome records, and local Ollama browser path now establish the design and non-paid path. This accepts the WP-00 dependency decision only; OpenAI/Anthropic live routes, actual provider fallback behavior, and paid usage reconciliation remain unqualified. A successful mock is not live route qualification. Do not dispatch external paid requests unless the user has explicitly budgeted them. WP-04 cannot claim production provider support until those provider-specific requests and uncertainty paths are captured.

**Handoff:** design the Core LiteLLM `Model` adapter, credential/admission factory and immutable physical-attempt record in WP-01/04/05. Keep user-owned provider keys outside agent-facing tools and telemetry.
