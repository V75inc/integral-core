# Adaptive harness: greeting latency and recommended routing

## Recorded evidence

Read-only inspection of local run `1abc1b09-7c53-4fbf-9441-d270074a4b62`
confirms the authored input was `hello there`. No new inference or records were
created for this investigation.

- User message to saved assistant message: **35.735 seconds**.
- Core run duration: **35.220 seconds**.
- Run start to first model dispatch: **9.472 seconds**. This interval includes
  preparation/admission; there is no per-phase trace to attribute it precisely.
- Four recorded physical requests, all responded, approximately **13.145
  seconds** combined provider intervals. These intervals are not time-to-first-token.
- First request: 18,495 instruction characters, 3,735 tool-schema characters,
  4 visible tools, 4,552 provider-reported input tokens. The human utterance
  itself was eleven characters; instruction overhead dominates that request.
- The assistant searched capabilities for greeting/orientation and read page
  context. Those operations were unnecessary for this input.
- Two overlapping framework step intervals span about 10.85 seconds during
  discovery; do not add them together. Search ranks both skills and tools using
  local embeddings. Phase-level measurements are needed before attributing all
  of this interval to embeddings.
- A fourth request has only 369 instruction characters and no tools. Subsequent
  source inspection identified the post-answer conversation-title inference
  call; it is presentation work, not operational reasoning.

This is one local observation, not a production percentile or a promised future
response time. The provider route was `ollama_chat/deepseek-v4.1-flash`.

## Source findings

`PydanticAIChatProvider._prepare_current` prepares connector capabilities,
workspace skill overlays, Core skill files, tool adapters, session identity and
checkpoint history on every ordinary model turn. Model-side deferred tool
loading does not defer that host preparation.

`build_native_runtime` enables initial discovery for ordinary turns.
`IntegralToolDisclosure.after_model_request` can retry an otherwise complete
conversational answer if discovery has not happened. The current page-context
exception avoids forcing search after page-only reads, but a simple greeting
does not require either kind of read.

`build_capability_search_tool` has run-local result/vector caches. Skill vectors
are recomputed for a new semantic ranking, and tool vectors are rebuilt across
new runs. `embed_query_text` executes individual encodes in worker threads;
catalogue-wide concurrent calls should be measured against batched encoding and
a bounded content-addressed cache.

The provider buffers candidate text until validation settles. That protects
against publishing rejected/retried output, but means the user sees none of
those candidates while discovery and retries run. Do not remove that protection
as a general latency fix.

## Recommended contract

Use **one resident harness with progressive preparation**, not a separate router
model followed by an answering model. Modes select context and capability
availability; they never confer authority.

| Path | Examples | Preparation |
| --- | --- | --- |
| Conversation | Greetings, thanks, general explanation, writing from supplied text | Small identity/disclosure policy; bounded public dialogue; no workspace reads or catalogue |
| Operational | Current workspace facts, current screen, record lookup, saved deliverable or change | Load needed scoped capability catalogue/skills; existing broker and receipts |
| Continuation | Concrete approval decision, approved build, background task, interrupted effect recovery | Enter operational path directly with authoritative host state |

Ordinary interactive turns start with a lightweight model request. The same
model may answer directly or emit a typed, non-mutating `request_operational_context`
signal. That signal carries only a need/reason, not a user ID, workspace authority,
or approval. The host loads current scoped context and continues within the same
accepted turn and accounting lineage. Permit one escalation per turn; no loop
between modes. Requests for current state must escalate before making factual
claims. Ambiguous follow-ups such as `yes` consult bounded current host state;
never interpret them as fresh permission via a keyword classifier.

Keep authentication, workspace membership, durable WorkItem/run identity,
encrypted transcripts, cancellation, metering/model admission and instruction
protection on both paths. On escalation, refresh capability/permission state;
tool execution retains live policy checks, exact approvals and idempotency.
An uncertain prior effect blocks replay even if the new text looks conversational.

Use the configured model/funding route initially. Separate fast-model selection
or provider reasoning controls are optional later settings, only where supported
and explicit; do not silently change BYOK or bill a different provider. Short
messages can be complex operational requests, so message length is not a mode
decision. A greeting followed by a command must still be able to escalate.

## Implementation order and qualification

1. Add content-free phase spans: acceptance/queue, common authority checks,
   profile/catalogue, skills, history, discovery/embedding, physical dispatch,
   first provider token, settled answer and persistence. Record mode, escalation
   reason, model calls and tool calls.
2. Remove unconditional discovery for conversational answers; add the typed
   escalation path and bounded public context. Keep current heavy execution
   unchanged behind escalation until its authority/recovery matrix passes.
3. Defer catalogue/skill materialization and operational checkpoint restoration;
   keep stable session identity and isolated operational resume pointers. Light
   replies must appear in subsequent public history without overwriting an
   unresolved operational checkpoint. Changing mode alone must not lose history
   through the current capability-version namespace calculation.
4. Cache static skill/catalogue vectors by content and embedding model version,
   with size bounds and tenant-safe keys for private overlays. Filter current
   authorized catalogue before ranking; cached rankings cannot supply authority.
   Batch encoding and avoid duplicate query encoding if measurements confirm it.
5. Qualify greeting/thanks/general advice, greeting plus mutation, multilingual
   requests, current-page reads, workspace switching/revocation, pending approval
   `yes/no/hello`, expired/rejected proposals, prior partial failures, provider
   outage/cancellation and no duplicate effects on escalation/retry.

For an ordinary greeting without unresolved operational state, require one
answer-generation request, zero capability/record/page reads, and no synchronous
operational summarization. Measure cold/warm time-to-first-visible-answer and
completion against the same configured provider; report provider latency
separately.

## Implementation and local qualification

The conversational pass is implemented in `harness/adaptive.py`. It accepts
plain text or the typed `request_operational_context` output, never workspace
function calls. Both modes share the same model-route/admission factory.
Escalation remains in the same Core run and counts its input/output and request
against the operational turn limits; accounting includes both model namespaces.
It neither emits a fabricated approval nor automatically executes a change.

Authoritative pending approvals, incomplete design, prior non-successful
operational runs, host continuations, attachments and referenced resources go
directly to the existing operational harness. Durable work additionally must be
an ordinary interactive root chat turn to use the conversational path.
Both current `http` durable and legacy `chat` interactive origins are supported.
`INTEGRAL_NATIVE_ADAPTIVE_CONTEXT=0` restores the prior preparation path.

Recent dialogue discovery is bounded to 13 candidate rows through the maintained
thread lookup cache. Messages are hydrated only through canonical `CONTAINS`
edges, then projected to at most 12 plain messages / 12,000 characters. Host
instructions, reasoning and tool payloads are excluded. Operational history
gets subsequent public dialogue without replacing its checkpoint. Missing
dialogue triggers model-requested escalation, not inferred authorization.
Actual database work for the sorted bounded candidate query still depends on
the backend/index; the row limit is not a production physical-work guarantee.

Light replies omit automatic title inference and retain the existing private
envelope normalizer and instruction-copy guard. Cancellation, current scope,
WorkItem effect boundaries and model accounting remain enforced. This does not
prove general semantic prompt-injection immunity.

### Live local measurements

The configured `ollama_chat/deepseek-v4.1-flash` route was used through ordinary
authenticated HTTP chat, with no new credentials/funding route. The validation
conversation is `n.ChatThread.292a4d6db0ef4c50b541aee4`.

| Input | End-to-end seconds | Physical model calls | Result |
| --- | ---: | ---: | --- |
| hello there | 13.458 | 1 | Conversational; no workspace/page tools |
| thanks | 1.263 | 1 | Conversational |
| What is 12 times 7? | 1.129 | 1 | Conversational; correct answer 84 |
| Read-only current App question with page snapshot | 19.829 | 3 | Escalated; verified CRM and canonical App link |
| Read-only accessible App inventory | 28.391 | 5 | Escalated; returned the existing three Apps |
| Bonjour ! | 1.543 | 1 | Conversational; French greeting |

The final shared-transport build was checked in a second conversation,
`n.ChatThread.b55f365fc7454d34b721172b`: greeting 11.172 seconds (light),
ambiguous `yes` 2.242 seconds (light clarification, no approval or tools),
greeting plus read-only App inventory 27.022 seconds (operational), and French
read-only inventory 13.062 seconds (operational). The latter reused the recent
operational result and explicitly disclosed that it had not refreshed it.
The browser rendered these replies and captured no console errors.

This explicit HTTP-scope probe also exposed a pre-existing distinction:
`integral_get_scope` binds to `users.get_my_scope`, whose `active_workspace_id`
is the user's saved preference (Acme), while the test thread/header and App
reads were bound to Business Admin. The assistant disclosed the discrepancy;
the App results matched the request scope and no cross-scope change occurred.
Clarifying the scope tool's preference-versus-invocation contract remains a
separate follow-up; adaptive context does not resolve that ambiguity.

No live App/Track/Entry was changed by these cases. The light requests carried
2,106 instruction characters versus 18,495 in the earlier recorded greeting.
The cold greeting's physical provider interval was 10.058 seconds; common host
preparation/admission/persistence comprise the remaining end-to-end interval.
Warm conversational calls had provider intervals 0.761–1.046 seconds. These are
individual local measurements, not a p95/SLA or a matched cold-start benchmark.

An initial smoke incorrectly remained heavy because the legacy origin was not
allowlisted. Its greeting took 42.725 seconds and performed onboarding reads.
The guard was corrected and both origins gained regression coverage. This failed
qualification is retained, not counted as successful adaptive behavior.

Source tests cover typed escalation, plain answers, host-routing cases,
multilingual inputs, ambiguous short replies, scope refusal, bounded history,
forged lookup-cache membership, existing checkpoint preservation, no title call,
private-envelope normalization and the existing native stream/recovery suites.
Operational mutation, approval and execution-fence cases run in isolated
databases; the live provider cases remain read-only. PostgreSQL qualification
includes the adaptive cases, effect fences, approved design execution and model
admission/receipt storage. Configured formatting/lint/types/guards pass. Retained
logs under `/private/tmp/integral-browser-latency` distinguish source suites,
provider observations, failed baseline and successful live cases.

The default-selected full backend run completed with exit 0. Its opt-in
PostgreSQL/infrastructure/domain/slow exclusions remain exclusions, not passes.
That broad run began during implementation; subsequent shared-transport and
legacy host-continuation changes passed the final targeted suites and configured
hooks. It is not release CI or an exhaustive final-artifact production matrix.
The final local API wheel SHA256 is
`eb974eb9b6d0ba3a509bc0f7d71e555ee4706c272571ffd93eba8eb641029d84`;
all 13 changed backend production file hashes match the artifact manifest.
Core reports `0.1.1rc18` and jvspatial `0.2.2`. The existing 59 App/Track/Entry
rows retain their pre-change content digest.

The remaining catalogue-embedding cache/batching and finer per-phase tracing are
separate follow-up optimizations; operational discovery is still relatively slow.
No claim of every provider, language, production load or failure permutation is
made. The local changes are uncommitted and have not been pushed.

The light path retains the verified user-time context and escalates current facts
that require retrieval. The final clock/host-continuation regression suite passes.
The browser Home composer also completed a greeting with one physical model call
(run `24b2b4d4-1756-4473-8a2f-e309c91109e8`, 7.723 seconds Core run duration).
The earlier 2,106-character measurement precedes the trusted-clock addition.
