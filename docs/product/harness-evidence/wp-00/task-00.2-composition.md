# WP-00.2 — Offline composition evidence

## Environment and command

Temporary isolated interpreter: `/tmp/integral-pydantic-harness-v1-probe` (Python 3.14.3), Harness 0.30.0, Pydantic AI Slim 2.54.0, Pydantic 2.13.5, PyYAML 6.0.3. The first Skills construction correctly exposed its optional `skills` extra requirement; after installing that extra in the temporary interpreter, the composition probe completed. No Core package or lockfile changed.

Command: `/tmp/integral-pydantic-harness-v1-probe/bin/python /tmp/integral_harness_capability_smoke.py`.

Probe composition: `Agent(TestModel(call_tools=[]))` with `Instrumentation`, `ToolSearch`, `Planning(tools=["write_plan"])`, Harness `Skills` over the Core skills directory selecting `integral-identity`, and `StepPersistence(InMemoryStepStore(), agent_name="integral-smoke")`. It used `run_stream` with a framework conversation ID, collected streamed text and usage, listed persisted runs, and continued the run from its stored messages.

## Result

The command exited 0. It streamed the TestModel sentinel `success (no tool calls)`, recorded one run, persisted two snapshot messages, and reported `RunUsage(input_tokens=57, output_tokens=4, requests=1)`. `continue_run` returned history with the same two messages as the settled run result. This proves those selected capabilities can be composed with the tested release and TestModel API; it does not establish a production route or production persistence.

Harness emitted: `Ignoring unsupported Agent Skill behavioral frontmatter fields: integral-identity: allowed-tools`. This matches the required boundary: Skills provides instructions, not execution authority. Integral must compute the permitted skill set from the authenticated host scope and independently register only brokered tools. The warning is compatibility evidence; it does not mean the Core standard frontmatter is invalid or should be modified to appease Harness.

## StepStore boundary

The 0.30.0 public `StepStore` protocol has methods for `register_run`, `get_run`, `list_runs`, `append_event`, `list_events`, `save_snapshot`, `latest_snapshot`, `record_tool_effect`, `get_tool_effect` and `list_unresolved_tool_effects`. Reads/writes address data with framework `run_id`; tool effects additionally use `tool_call_id`; lists filter by optional framework `parent_run_id`/`conversation_id`. The protocol has no caller, tenant, workspace, permission revision or Integral session argument. A thread-safe production adapter cannot rely on request ContextVar values implicitly if callbacks might run on worker threads; build it from immutable trusted execution scope and verify every read, write, list, fork and cleanup path. The bundled InMemory/File/SQLite stores are not shared production stores.

The probe's `conversation_id` was an arbitrary string (`probe-tenant/probe-thread`). This does not prove any tenant verification; it demonstrates that the framework accepts caller-shaped identifiers and that Integral must map opaque framework identities from an authorized persisted session, not treat the ID as an ownership claim. `InMemoryStepStore` exposes `list_snapshots` beyond the protocol; additional capability protocols may need further methods and must be inventoried before enabling those capabilities.

## Deferred approval/resume probe

Using the same full-lock disposable environment, a second no-network TestModel probe registered a `requires_approval=True` tool and selected `output_type=[str, DeferredToolRequests]`. The first call returned one pending approval and did not execute the tool. A second `Agent.run` with the returned message history and `DeferredToolResults(approvals={id: ToolDenied(...)})` completed as a string and returned four messages. The command exited 0. This proves Pydantic AI exposes a host-resolvable deferred call and can continue the transcript in a fresh run invocation. It does **not** prove persistence across process restart, WorkApproval binding, revalidation, duplicate rejection, or that Pydantic Harness StepPersistence serializes deferred approvals as a safe continuation. Integral must persist the pending operation/approval in Core and resume from a trusted server-side record only after current authority checks; no browser-supplied message history or approval answer is authoritative.

## Full-lock candidate installation

Copied `backend/pyproject.toml` and `backend/uv.lock` to `/tmp/integral-harness-lock-probe`, added only `pydantic-ai-harness[skills]==0.30.0` to the temporary manifest, and ran `uv lock --directory /tmp/integral-harness-lock-probe`. Resolution succeeded in 1.74 seconds and added 11 package nodes. Then `uv sync --directory /tmp/integral-harness-lock-probe --frozen --extra dev --extra test --no-install-project` installed 172 packages successfully on Python 3.11.15, including the existing pinned jvagent/jvspatial/LiteLLM set and Harness. This proves the candidate package set resolves and installs against this frozen manifest on Python 3.11; it does not test all supported Python versions, importing Integral from the disposable environment, or a production model request.

After that proof, the pinned package was added to Core's runtime dependencies and `uv lock` regenerated the repository lock. `uv sync --frozen --extra dev --extra test` succeeded on the actual Python 3.14.3 backend environment, and `uv pip check --python .venv/bin/python` reported all 173 packages compatible. Repeatable repository tests now live at `backend/tests/native_harness/wp_00/`; `.venv/bin/pytest tests/native_harness/wp_00 -q` passed both tests. Those test cases verify capability composition/stream/StepPersistence and a deferred approval round-trip using TestModel. The observed `allowed-tools` warning remains expected and is retained as evidence.

## Provider-route finding

The current locked stack exposes the LiteLLM Python SDK (not an Integral-operated OpenAI-compatible LiteLLM proxy URL). Pydantic AI's documented `LiteLLMProvider` accepts an HTTP `api_base` and speaks the OpenAI Chat Completions API to that endpoint. Therefore that provider cannot simply be pointed at the currently installed LiteLLM SDK. WP-00.3/WP-04 must prove an adapter that routes Pydantic AI model requests through the existing LiteLLM SDK and its callbacks/credentials, or design and separately operate a qualified proxy. Calling a provider SDK directly to get around this gap would violate the existing Intelligence Plane accounting boundary. Source: [Pydantic AI compatible API documentation](https://pydantic.dev/docs/ai/models/compatible-apis/).

## Not covered

- Complete Integral lock resolution or supported Python 3.10–3.14 matrix.
- Live LiteLLM/credential/model route, physical retries, loss-after-provider-response, or monetary capture.
- Exact capability hook ordering and nested auxiliary model-call coverage.
- Mid-stream client cancellation, deferred approval, detached continuation, concurrent run/session claim, worker death or cross-process store safety.
- Tenant namespace adapter, encrypted persistence, or PostgreSQL commit fences.

WP-00.3 must isolate and answer provider/usage compatibility before WP-04 pins an integration route. WP-01 will codify the ID and host-authority boundary before native tools become visible.
