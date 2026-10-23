# Bounded context across resident harness runs

Status: implemented in PR #171; qualification results are recorded separately
in `docs/ops/qualification/harness-context-lifecycle.md`.
Reviewed 2026-10-23 against PR #171, commit `e6d15099`,
`pydantic-ai-slim==2.54.0` and `pydantic-ai-harness[skills]==0.36.0`.

## Outcome and evidence

For a fixed-size task, increasing conversation age or the number of previously
used Apps/skills must not increase the model request without bound. Current
work, explicit user constraints, approvals and effect identity must survive.
This is a context-retention defect within a conversation, not evidence of
cross-workspace exposure or a process-memory leak.

The existing deterministic audit rebuilt the native Agent for each run and
resumed the actual StepStore checkpoint. It measured outgoing OpenAI-compatible
JSON using a local mock transport, without cloud inference:

| Workload | Observed result |
| --- | --- |
| 100 repeated read workflows | Projected request size stabilized near 75 KB |
| 60 runs loading the same skill | Final actual request 34,237 bytes; one skill body |
| 60 runs loading different skills | Final actual request 1,263,174 bytes; all 60 skill bodies |

The fixtures and reports are in the local recovery directory
`consolidate-20261023-161546`. Convert them into repository regressions in step 1;
their current location is not a durable CI gate. These measurements establish
payload growth, not a real-provider latency improvement. This planning pass
reviewed their evidence and the implementation; it did not rerun the probes.

## Assessment of the integration

Pydantic should continue to own Agent execution, tool dispatch, typed capability
activation, tool pairing, summarization and StepPersistence. Core owns access,
staging, effect identity and its context-retention policy. No harness replacement
or dependency upgrade is required for the initial fix.

Confirmed defects and limitations in `pydantic_ai_compat.py`:

1. `IntegralHistorySummary.compact` reintroduces a complete load call/return for
   every historically active skill after native summarization. These bodies
   bypass the native retained-history budget. Repeated summaries preserve them
   again. This is the reproduced growth path.
2. The same method unions historical `ToolAvailabilityDeltaPart.tools_added`.
   This is another retention path: the disclosed set can grow toward the entire
   authorized catalogue. Quantify actual schema growth separately; the skill
   experiment did not exercise hundreds of distinct tool schemas.
3. Active-turn clearing keeps two recent read results by count, but has no
   independent bound on their size or retained arguments. The completed-history
   clearing path excludes only `load_capability`, whereas active clearing uses
   a read-only allowlist. Unresolved effects need one consistent protection rule.
4. The 96 KB trigger measures serialized checkpoint objects, including historical
   instruction copies and metadata. It is not the outgoing request size.
   The history-only digest guard also does not capture changes to model window,
   current instructions or tool schemas.
5. Skipping summarization for the final response preserves its request allowance,
   but does not prove that final request fits. Protected current-turn data can
   itself exceed the usable window. A post-compaction admission check is needed.

The installed native library already exposes `TieredCompaction`, public context
estimation, tool-pair-safe clearing, summarization and context usage reporting.
Its accounting includes current instructions and revealed schemas; Integral's
comment claiming otherwise is stale. Its current before-request history handling
also differs from the compatibility comment referring to Pydantic 2.10. Qualify
the pinned implementation before removing any explicit persistence writeback.

Capability activation is derived from typed history. A retained skill ID with a
discarded body is not a valid fix. Likewise, changing outgoing history without
changing the durable working history allows later recovery to resurrect it.

## Implementation sequence

### 1. Establish failing regression tests and a context inventory

Promote the read, same-skill and rotating-skill probes into native-harness tests.
Capture current instructions once, actual transmitted messages, disclosed schema
bytes, loaded capability bytes, estimated input tokens and summary requests.
Keep checkpoint storage size as a separate metric. Add a rotating-tool fixture
and a fixture that grows the authorized workspace catalogue while executing an
unchanged narrow task. Check initial request metadata as well as loaded bodies.

Use small deterministic fixtures for CI and 100/500-run qualification variants.
Include a genuinely fresh runtime and serialized checkpoint reload on every run.

### 2. Define one context budget and retention policy

Add a small typed policy behind the existing harness compatibility boundary;
avoid spreading independent constants across provider, runtime and tool code.
Resolve its limits for the actual model selected on each request, including
light/heavy transitions and custom provider window overrides.

Proposed starting calibration: compact above 24,576 estimated total input tokens
and reclaim toward 16,384, further constrained by the model window minus output
reservation and safety headroom. These are proposed testable defaults, not
measured optimal values. Establish a conservative window fallback for unknown
models and account separately for images and other non-text inputs. Do not use
a chars/4 estimate as an exact provider-token guarantee.

Measure instructions, loaded skills, revealed schemas, summary, recent dialogue,
tool arguments/results and current input together. Byte/item limits provide
independent safeguards; they do not replace model-token accounting. A larger
context window must not automatically imply a larger latency target.

### 3. Give skill and tool disclosures a bounded working lifetime

Remove the unconditional historical skill reinsertion and tool-name union.
Select retained disclosures by current unresolved work and actual recent use,
under a byte/token allowance. Recency alone cannot override a pending tool
continuation. A framework run ID or a new user message alone does not prove that
the previous task is finished.

Preserve the procedures needed by the current workflow, pending tool calls and
Core-identified approval/recovery continuations. Expire older eligible load
call/return pairs together; keep a bounded summary and authorized history
references so earlier procedures can be rediscovered and loaded again. Never
mark a skill active if its required instructions were discarded or truncated.
Do not silently truncate executable procedures to make them fit.

Apply equivalent selection to all discovery evidence, including typed search
returns, availability deltas and supported legacy discovery metadata. Ensure
the retained tool schemas and dynamic active-skill instructions match the
resulting history. Registration remains authorization-filtered; unloading is
context management, never a permission grant.

Project restored history before the new Agent invocation, after existing
checkpoint/effect validation and staging refresh. During a long run, compact
only at settled tool boundaries, using public capability hooks. Prove same-step
agreement between instructions, provider schemas and dispatch gates; if a public
hook cannot guarantee it, defer that transition to a safe invocation boundary.
Do not mutate Pydantic private activation state or manufacture successful loads.

### 4. Compose one cheap-to-expensive compaction pipeline

Use native `TieredCompaction` with narrow Core policy strategies where required:
expire eligible disclosures, clear old read payloads, then summarize completed
dialogue only when cheaper passes cannot reach the target. Re-measure after all
retained state has been added, not before reinsertion.

Keep current accepted user input intact, a bounded recent dialogue window,
explicit constraints and current work references. Maintain valid call/return
pairs, including parallel batches, retries and typed host events. Apply the
read-only classification consistently to active and completed history.

Large read results should use bounded previews plus scoped retrieval references,
with clear truncation/coverage information. Preserve exact unresolved effect
identity and approval state. Replace older settled mutation detail with compact,
authoritative receipt references only after proving recovery/readback works;
do not erase evidence solely because its tool name is old.

Limit summary input and output as well as frequency. A failed or ineffective
summary must not trigger repeated identical summary calls. Compare reclaimed
budget, not just a serialized-history digest. Summary inference stays inside
existing shared deadlines, accounting and funding controls.

### 5. Preserve recovery and repair existing bloated conversations

Persist the normalized working history through native StepPersistence. Add a
policy-version marker to supported checkpoint/run metadata if needed for
idempotent normalization. Apply the policy on first valid resume of existing
sessions; users must not need to delete their chats to obtain the fix.

Keep historical encrypted evidence and durable effect records available through
the existing scoped retrieval path. Test that evidence about the triggering
step remains recoverable; do not assume every cleared result has already been
saved in an earlier snapshot. Chat display history and live model context remain
distinct. No destructive checkpoint/archive migration is part of this fix.

Recheck permissions, changed/uninstalled skills and current approval outcomes
using existing Core authorities. Do not replay tool calls or resurrect expired
skills when an old snapshot is resumed after a process restart.

### 6. Enforce a final budget check and expose diagnostic measurements

Validate the final prepared request after dynamic instructions and schemas are
resolved, through supported model/capability boundaries. Include summary calls,
retries, final answers and role switches. Use provider token counting where
supported without introducing an unconditional extra network call per step;
otherwise use calibrated conservative estimation and independent size limits.

If irreducible current input or required pending state exceeds the safe budget,
stop before dispatch with an actionable, resumable outcome. Never silently drop
approvals, repeatedly retry an oversized request or treat compaction as new
permission to execute a write. This is the explicit overflow behavior, not the
normal operating path.

Record content-free sizes/counts, compaction reason, reclaimed budget, summary
duration, loaded skill/tool counts, provider-reported input tokens, pre-dispatch
time, first-token time and first-tool time. Separate provider waiting time from
Core preparation and summary overhead. Extend existing observations rather than
introducing a second telemetry store.

### 7. Qualify correctness, growth and responsiveness

| Test | Required result |
| --- | --- |
| Same task and rotating skills, 100 then 500 runs | Every request fits policy; steady-state size plateaus rather than tracking total skills visited |
| Rotating tools and growing authorized catalogue | Disclosed schema size stays bounded; unused Apps do not fill the request |
| Oversized reads, arguments and user input | Bounded read projection; protected input retained or explicit pre-dispatch overflow |
| Pending approvals, writes, partial failures and resume | Exact effect identity preserved; no lost proposal or duplicate mutation |
| Old bloated checkpoint and process restart | First valid resume normalizes once; discarded context does not return |
| Light/heavy switching and intervening light turns | Dialogue appended once; operational context fits the selected model |
| Failed summary, cancellation and request exhaustion | No compaction loop; final response reserve and deadlines respected |
| Model adapters and parallel tool batches | Valid typed pairing; instructions, schemas and dispatch availability agree |
| Different threads/principals/workspaces; revoked access | No widening of context or recovery authority |
| Long-lived user constraints and follow-up tasks | Relevant constraints survive; expired procedures can be loaded again |

Assert explicit per-request limits and bounded skill/schema inventory, not just
an average or last-run snapshot. Measure the beginning, middle and end of a
fixed-workload soak; tolerate only the configured bounded working-window cycle.
Add property-style checks for valid pairing, deterministic/idempotent projection
and persistence round trips.

Then run real local and Dev browser smoke with the V75 Inc. workspace and the
configured GPT-4.1 light / DeepSeek v4.1 flash heavy pair. Use matched tasks and
multiple samples, retain input-token and latency distributions, and disclose
provider variance. Synthetic payload tests cannot establish actual speedup.

### 8. Delivery and maintenance

Implement in the existing PR #171 worktree, in this order: reproductions,
retention lifecycle, budget pipeline/recovery, observability, qualification.
Primary implementation areas are `harness/pydantic_ai_compat.py`, `runtime.py`,
the provider resume boundary, model observations and native-harness tests. Split
policy into a focused module if needed; retain the existing compatibility facade.

Document the contract in `docs/backend/ai-chat.md`, workspace-agent-profile docs,
resident-harness guidance and configuration/qualification documentation. Add a
stable bounded-context invariant. Preserve I-HARNESS-01/02, I-SKILL-SCOPE-01,
I-SKILL-02, I-RET-01, I-CONTEXT-01, I-WORK-01/02/05/07/08 and graph placement rules.
No schema/authority weakening or new resident harness is proposed.

Run full repository gates, PostgreSQL recovery tests and browser smoke; resolve
the separately observed frontend dependency-audit CI failure before calling the
PR merge-ready. Keep the PR open until qualification is complete. Dependency
upgrades must rerun these regression fixtures; source comments and assumptions
must describe the pinned library behavior.

## Decision and limits

Proceed with a bounded working-context lifecycle on the existing native harness.
Do not fix this by raising limits, clearing every conversation or preserving all
historical skills indefinitely. The defensible completion claim is that tested
context classes are bounded, recovery preserves correctness and regressions are
gated. It is not that arbitrary tasks have constant latency or infinite history
can remain fully present in a finite context window.

Public references, checked alongside installed source:
- [Pydantic Harness compaction](https://pydantic.dev/docs/ai/harness/compaction/)
- [Pydantic capabilities](https://pydantic.dev/docs/ai/capabilities/overview/)
- [Conversation search](https://pydantic.dev/docs/ai/harness/conversation-search/)
