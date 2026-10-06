# Resident harness recalibration — 2026-10-05

This is integration evidence, not V1 acceptance. Tested branch:
`feat/pydantic-ai-harness-v1`. Browser: authenticated `Harness Smoke Test`
workspace on `http://127.0.0.1:9012`, proxying the checkout backend on 4011.
Installed Pydantic AI 2.54.0, Pydantic AI Harness 0.36.0, LiteLLM 1.101.4.
The user-visible 9011 conversation observed earlier was a legacy JV thread;
its transcript was not counted as native-harness evidence.

## Browser evidence

All prompts below used ordinary wording without skill names or tool coaching.

| Workflow | Observation | Result |
| --- | --- | --- |
| Workspace and track lookup | Named the smoke workspace and correctly reported its initially empty state; three Core reads, two model calls | Passed |
| New equipment register | Requested serial number, condition, location and holder. Native skill load, coverage and saved proposal; four model calls, 69.2k tokens, 13.0s | Passed proposal |
| Natural approval | `That sounds good. Go ahead.` then build and verify, without separate staging approval cards; five metered calls including approval judgment, 88.8k tokens, 10.7s | Passed |
| Structure readback | Apps showed one Equipment Register; its Equipment track showed Serial Number, Condition, Location and Assigned To in All Equipment; initially no entries | Passed |
| Add existing record, first attempt | Repeated discovery and hit a library usage limit. Search with limit=1 returned a skill but no tool; skill loading did not disclose its declared tools | Failed; corrected and retested independently |
| Add existing record, corrected attempt | Requested cordless drill DR-001, Good, Workshop, Ravi; correct tool staged one exact record in four model calls, 51.6k tokens, 13.8s | Passed staging |
| Record confirmation | One Approve click applied the record; native follow-through read it and linked the local record; one metered call, 23.5k tokens, 3.2s | Passed card approval |
| Record readback | Table contained exactly the drill with the requested values, and the entry panel opened | Passed |
| Cost display on unpriced GLM route | Corrected runs show token usage without falsely displaying $0.0000. Raw LiteLLM zero remains retained separately in the physical request usage record | Browser display passed; raw retention covered by source tests |
| Native OpenAI workspace lookup | Administrator account, native Integral AI binding, configured platform OpenAI key; gpt-4.1 returned the correct workspace and Posts track; three physical calls, 11,639 tokens, $0.0185, 6.8s | Model/tool execution and cost display passed; generated navigation link failed |
| OpenAI generated track link | The model omitted the `n.Track.` prefix from the returned ID; clicking its Posts link showed Track not found | Failed; remains a routing/output acceptance gap |

The OpenAI control temporarily changed the backend's default model, not tenant
credentials. The usage details showed all three calls and provider-response
cost attribution. This is not qualification of all OpenAI workflows or BYOK.

![Native OpenAI model and cost details](openai-cost-readback.jpg)

![Created equipment structure](equipment-readback.jpg)

![Applied drill record](drill-readback.jpg)

Created objects:

- App `n.WorkspaceApp.73012a30316445d789dfa8cf`.
- Track `n.Track.f6555a4d645e465b9a54e677`.
- Entry `n.Entry.3fff265b0f8144a09a56a937`.

## Corrections and source evidence

- Unified discovery uses native `ToolReturn.tools` disclosure and replays
  availability from framework history. Search retains a workflow and a tool
  even when the caller requests one result.
- Standard `allowed-tools` metadata is retained in projected skills. The
  supported tool preparation hook exposes those schemas while the skill is
  active. Integral's broker still authorizes every invocation.
- Removed default duplicate tool search and default extra Planning surface.
  Low-level app setup primitives remain behind Core's composed proposal/build
  contract on the resident model surface.
- Native approval uses the same metered model adapter, binds consent to the
  saved design/message, and checks the active session/run transactionally.
  The initial graph-ID lookup bug was reproduced in the browser and fixed.
- PostgreSQL regression tests passed for actual graph versus logical session
  identity, committed marker readback/cache invalidation, and a newer run
  taking ownership during judgment (two cases).
- Public `Tool.from_schema` replaced the private FunctionSchema import.
- Reported provider zero remains valid; an unpriced LiteLLM placeholder zero
  is retained as raw SDK accounting and is not treated as provider cost.
- Verification success now requires the result status `verified`; partial,
  blocked and failed readback cannot become a readiness claim.
- Native harness plus proposal service suite: 241 passed, 28 skipped before
  the final verification-status assertion was added. PostgreSQL cases above
  were run separately; skips are not PostgreSQL acceptance evidence.
- Full `make verify` completed successfully on this slice: staged guards,
  pre-commit checks, format/lint/types, reproducible wheel/import, CI-faithful
  smoke, frontend (1,305 tests across 220 files), and the full backend suite.
  PostgreSQL-only skips in that suite are not database qualification evidence.

## Outstanding acceptance work

- Failed/stopped turn reconciliation still blocks continuation in affected
  conversations; the discovery retest used a new conversation and does not
  qualify recovery.
- Record approvals still require the existing card and disable the composer.
  Natural approval is qualified for the exact saved scaffold design only.
- The successful scaffold response initially invented `app.integral.ai` as
  its deployment host. Relative-link instructions were added; the subsequent
  drill response linked the correct local record.
- Saved proposal wording is not consistently explicit that it is unbuilt.
- The host-generated `[PROMPT_SHEET]` marker is visible in chat after a card
  approval and needs removal from product-facing copy.
- Repeat OpenAI navigation, design-only, amendment/refusal, cancellation and
  recovery workflows against the final revision. Most build workflows in this
  round used GLM cloud; OpenAI lookup/cost details are recorded above.
- No commit or push is asserted by this report.
