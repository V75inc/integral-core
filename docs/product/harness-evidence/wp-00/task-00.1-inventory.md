# WP-00.1 — Baseline and compatibility inventory

**Status:** accepted for dispatching WP-00.2; not Harness runtime acceptance.

## Baseline

- Dedicated branch: `feat/pydantic-ai-harness-v1`.
- Branch base: `b0934bd5d39e7b07d714cb9c8d68ff26835c61b8`.
- At branch creation the checkout had 157 pre-existing changed/untracked paths. The aggregate fingerprint (recorded in `../harness-execution/ledger.yaml`) is for matching that exact worktree without copying its content into this evidence file. Keep this dirty baseline intact; implementation changes must be distinguishable from those paths.
- Backend interpreter: Python 3.14.3; uv 0.11.3.
- Current backend package versions: jvagent 0.1.8rc20; jvspatial 0.1.1; LiteLLM 1.101.4.
- Neither `pydantic-ai`, `pydantic-ai-slim` nor `pydantic-ai-harness` is a dependency in `backend/pyproject.toml` / `backend/uv.lock`, and none is installed in the backend environment.
- An isolated `/tmp/integral-pydantic-harness-v1-probe` environment has Harness 0.30.0, Pydantic AI Slim 2.54.0 and Pydantic 2.13.5. This proves only that this interpreter can resolve/install this candidate set; it does not establish compatibility with Integral’s complete lock, installed extras, models or production route.

## Verified source map

| Concern | Existing Core surface | Consequence for the native binding |
|---|---|---|
| Chat contract | `backend/app/services/chat_providers/base.py` defines `ChatTurnContext`, provider capability flags, normalized event expectations and `ChatBackendProvider`. | Implement an adapter and preserve the interface; do not fork the HTTP/UI chat flow. |
| Provider registry | `backend/app/services/chat_providers/registry.py` is a process-local provider registry. | Register an optional native provider through the established path; do not select it as default during V1 implementation. |
| Streaming and cancellation | `backend/app/services/chat_streaming.py` persists normalized provider events. Its active cancel helper imports/calls `jvagent_embed.cancel_interact`. | Native cancellation needs a provider-aware host path; this is a coordinator/shared-file change after contract proof. |
| jvagent lifecycle | `backend/app/main.py` imports/registers `jvagent_provider` as default and `_startup` bootstraps the embedded runtime. `backend/pyproject.toml` pins jvagent in base dependencies. | V1 can coexist as opt-in; removing the jvagent package/runtime from a Core distribution is a later native-only package milestone. |
| Conversations | `ChatThread` and `ChatMessage` are graph Nodes. A thread stores `user_id`, `workspace_id`, `provider_id`, and `provider_session_id`; messages are connected by `CONTAINS`. | Existing threads are the canonical conversation/history. No `HarnessSession` node/edge was found in current `nodes.py`/`edges.py`; a framework run/conversation identifier cannot replace Core ownership. |
| Durable work | `WorkItem`, `WorkApproval`, `WorkOutboxEntry`, lease token/fence, `WorkExecutionContext`, effect keys, worker and recovery services already exist. | Reuse the existing work and approval authority; do not add a second task queue or framework-owned approval authority. |
| Runs and observability | `agentive/services/execution_runs.py` defines `AgentRun` and `RunStep` Objects, provider-event ingestion, per-run model/tool records and aggregate model token counts. | Extend/mapping should be assessed before introducing duplicate run/step records. Existing aggregates are not an immutable per-physical-request financial ledger. |
| Model accounting | The scanned provider/gateway services have no Pydantic AI gateway and no immutable source usage ledger/reservation service. | Gateway admission and durable source observations remain necessary for commercial accuracy; Harness/OpenTelemetry alone does not satisfy that contract. |
| Skills | Core’s neutral parser `app/services/skill_format.py`, disk skills and compliance checks use the Agent Skills format. Harness `Skills` builds deferred capabilities from package bodies. | Candidate skill discovery can reuse standard skill content, but must receive only permission-filtered skills and must not execute bundled resources or grant tool authority. |

## Candidate release/API facts

The official Pydantic AI Harness docs and PyPI metadata say the package requires Python 3.10+ and supplies composable capabilities; the docs describe a 0.x API stability policy. PyPI currently identifies candidate 0.30.0. The inspected 0.30.0 wheel resolved with `pydantic-ai-slim` 2.54.0 in isolation. Treat those as candidate facts, not a production pin or evidence that every optional capability fits Integral’s lock.

The inspected public imports include `pydantic_ai.Agent`, core `pydantic_ai.capabilities.Instrumentation` and `ToolSearch`, and Harness `Planning`, `Skills`, and `StepPersistence`. `Agent.run`, `run_stream`, and `run_stream_events` accept per-call model, history, conversation/run IDs, metadata, capabilities and cancellation token. `StepPersistence` exposes a `StepStore` protocol plus memory/file/SQLite stores and resume/fork helpers. Its standard stores are not Integral tenant stores; do not use them for shared production state.

Two critical design constraints from inspection:

1. A StepStore method addresses data by framework run/conversation IDs and its protocol has no authenticated tenant argument. Any shared backend needs an Integral-owned tenant-filtering namespace/store wrapper, checks on every operation, stable ID mapping and adversarial cross-tenant tests.
2. Harness `Skills` turns skill directories into deferred instructions and warns that tool-execution/behavioral frontmatter is not its authorization surface. It does not replace the Integral broker, skill scopes, `allowed-tools` policy, WorkItem effect checks or staging. Integral constructs the discoverable subset from trusted tenant/App context and all callable tools still cross the broker.

Official sources consulted: [Harness documentation](https://pydantic.dev/docs/ai/harness/), [PyPI release metadata](https://pypi.org/project/pydantic-ai-harness/), [Harness skills](https://pydantic.dev/docs/ai/harness/skills/), and [upstream source](https://github.com/pydantic/pydantic-ai-harness). No model/provider request or credentialed test was made.

## WP-00.2 probe questions

- Does Harness 0.30.0 compose selected planning, deferred skills, instrumentation and StepPersistence against `TestModel` without a network provider?
- Which stream events and settled-history interfaces can be translated without dropping Integral’s normalized text/tool/source/status/message-finish semantics?
- Can an adapter around the published StepStore protocol enforce tenant namespaces and run/conversation identity mapping, and what portions of store enumeration need explicit filtering?
- Does `allowed-tools` parsed by Core remain declarative metadata while actual native tool availability is derived exclusively from a permission-filtered Core toolset?
- Which required behavior calls a model outside the ordinary Agent model boundary, and is it observable/admissible through the LiteLLM route?
- Does the complete Core environment resolve candidate extras without modifying its production lockfile? Candidate lock resolution is a separate isolated check.

## Unproven items / blockers

- Full dependency and optional-extra compatibility with this checkout’s locked runtime.
- Actual LiteLLM SDK/Proxy model request, provider feature parity, retry capture and usage normalization. No paid model calls were made.
- Actual concurrency, cancellation and crash/recovery semantics in the Integral event loop.
- A production-grade encrypted/fenced jvspatial StepStore implementing every protocol method safely.
- Any improvement in task efficacy, cost or latency; no evaluation corpus run has occurred.
