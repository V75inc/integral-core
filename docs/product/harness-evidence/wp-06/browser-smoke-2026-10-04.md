# WP-06 browser smoke — 2026-10-04

## Environment

- Branch: `feat/pydantic-ai-harness-v1`
- Frontend: `http://127.0.0.1:9008/agent`
- API: `http://127.0.0.1:4002`
- Isolated PostgreSQL database: `integral_pydantic_smoke`
- Harness: Integral Native / Pydantic AI
- Model: local Ollama `ollama/gemma4:26b`
- Browser account: reserved synthetic smoke account (no production workspace)

## Exercise and evidence

1. Signed in through the browser and opened the Agent chat surface.
2. Confirmed the active binding displayed **Integral Native — Resident Harness**.
3. Created a new conversation and submitted a text prompt.
4. The request created a thread and message, dispatched to the local Ollama daemon, streamed a terminal assistant result, and rendered run diagnostics. The first browser run showed run ID `596722cf-f628-419e-b6a1-7e4d91bfba67`, provider session ID `53bcb3db-2017-44e0-90d3-61b63125c669`, terminal status `complete` / `stop`, first token at about 24.3 seconds, total stream time about 26.4 seconds, and 54 output tokens. Later local-route runs completed in about 4–5 seconds. The chat badge showed an approximately 2.1k-token conversation estimate.
5. Confirmed Ollama had `gemma4:26b` loaded on the local daemon during the run. The original LiteLLM route went to Ollama Cloud and failed because a local-only model was not available there. Added local `api_base` propagation from `OLLAMA_API_BASE` to the LiteLLM SDK call. Follow-up diagnosis found that LiteLLM's `ollama/` OpenAI-compatible adapter can flatten Gemma tool/reasoning output into content; the local route now selects LiteLLM's `ollama_chat/` adapter. A direct Pydantic AI + LiteLLM SDK reproduction on that route yielded distinct thinking events and the exact text answer. The full browser path still rendered a thought/role/content JSON-like envelope as ordinary assistant text, even after this route change and an explicit final-answer-only system instruction.

### Fresh-conversation repeat

- Created a second, fresh conversation in the browser; the active binding still showed **Integral Native — Resident Harness**.
- Submitted the same exact-answer prompt. Run `aa90b0d3-f4bf-4dca-ac09-a2a3620f6106` completed and rendered its usage/timing control (`2.1k tokens · 24.3s`); the chat bubble was marked complete.
- The visible bubble again contained a serialized `thought` / `role` / `content` envelope, including the requested answer inside `content`, instead of the exact answer alone. The message-debug panel confirmed `provider_id: integral_native` and the same malformed assistant body. No prompt or completion text was added to logs or this evidence file.
- This confirms the defect on a new native conversation after the local `ollama_chat/` route and final-answer-only instruction changes. The actual model route and Pydantic event classes for this browser run have not yet been captured, so the fault's precise layer remains unproven.

### Guarded regression repeat

- Added a stateful Pydantic AI event boundary that buffers text shaped like an assistant reasoning envelope or serialized function-call envelope. It extracts only `content` from the former; it rejects the latter without executing it. Ordinary JSON remains intact.
- The first guarded attempt withheld the private envelope and returned a safe normalization error because the provider split the envelope across text parts. Another browser run surfaced a function-call-shaped text body; that body was subsequently classified as an undispatched tool envelope and rejected safely.
- Fresh conversation run `50b08329-3ded-41ca-bb24-83f8f6de4eb0` completed with `provider_id: integral_native`, provider session `270463af-ee5b-466e-9eec-5f4aef981a8f`, and visible usage/timing `2.1k tokens · 4.3s`.
- The rendered message and Message Debug `MESSAGE CONTENT` both contained exactly `Integral Native harness smoke passed.` Reloaded the browser page and confirmed the persisted transcript still showed that exact assistant text.
- The request used the workspace's configured keyless local Ollama model. The route resolver maps that configuration to LiteLLM's `ollama_chat/gemma4:26b` adapter and local Ollama base URL. The browser confirms the native provider binding; the encrypted per-run observation was not independently decrypted to verify the resolved route field.

### User-requested smoke repeat

- In the current browser session, created a fresh conversation in the synthetic Harness Smoke Test workspace while **Integral Native — Resident Harness** was active.
- Submitted the exact-answer smoke prompt. The assistant rendered exactly `Integral Native harness smoke passed.` and the UI reported `2.2k tokens · 26.4s`.
- Reloaded `/agent` and verified the persisted conversation and exact assistant response remained visible. Browser console warning/error collection was empty.
- Confirmed the browser's API endpoint (`127.0.0.1:4002/health`) returned HTTP 200 with `status: healthy` and `database: connected`. Separate local API processes on ports 4000 and 4003 returned 503 but are not the API used by this browser session.

### Fresh-account browser smoke repeat — 2026-10-04

- Repeated the smoke in Chrome against the isolated local stack (`127.0.0.1:9008` → `127.0.0.1:4002`) using a newly created synthetic account and its private workspace. The account skipped optional email verification; no external account or workspace was used.
- Selected **Integral Native — Resident Harness** in Settings → Agent. The chat header confirmed the active binding.
- Submitted `Reply with exactly: Integral Native harness smoke passed.` The browser rendered exactly `Integral Native harness smoke passed.` The UI reported `2.1k tokens · 24.6s`.
- Message Debug confirmed `provider_id: integral_native`, `provider_label: Integral Native`, run ID `75600a7c-2d47-4280-9bc6-99d30b49e8ab`, and provider session ID `61afd435-0db0-408d-8b1a-aef27e3e02d0`. Its request metadata showed `session_id: null`; this is a diagnostic inconsistency to reconcile before treating Core conversation-session linkage as proven.
- Reloaded `/agent`; the prompt, exact assistant response, and usage/timing control remained visible.
- Browser console warning/error collection was empty. The configured API health endpoint returned HTTP 200 with `status: healthy` and `database: connected`.
- The displayed token count is UI diagnostics only; provider billing reconciliation was not tested. This one-account smoke does not establish cross-tenant isolation, durable worker execution, or session linkage.

### Request-ID plumbing browser regression — 2026-10-04

- Reloaded the updated frontend and started a fresh conversation with **Integral Native — Resident Harness** still active.
- Repeated the exact-answer prompt. The response again rendered exactly `Integral Native harness smoke passed.` with the UI reporting `2.1k tokens · 24.3s`.
- Message Debug confirmed `provider_id: integral_native`, run ID `53b248c7-43b1-4cb1-869a-aa6010baea9b`, and provider session ID `d08d072e-5858-487e-8409-7e9e33189c53`. The debug request continued to show `session_id: null`.
- Reloaded the page and confirmed the exact user and assistant messages plus timing control remained visible. Browser warning/error collection remained empty.
- This confirms the new optional request-ID field did not break the live native chat path; the 401 replay's request-ID equality is covered by the focused frontend provider test. Native WorkItem admission remains disabled, so this smoke does not claim durable worker execution.

## Result

**Earlier post-guard exact-answer browser smoke: PASS (historical). Latest rerun: FAIL.** The initial and second browser runs exposed private-envelope text; a later run exposed a function-call-shaped text body. The guard withheld those outputs, and an earlier guarded browser run rendered the exact requested answer across reload. The latest two fresh-conversation runs again hit the normalization fallback; see “Latest browser rerun” below. Do not treat the earlier pass as proof of consistent current behavior.

The browser smoke does not establish multi-tenant isolation, tool approval behavior, cross-provider interoperability, concurrency, token-cost reconciliation, or production readiness. Those remain separate acceptance items. The displayed token counts were visible UI diagnostics; no claim is made here that they reconcile to provider billing.

## Validation

- Focused route, event, and provider regressions: **19 passed** across `tests/native_harness/wp_04/test_model_route.py`, `tests/native_harness/wp_06/test_events.py`, and `tests/native_harness/wp_06/test_provider_stream.py`.
- Black, isort, and flake8 passed on the changed event/provider tests and implementation files.
- `make verify` was rerun after the guarded provider change. Repository guards, pre-commit checks, backend format/lint/mypy, reproducible wheel validation, CI-faithful backend smoke, and the full frontend suite (**1,304 tests across 220 files**) passed. The final full backend suite failed the same three tests listed in the ledger: two declared-app-scope MCP parity tests and one direct staging-profile test lacking a bound principal.
- The direct local Pydantic AI + LiteLLM reproduction remains diagnostic evidence only; the browser run is the user-visible acceptance evidence. The harness evidence does not claim provider-billed token reconciliation.

## Follow-up

- The deterministic `PydanticAIProvider.stream_turn` TestModel fixture now exercises provider streaming and asserts private markers do not appear in emitted text. Live event payload contents remain intentionally unrecorded; capture only redacted event class/route metadata if deeper diagnosis is needed.
- Verify a second turn on the same thread and a separately scoped tenant before claiming session continuity and isolation.
- Re-run the full required gate before merge.

### Latest browser rerun — 2026-10-04

- Opened the isolated local UI at `127.0.0.1:9008/agent`, with API `127.0.0.1:4002`. API health returned HTTP 200, `status: healthy`, and `database: connected`.
- The chat header showed **Integral Native — Resident Harness** in the synthetic Harness Smoke Test workspace.
- Submitted the exact-answer prompt in a fresh conversation. Generation completed and the UI reported `2.1k tokens · 25.8s`, but the assistant displayed `The assistant response could not be safely normalized. Please retry.`
- Repeated the same exact-answer prompt in another fresh conversation. It completed in `9.3s` and returned the same normalization fallback.
- Reloaded the page and confirmed the latest failed response remained persisted. Browser console warning/error collection was empty.
- No provider output or secrets were copied into this report. The UI usage estimate is not billing reconciliation evidence. These two runs do not validate durable WorkItem execution, cross-tenant isolation, or session linkage.

## Latest result

**Latest browser rerun: FAIL.** Earlier browser runs on this date passed after the output guard was added, but the two latest fresh-conversation runs again hit the safe normalization fallback. The current browser evidence therefore does not support calling the exact-answer smoke consistently green. Investigate the live Ollama/LiteLLM event shapes and strengthen the event-boundary handling before accepting WP-06.

The browser did confirm that the native binding was selected, requests reached a healthy API/database, the failure was surfaced safely, the failed assistant response persisted across reload, and the browser console remained clean. It does not establish successful model output for this build.

### Same-thread session continuity regression and recovery — 2026-10-04

- The next fresh conversation rendered the exact first-turn answer, but its follow-up failed with a safe retry message. A read-only inspection of encrypted, per-run model-request receipts found the same physical request recorded as `dispatch_intent → responded → outcome_unknown`. The `outcome_unknown` was emitted by async stream cleanup after the known response; recovery correctly refused to continue while the ledger showed uncertainty.
- Fixed `_LiteLLMStream` terminal-state ordering so cleanup cannot append a conflicting unknown outcome after settlement. Added transport regressions for concurrent close during terminal observation and iterator close after `[DONE]`.
- Started another fresh conversation after the fix. First turn rendered exactly `Integral Native harness smoke passed.` (`2.1k tokens · 5.0s`). The same-thread follow-up rendered exactly `second-turn session check passed.` (`2.1k tokens · 11.5s`).
- Message Debug showed `provider_id: integral_native`. The first turn's `provider_session_id` and the follow-up request's `session_id` both resolved to the same native session. The follow-up also completed under a new run ID, as expected for a new turn.
- Reloaded `/agent`; both user prompts, both exact assistant responses, and timing controls remained visible. Browser console error/warning collection was empty.
- The isolated API and database remained healthy. This smoke uses one synthetic principal/workspace and a local Ollama `gemma4:26b` route. It does not prove cross-tenant isolation, tool/approval behavior, durable WorkItem execution, provider-billed token reconciliation, or production readiness.

## Latest result

**Latest post-fix same-thread browser smoke: PASS.** It proves the native binding can complete two consecutive turns in one conversation, preserve the same native session identity, and reload both persisted responses. Earlier normalization, session-pointer, and unsettled-request failures remain documented above as regression evidence; they are not the current result after the latest fix.

## Latest focused validation

- LiteLLM SDK transport tests, including the two stream-settlement regressions: **9 passed**.
- Focused event-normalization, provider-cancellation, and Postgres session-pointer regressions: **13 passed** (PostgreSQL-backed run).
- Browser run: first turn PASS; same-thread follow-up PASS; reload persistence PASS; browser console warning/error collection empty.
- These focused checks do not complete the WP-06 3-scenario × 5-repeat matrix, tenant-isolation proof, or full release gates. No commit or push was made.
- Read-only verification of the final run's encrypted request ledger found `dispatch_intent → responded`, zero unsettled requests, and complete input/output token usage. Provider cost remained unavailable (`provider_cost_complete: false`) for this local Ollama route; the UI's “2.1k tokens” is an estimate and is not invoice-grade usage or cost evidence.
- Backend Black, isort, and flake8 checks passed for the touched harness/provider/test files. The complete `tests/native_harness` suite passed; five Postgres-only cases were skipped in the default JSON run. Separate Postgres-backed session-pointer regression passed in the earlier 13-test focused run.

### Ollama explicit-output truncation follow-up — 2026-10-04

- Reopened the earlier 90-line output-limit conversation. Its settled `response.output` ended at `END-` while the request required `END-COPY`; Message Debug showed `outputTokens: 2045`, `status: complete`, and `reason: stop`. This is a provider/default generation-budget boundary being reported as a normal stop, not text removed by the chat event translator or renderer.
- Local Ollama route resolution now sets a per-request output budget through LiteLLM's `max_tokens` parameter, which its Ollama chat adapter maps to `options.num_predict`. The default is 8,192 and `INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT` can tune it from 1 to 131,072. This is restricted to keyless local Ollama routes; OpenAI/other provider calls receive no such override. The existing 8,192 `num_ctx` remains a separate input-plus-output context limit.
- Repeated the same 90-line prompt in the browser against the patched Core API: output continued past the former ~2,048-token boundary, included all 90 numbered lines and the terminal `END-COPY`, and Message Debug's settled raw response matched the visible message. Reported output usage was 1,691 tokens and finish reason was `stop`.
- The model still corrupted the characters in numbered marker 061 (`6പരി 61` in the settled output). This is present in the raw model result and the rendered message, so it is generation fidelity wobble rather than harness truncation/translation. The harness preserves that raw result; it does not silently rewrite model text.
- The exact-output short control also returned `Integral-Ollama-Explicit-Return-7Q4N-Alpha-END` identically in raw response and chat, with a normal `complete` / `stop` status.
- Targeted route/transport tests: **21 passed**. They verify the configured local output budget reaches LiteLLM, OpenAI and Anthropic requests do not receive local Ollama overrides, and invalid local budget values fail before dispatch. This does not establish exact-copy reliability for open models on arbitrary prompts or qualify larger outputs at the configured 8,192-token cap.

### Ollama output-budget alias regression — 2026-10-04

- Ran the explicit-output Ollama smoke in a fresh browser conversation against the local Integral API on port 4002. The prompt requested 280 numbered lines and a final `END-EXACT` marker.
- The settled Message Debug `MESSAGE CONTENT` and the rendered chat both contain lines 001–280 and the final marker. The visible chat screenshot shows lines 257–280 and `END-EXACT`; the model metadata reads `gemma4:26b · 12.1k tokens · 195.6s`.
- The first accessibility snapshot appeared to stop at line 250. This was a snapshot ceiling, not response truncation: the message container reports `showing 0-500 of 561 items`, and the debug content plus screenshot confirm the remaining lines are present. Earlier statements treating the 280-line browser run as truncated at 250 were incorrect and are superseded by this observation.
- The distinct earlier 90-line case remains evidence of actual output truncation: its settled debug payload ended at `END-`, reported `outputTokens: 2045`, and had a normal `stop` finish. Pydantic AI's OpenAI-compatible model profile sends the generic `max_tokens` setting as `max_completion_tokens`; the Ollama bridge now removes that alias and sets only the Core-owned `max_tokens` value for LiteLLM to map to `num_predict`. Its context remains separately set to 16,384 tokens.
- The transport regression starts with a 2,048-token Pydantic AI setting and asserts the outgoing local Ollama SDK call has `max_tokens: 8192`, `num_ctx: 16384`, and no `max_completion_tokens`. Focused route and transport tests: **22 passed**. Black, isort, and `git diff --check` passed on the modified files.
- This live 280-line case confirms no translation or UI loss for this prompt after the change. It does not prove all open models follow exact-copy instructions, nor does it reconcile local Ollama token estimates to billed usage.
