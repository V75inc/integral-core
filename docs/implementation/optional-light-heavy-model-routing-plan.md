# Optional light/heavy model routing — implementation plan

Status: implemented in PR #170; qualification evidence is recorded in the integration review and PR.
Baseline: PR #170, commit `ce38c7144c7d858e0710c778cbf5fd7a1e5523af`.
Verified installed dependencies: `pydantic-ai-harness==0.36.0`,
`pydantic-ai-slim==2.54.0`. No dependency upgrade is proposed.

## Outcome and architecture

Allow an owner to choose either Single model or Automatic (light + heavy).
Keep one resident Integral harness, principal, work item, approval boundary and
operational checkpoint. Light/heavy describe execution roles; profiles can use
different providers, or the same base model with different reasoning settings.
No subagent delegation or A2A surface is needed.

Pydantic AI accepts an explicit model and model settings for agent execution;
its Harness composes capabilities around that agent. Integral can therefore reuse
its existing model adapter and route resolver for the two execution paths.
References: [Pydantic AI Agents](https://pydantic.dev/docs/ai/core-concepts/agent/)
and [Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/).
Latest online examples are not proof of the pinned dependency's API; adapter
contract tests will qualify the installed versions. Imports stay behind
`pydantic_ai_compat.py`.

### Verified starting point

- `_try_light_turn` already runs a bounded conversational pass and can return a
  typed `OperationalContextRequest`; `_prepare_current` builds the operational path.
- Both call `_build_turn_model`, which currently resolves the same workspace-owner
  default or explicitly selected conversation profile.
- Light execution has no workspace tools. A simple reminder produces a reviewed
  proposal through the existing host service rather than activating work directly.
- Host state already bypasses the conversational path for approvals, attachments,
  operational continuations and other structured operational context.
- PR #170 provides encrypted Providers, reusable model profiles, reasoning settings,
  profile selection, dispatch admission, usage deductions and timing markers.
- `_build_turn_model` currently replaces the task-local settings and can reschedule
  the deadline. Splitting routes requires fixing this boundary first so a second
  model cannot reset budgets or extend a turn.

## 1. Configuration and compatibility

Use the existing default profile as the Main/heavy profile. This avoids a third
competing default selection. Add `routing_mode` (`single` or `light_heavy`) and
`light_profile_id` to the existing private `UserModelCredential` Object. The heavy
assignment remains `active_profile_id`; the API may expose it as
`heavy_profile_id` for clarity.

Introduce a typed routing configuration request accepting mode, light and heavy
profile IDs. Validate same-owner access to both profiles and their Providers,
capability support, readable credentials and deployment policy before one
revision-checked credential update. Heavy activation, the legacy projection and
routing mode must change atomically. Saving a profile alone never enables routing.

- Existing records default to Single model. Preserve current adaptive-context
  behavior and the global `INTEGRAL_NATIVE_ADAPTIVE_CONTEXT` control.
- Enabling Automatic requires both valid assignments. Legacy credentials may be
  explicitly promoted using the existing idempotent profile snapshot mechanism.
- Single model ignores the light assignment. Retain it as an inactive preference
  for easy re-enablement, with its deletion dependency explained in the UI.
- Selecting a new default changes the Main/heavy assignment and preserves the
  optional light role only if the resulting configuration remains valid.
- Block removal of assigned profiles until reassigned or explicitly cleared.
  Provider removal remains blocked while profiles reference it.
- Old primary-model/key API writes atomically return to Single model when they
  invalidate profile selection. Voice-only edits preserve routing and assignments.
- Profile/Provider changes are authoritative on the next accepted turn. Recheck
  route identity, revisions, owner and policy before every physical dispatch.
  An incompatible mid-turn change fails safely rather than silently rerouting.

Support deployment-managed parity: proposed
`INTEGRAL_NATIVE_ROUTING_MODE=single|light_heavy` and optional
`INTEGRAL_NATIVE_LIGHT_MODEL`, with existing `INTEGRAL_NATIVE_MODEL` as Main/heavy. Proposed typed JSON settings
`INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS` and
`INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS` reuse the same generation-setting validator
and deployment ceilings; existing local-model defaults remain compatible.
Expose safe effective mode/model summaries. Platform-only remains operator managed;
strict BYOK never silently borrows a platform route. Resolve one funding policy for
both roles rather than mixing another member's or owner's credentials.

Files: credentials model; AI configuration schemas/service/API; legacy credential
service; config; `.env.example`; deployment documentation; route resolver.

## 2. Explicit role resolution and routing

Add a small typed route-plan contract containing mode, authorized light/heavy
routes, configuration revision and safe decision codes. Build it once from current
workspace authority for an accepted turn. Pass an explicit execution role to the
existing model-construction boundary; do not change global defaults to switch models.

| Situation | Execution |
| --- | --- |
| Single model | Current behavior, using one selected profile |
| Known operational host context, files/images, approvals, checkpoint recovery, scheduled/background work | Direct Main/heavy; no light classifier call |
| Eligible ordinary chat without operational context | One bounded light pass |
| Light returns a valid conversational answer | Finish through existing validation/publication path |
| Light requests operational context or returns an unusable protocol result | Escalate once to Main/heavy with the original accepted request |
| Light has a transient provider failure | At most one heavy escalation if authority and remaining budget permit |
| Cancellation, stale authority, policy/admission/quota rejection or invalid configuration | Preserve the existing stop/recovery behavior |
| Heavy fails | Existing retry/error handling; no automatic downgrade to light |

Keep the existing host guards and typed escalation. Message length and keywords
must not grant authority or replace the operational-context guard. Plain text can
still require a light pass followed by escalation; document that extra round-trip
and measure it rather than promising every operational request will bypass it.
Do not add another model call solely to classify each request.

The light pass keeps no general workspace tools. Preserve its reviewed simple
reminder contract. Escalation carries the accepted user input and bounded public
conversation; it does not execute light-authored tool names, reuse private reasoning,
or turn light prose into authority. Heavy restores its canonical operational
checkpoint and effect identities. No light/heavy ping-pong within a turn.

Files: `adaptive.py`, `model_route.py`, `contracts.py`,
`pydantic_ai_provider.py`, compatibility adapter, accepted-input/continuation code
only where necessary for trusted route selection.

## 3. One budget, admission and durable continuity

Freeze a shared turn budget and absolute deadline before the first model dispatch.
The effective ceiling is the minimum of deployment ceilings and explicit run limits
on the configured participating profiles. Show this effective limit in Settings;
a lower profile limit must not disappear when the model changes.

Reasoning effort, output-token allowance, sampling, context and request/idle timeout
remain specific to the selected model profile. A per-request timeout never extends
the shared wall-clock deadline. Every actual request, retry, light pass, escalation
and tool execution counts toward the same cumulative allowance. When budget is
exhausted, produce the existing bounded handoff/error rather than granting a fresh
heavy budget.

Refactor `TurnLatency.model_settings`/`turn_usage_limits` into distinct current-model
settings and shared budget state. Record all light usage, including retries and
failed attempts when observable, rather than assuming escalation cost exactly one
request. Keep `WorkModelAdmission` and receipts on both roles, including pricing of
every physical dispatch and current authorization checks.

Persist only the trusted non-secret routing decision needed for execution audit and
resumption in the existing work/run structures. Approvals and interrupted operational
work resume the heavy path and retain the same effect owner/checkpoint. Replaying
public events performs no inference. Reject incompatible configuration changes on
resume; do not discard a checkpoint or rerun completed tools to accommodate them.
Qualify provider changes against the adapter's typed history handling; keep private
reasoning and provider-specific signed content out of cross-model handoffs.

Invariants preserved: I-GRAPH-01/02 (no new floating Nodes; private configuration
remains Object), I-CRUD-01, I-SUBSTRATE-01/I-EXT-01, I-GOVERN-01,
I-HARNESS-01/02, I-WORK-01/02/03/05/07/08/09, I-APPROVAL-01/02/05,
and I-CHAT-01. No invariant changes or graph migration are proposed.

## 4. Settings and conversation controls

Add a compact Model routing section within AI Models, using existing padded
editors and shared outline brain icons.

- Default: Single model, with the existing Main model selection.
- Optional: Automatic (light + heavy), revealing Light model and Heavy model
  profile selectors. Heavy is explicitly identified as the existing default.
- Each selected profile shows its Provider, model, reasoning setting and output
  limit, with an Edit settings action. Keys remain in Providers.
- Explain Light as quick conversation and Heavy as operational/complex work.
  Allow two profiles using the same underlying model with different settings.
- Show effective shared limits, owner/deployment control, missing configuration
  and clear validation errors. Test provider access remains separate from proving
  model inference/tool support.
- Save both assignments and mode together. Saving neither activates partial routing
  nor changes the workspace configuration before confirmation.

Extend conversation selection with Use workspace routing, Heavy only and Specific
profile. Heavy only bypasses the light pass. A specific profile uses that model for
both existing context paths and does not borrow the configured light model. Existing
`model_profile_id` overrides retain their fixed-profile meaning. Apply changes only
to an idle conversation through field-only CAS; never overwrite active-work pointers.
The API must enforce the same owner/funding rules as the default configuration.

Cover desktop/mobile, keyboard interaction, owner/member/platform-managed states,
empty profiles, missing capabilities, stale saves and failed provider tests.

## 5. Visibility and measured acceptance

Add safe route-role/model summaries to existing activity events and replay, with
bounded decision codes such as conversation, operational_context, explicit_heavy,
and light_escalation. Show concise statuses such as “Working on that for you” and
“Taking a closer look” when escalation occurs. Preserve explicit model usage and
cost where available; do not imply unreported costs are zero. No prompts, secrets,
private reasoning or raw tool content are added to activity.

Extend existing timing to distinguish light pass, escalation and heavy first output,
while retaining request-to-first-published-text, first operational tool and total
settlement. Aggregate usage across models without hiding which role incurred it.

Qualification set: greetings/general questions, supplied-text writing, standalone
reviewed reminders, ambiguous follow-ups, plain-text operational requests, structured
record operations, attachments/images, approvals/recovery and scheduled tasks.
Compare Single model against Automatic using the same requests and candidate pair.
Report p50/p95 first published text, first operational tool, completion time, requests,
tokens/cost, escalation rate and task correctness. Keep cold and warm measurements
separate. No production latency claim follows from mocked or embedding-only timings.

Suggested rollout criterion: faster/cheaper routine turns, no material task-quality
regression, no extra light request for known operational contexts, and no authorization,
approval or duplicate-effect failures. Actual benefit depends on the chosen models;
Automatic stays opt-in even if qualification succeeds.

## 6. Implementation sequence and delivery gates

1. Configuration/schema/API contracts, atomic selection, defaults and legacy tests.
2. Explicit role resolution, shared budgets, dispatch admission and continuation tests.
3. Settings/conversation UI, safe activity projection and replay tests.
4. Provider-pair evaluation, full regression gates, then update PR #170 and leave open.

Required regressions include: missing/deleted/foreign profiles; same-model profiles
with different reasoning; cross-provider routes; operator/platform/strict-BYOK policy;
concurrent saves and changes during dispatch; single-mode compatibility; one escalation
maximum; transient failure versus policy rejection; all retries charged; no deadline
extension; cancellation in either pass; approval re-entry; restore/replay without
re-execution; member access changes; cross-provider history; and account deletion.

Extend `tests/native_harness/test_adaptive_context.py`,
`tests/native_harness/wp_04/test_model_route.py`, the configuration and latency
tests, model-admission contracts, and the Settings/conversation interaction tests.

Run `make verify`, CI-faithful smoke, relevant PostgreSQL atomicity/admission/
approval tests, frontend interaction suite and rendered browser checks. Run live paired
model evaluations only with approved test credentials and a bounded usage allowance;
otherwise report that evidence as pending. Recheck GitHub checks after pushing.

Rollback: switch routing mode to Single model, retaining the Main/heavy profile,
Providers, personal data, conversations and operational checkpoints. No release,
consumer pin change or deployment belongs to this patch.

The user approved implementation and the Harness integration review on October 22, 2026. Update PR #170 after qualification and leave it open.
