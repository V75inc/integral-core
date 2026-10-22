# Model settings and dual-model smoke qualification

## Changes

Provider tests report success, rejected credentials and transport errors through the existing toast system. Configuration editors retain their own inline save errors.

The settings page has one active-model readout. Model/provider selection, supported reasoning effort and output limits remain immediately available; capability badges, sampling, context and execution budgets sit under Advanced settings. Shared limits and routing explanations are collapsed by default. Profile names are optional in the UI and default to the model ID.

Editing a model from an unfinished routing configuration returns to the selected pair. Profile-only revision updates can refresh the draft revision, while concurrent routing-assignment changes retain the old revision so the server rejects the stale save.

## Confirmed defects

1. On the local macOS deployment, uvloop's clock and Python's monotonic clock had different epochs. Rescheduling an asyncio timeout using the metrics clock could immediately expire a new turn. Deadline reads/rescheduling now use the event-loop clock. Monotonic elapsed-time metrics remain separate. Durable recovery converts remaining loop time to a wall-clock deadline and back without mixing epochs. Shared deadlines can tighten but cannot extend.
2. LiteLLM shallow-merges `extra_body` into the final Ollama request. Supplying `options.num_ctx` replaced the adapter's generated options, dropping output and sampling controls. The local route now preserves native output, temperature, top-p, seed, repeat penalty and stop options alongside the context window.

The suspected profile-cache defect did not reproduce after isolation; no cache change is included.

## Functional smoke matrix

A dedicated account, PostgreSQL-backed API/worker and a deterministic loopback model provider exercised actual requests through the resident Pydantic AI harness. The operator-configured local Ollama endpoint was used; custom-endpoint SSRF controls were preserved. Provider logs captured model IDs, numeric settings and tool names, never credentials or message contents.

| Scenario | Observed result |
| --- | --- |
| Automatic conversation | Light alone returned the answer |
| Typed operational-context escalation | Light handed off once to Heavy; Heavy searched capabilities and loaded a skill before answering |
| Conversation Heavy-only | Heavy executed discovery and completed |
| Conversation fixed profile | The selected profile executed, without choosing the configured Light profile |
| Single-model mode | The active main profile completed |
| Edit warmed Light profile | Next turn used the new model ID without restart and sent `num_predict=1536` together with `num_ctx=32768` |
| Stale routing revision | Save rejected; existing automatic pair preserved |

These deterministic responses verify dispatch and integration, not model answer quality or provider latency.

The running preview also exercised its actual saved providers: GPT-4.1 completed the Light request, and DeepSeek-v4.1-Flash completed a Heavy-only request with three tool calls (`search_capabilities`, `load_capability`, `integral_whoami`). OpenAI provider verification displayed the success toast. Existing providers, credentials and role assignments were retained; the smoke conversation was returned to workspace routing.

## Chat composer

The draft area sits above a single toolbar. Attachments, suggestions and a brain-icon model menu are grouped on the left; dictation and Send/Stop sit on the right. Reference hints sit below the input. Model choices use radio-menu semantics, show full profile/model descriptions and mark the current choice. Selection closes the menu and returns focus to its brain trigger. Running turns disable changes; failed saves keep the existing selection and display a toast. Model Settings, routing and model-setup shortcuts share the Settings tab's plain anatomical outline brain icon. The generic live status is “Responding…” for conversation and drafting; tool-specific activity and result-review statuses remain descriptive.

The collapsed activity line now includes whole-turn duration and compact token usage after the model (for example `gpt-4.1 · 2.4s · 1.1K tokens`). Token counts use K/M abbreviations; expanded usage keeps exact numbers. Partial reporting is labelled `tokens reported`, and absent measurements are omitted. Duration uses turn latency or client stream timing, never the sum of individual model calls. Longer readouts wrap on narrow screens. Verified against existing Light, Heavy and dual-model turns in the live preview and at390x844.

All AI Models update results now use success/error toasts: provider tests/saves, profile saves, default changes, removal, routing and voice settings. Failed saves retain their dialog and draft; update banners and inline mutation errors are removed. Loading failures and static field guidance remain content states. Verified a real profile save toast without changing the saved model settings.

## UI checks

Inspected the rendered page at 1440x1000 and 390x844. Role cards stack on mobile; document width384 fits viewport390. The model editor retains padded fields and visible Save/Cancel. Advanced controls start collapsed. Close → Shift+Tab wraps to Save; Escape dismisses and restores the launcher's focus. Component tests cover provider toast success/failures, metadata errors, loading/empty states, owner restrictions, ENV overrides and profile-edit/routing-draft revision behavior. No full WCAG audit is claimed.

## Automated checks

Full backend:7278 passed,983 skipped. CI-faithful smoke:2139 passed,24 skipped (`TESTING=1`, `DEBUG=false`, no `.env`, four xdist workers). PostgreSQL configuration/routing regressions:44 passed, including loop-clock offsets and real uvloop. Full frontend:345 files,2064 tests passed; final status/menu regressions passed separately after copy/icon refinements. Production build, pinned formatting, guards, pre-commit, mypy, TypeScript and ESLint checks passed; ESLint retains482 repository warnings and zero errors. Reproducible wheel/import qualification passed.

No graph schema, ownership, permission or approval path changes. I-GRAPH-01/02, I-CRUD-01, I-SUBSTRATE-01 and I-EXT-01 are preserved. The single resident harness, broker admission, physical-request accounting, shared limits and stale-authority rejection remain in force.

## Printed routing marker regression

The live chat exposed `OperationalContextRequest` as an assistant answer. The Light prompt named that schema as something to return, while the provider only recognized a typed result or serialized tool protocol. It accepted the bare schema name as conversation. The prompt now explicitly calls the output tool, and a narrowly matched bare schema/tool marker escalates with the original user input and existing usage accounting. The same check applies after private-envelope normalization. Ordinary explanations mentioning the type remain valid text. No printed arguments execute and escalation grants no permission or approval. Online/tool requests explicitly ask the operational pass to discover capabilities rather than refusing based on the tool-free Light pass.

In a fresh live conversation, the original request escalated GPT-4.1 to DeepSeek, used four governed tools and explained that no web-search service was connected. The finished readout showed39.0s and49.4K tokens. Earlier failed answers remain in their original conversation history; stored messages were not rewritten.

## Reasoning controls across provider choices

Verified missing controls in the pinned metadata for Ollama Cloud, OpenRouter and an Anthropic alias. DeepSeek V4.1 Flash now exposes Off/Low/High/Max; GPT-OSS exposes Low/Medium/High. These values were checked against Ollama's public `/api/show` metadata on2026-10-22. Local Ollama has an optional native on/off control; unknown model support is explicitly unverified. Custom OpenAI-compatible endpoints permit explicit bounded effort values without claiming endpoint verification. Provider defaults send no extra reasoning setting.

OpenAI uses the pinned per-model contract, including Max where declared. Anthropic preserves manual thinking budgets for compatible models, resolves the Claude3.7 alias and exposes adaptive effort where advertised. OpenRouter uses a packaged snapshot of336 models' published reasoning metadata, including mandatory reasoning, exact effort choices and token-budget support; the model catalog remains offline during chat execution. Publisher metadata is a fallback for models absent from that snapshot. The snapshot records its source and retrieval date in `backend/app/services/model_reasoning_catalog.json`; refresh it from `https://openrouter.ai/api/v1/models`, retaining only the per-model `reasoning` objects.

The transport preserves Ollama's selected native level instead of the pinned adapter's boolean conversion, applies saved settings ahead of deployment thinking defaults, sends OpenRouter's native `reasoning` object without rewriting Max to Extra high, and sends an explicit Anthropic disabled value for Off. Tests cover each provider, validation of unsupported levels, explicit false values, mutually exclusive controls and native SDK payloads. Reasoning-capable OpenAI, Claude and OpenRouter presets are available without changing existing defaults. Live desktop/mobile inspection confirmed the DeepSeek effort selector; no existing saved effort was changed during inspection.

Sources: [Ollama thinking](https://docs.ollama.com/capabilities/thinking), [OpenRouter reasoning metadata](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens), [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort), [Anthropic extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking), [OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning). Paid-provider execution was exercised with the saved OpenAI/Ollama providers; other providers were validated with offline contract and transport tests, not live credentials.

Follow-up validation: the full backend suite and no-env CI-style smoke run completed successfully; the final provider configuration/transport regressions passed91 tests after the provider refinements. The final frontend run passed345 files/2073 tests. Production build, public-wheel import qualification and an explicit wheel check for the336-model reasoning catalog passed. Provider-specific UI tests and real DeepSeek desktop/mobile rendering passed. Earlier CI for a257fe79 was fully green before these follow-ups; the new PR head requires its own CI run.

## Workspace model categories, voice and embeddings

The Models tab now uses aligned, single-open accordion categories for Language &
multimodal, Embeddings and Voice. All may be collapsed. Providers is the single
saved-connection catalog. Embedding and voice editors select compatible saved
providers; Voice input and Search link to AI Models for model management. Voice
preferences remain under Voice input. All mutation outcomes use toasts.

Local embeddings remain the default, with byte-identical existing local profile
IDs. Owner-managed optional BYOK embeds through OpenAI/OpenRouter/Ollama/custom
native APIs only on a compatible PostgreSQL passage store, with explicit text
processing consent, isolated model spaces and durable rebuilds. Voice can reuse a
saved OpenAI key independently of chat setup; key rotation is resolved live.
Implementation and invariants are documented in
`docs/implementation/workspace-voice-and-embedding-models.md`.

Final qualification:

- `make verify` passed: guards, pinned formatters, types, wheel/import checks,
  CI-faithful smoke and both full suites. Frontend: 346 files / 2,079 tests.
- The final native transport and configuration regressions passed 99 tests.
- The final real PostgreSQL transaction and pgvector lane passed 16 tests,
  including rooted configuration creation/rollback, concurrent revisions,
  synthetic BYOK indexing/query vectors and stale-job fencing.
- Production frontend build passed. Final precommit/guard and wheel/import
  qualification were repeated before commit.

Desktop and 390x844 mobile rendering was inspected for accordion alignment,
editor spacing and wrapping. Keyboard expansion, Escape and focus return were
checked. UI tests cover category exclusivity, consent, provider compatibility,
failed-draft retention and toast notifications.

A live OpenAI `text-embedding-3-small` test on the final transport processed only
`Integral embedding configuration test`, returned 1,536 dimensions and displayed
an “Embedding model test passed.” toast. The unsaved draft was discarded and the
local default (384 dimensions) was confirmed afterward. No workspace content was
sent and no saved user chat, embedding or voice configuration was changed.

Model choices live in a rooted `WorkspaceModelConfiguration` node, preventing
ordinary cached Workspace saves from restoring obsolete settings. Provider
ownership is checked at dispatch; former owners can revoke their private
provider after transfer. Platform-only policy uses deployment embeddings while
preserving the inactive workspace override. Custom endpoints reuse the existing
DNS-pinned outbound guard; responses and total request duration are bounded.

## Light-to-Heavy handoff follow-up

The live QA conversation exposed a routing miss: in Automatic mode, “can you help
me prepare an app that tracks my todo list” received generic coding advice, and
“build it for me” received a capability refusal from GPT-4.1 without a Heavy pass.

Light now makes an explicit Pydantic AI output-tool choice: `answer_conversation`
(with a bounded category), `request_operational_context`, or
`propose_simple_reminder`. Free-form text is no longer an accepted Light outcome.
A provider ignoring the required output protocol requests operational context;
its original usage remains counted. App planning/building and operational
follow-ups are explicit handoff cases in the routing instructions. Earlier
assistant refusals are not capability evidence. No keyword classifier, extra
routing inference call, tool authority or approval bypass was added.

Live checks with the saved providers:

- The affected “build it for me” follow-up used GPT-4.1 then DeepSeek V4.1 Flash,
  checked the workspace and produced a reviewable To-Do App design in 17.7s.
  The proposal was not approved or installed.
- The original App-planning request also used GPT-4.1 then DeepSeek and produced
  a reviewable design (24.8s), rather than generic coding advice.
- A fresh greeting stayed on GPT-4.1 and completed in 1.8s.
- Selecting Heavy only dispatched DeepSeek and returned the requested OK.
- Returning that conversation to Automatic used GPT-4.1 for “Hello again” (3.8s).

The native harness suite and dual-model routing run passed 841 tests (76
PostgreSQL-only checks skipped in this SQLite lane). Regression tests
exercise the real SDK bridge's required tool-choice payload, refusal-bearing
history, typed and malformed output, usage preservation and governed escalation.
CI-faithful smoke and precommit/guard checks were repeated before commit.
