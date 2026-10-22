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

## UI checks

Inspected the rendered page at 1440x1000 and 390x844. Role cards stack on mobile; document width384 fits viewport390. The model editor retains padded fields and visible Save/Cancel. Advanced controls start collapsed. Close → Shift+Tab wraps to Save; Escape dismisses and restores the launcher's focus. Component tests cover provider toast success/failures, metadata errors, loading/empty states, owner restrictions, ENV overrides and profile-edit/routing-draft revision behavior. No full WCAG audit is claimed.

## Automated checks

Full backend:7278 passed,983 skipped. CI-faithful smoke:2139 passed,24 skipped (`TESTING=1`, `DEBUG=false`, no `.env`, four xdist workers). PostgreSQL configuration/routing regressions:44 passed, including loop-clock offsets and real uvloop. Full frontend:345 files,2064 tests passed; final status/menu regressions passed separately after copy/icon refinements. Production build, pinned formatting, guards, pre-commit, mypy, TypeScript and ESLint checks passed; ESLint retains482 repository warnings and zero errors. Reproducible wheel/import qualification passed.

No graph schema, ownership, permission or approval path changes. I-GRAPH-01/02, I-CRUD-01, I-SUBSTRATE-01 and I-EXT-01 are preserved. The single resident harness, broker admission, physical-request accounting, shared limits and stale-authority rejection remain in force.
