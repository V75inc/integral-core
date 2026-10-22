# Pydantic AI Harness integration review and optional model roles

Review scope: native model construction, provider transport, role routing,
account funding, dispatch admission, budgets, durable execution and UI observability.
Baseline: PR #170 at `ce38c714`. Dependencies remain Harness 0.36.0 and Pydantic AI
Slim 2.54.0. No alternate resident binding or Harness upgrade was introduced.

## Integration assessment

Integral uses Pydantic AI's `Agent`, typed outputs and event execution, with Harness
Skills, Planning, StepPersistence and context management through its compatibility
boundary. Core owns authenticated identity, workspace policy, staged effects,
receipts and shared-store fencing. This remains the appropriate separation:
framework capabilities implement model execution; they cannot confer Core authority.

The model adapter constructs Pydantic AI's OpenAIChatModel with its LiteLLMProvider,
backed by an owned in-process LiteLLM SDK transport. Distinct light/heavy routes
are injected through that existing public model boundary. The output path retains
settled-answer validation buffering. Operational history and effects continue to
use the existing StepPersistence/manifest path; conversational output does not
advance the operational checkpoint. No SubAgents capability, A2A machinery or
parallel resident was added to obtain two model roles.

Public references: [Pydantic AI Agents](https://pydantic.dev/docs/ai/core-concepts/agent/),
[Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/).
Compatibility is qualified against the installed pins, not inferred solely from
current online documentation. Framework imports remain behind `pydantic_ai_compat`.

## Findings addressed

| Finding | Adaptation |
| --- | --- |
| Both context paths selected the same model | Optional owner/deployment light and heavy roles, using the default profile as heavy |
| A second model could replace task-local limits and reschedule a deadline later | Separate per-model settings from a frozen, monotonically tightening shared budget and deadline |
| Logical SDK usage alone omitted physical transport retry counts | A per-attempt guard runs before admission/dispatch intent; observed tokens accumulate by physical request identity |
| Worker recovery could otherwise restart a pair with fresh allowance or changed settings | Dual-mode run metadata retains a nonsecret route-plan fingerprint and deadline; recovery restores request/known-token usage and resumes operational context |
| SDK wrappers could hide UsageLimitExceeded inside an exception cause | Compatibility classification walks typed causes and preserves the harness-limit category |
| Per-turn SDK clients had no explicit owner cleanup | The native streaming boundary closes its created clients on completion, failure or cancellation |
| Activity named models but did not distinguish roles | Bounded light/heavy role metadata survives encrypted observations, public event normalization and transcript replay |

The durable route fingerprint excludes plaintext keys and hashes route material;
only the fingerprint and deadline are added to Core run metadata. Existing physical
observations retain encrypted route attribution. Field-only CAS preserves unrelated
run metadata and uses the work kernel's narrow identity-cache invalidation protocol.
A recovered bound run rejects a different pair or a change back to single mode.
A new accepted run can use updated configuration. Operator-managed credentials must
continue to version `INTEGRAL_NATIVE_CREDENTIAL_REF` when their generation changes.

## Configuration and behavior

Single model is unchanged by default, including the existing adaptive-context switch.
Automatic mode requires an owned Main/heavy profile and a valid owned Light profile.
Different Providers or two profiles of the same base model are supported. Reasoning,
output, sampling and per-request timeout settings remain profile-specific. Run limits
on participating profiles combine conservatively with deployment ceilings. Heavy-only
and explicit profile overrides do not inherit an unused light profile's limits.

Routing mode and assignments are revision-checked and saved with the default credential
projection in one CAS. Primary legacy configuration changes return to single mode;
voice-only changes retain roles. Assigned profiles cannot be deleted until reassigned
or cleared. Known models without structured-tool support cannot be selected for the
automatic pair; custom/unknown capability metadata remains unverified rather than
being advertised as supported.

Known operational contexts, approvals, attachments, background work and recovery skip
light inference. Eligible conversation gets one bounded light pass. A typed context
request or known transient provider failure can escalate once. Authority, cancellation,
quota/admission, authentication and configuration failures do not cause a fallback that
could bypass their boundary. Heavy execution never automatically downgrades.

Both roles use current workspace-owner funding and existing per-dispatch admission.
Strict BYOK cannot borrow platform credentials. Deployment-owned pairs use deployment
credentials for both roles. There is no implicit mixing of another user's keys or a
personal light key with deployment heavy funding.

## Operator configuration

Existing `INTEGRAL_NATIVE_MODEL` remains the deployment Main/heavy model.

```dotenv
INTEGRAL_NATIVE_ROUTING_MODE=light_heavy
INTEGRAL_NATIVE_LIGHT_MODEL=openai/gpt-4.1-mini
INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS={"max_tokens":2048}
INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS={"max_tokens":8192}
```

Reasoning options may be included when the selected model advertises support. Both
JSON settings objects use the same typed validator and ceiling checks as saved
profiles. Unset role settings retain provider/local defaults. The workspace owner's
BYOK profile configuration takes precedence under existing hybrid policy; platform-only
roles remain operator controlled. Reset mode to `single` to disable dual routing.

Settings shows the effective deployment model, routing pair and validated per-role
tuning as server-managed information. ENV credentials are never returned or imported
as personal Providers. Platform-only configuration is read-only; hybrid policy permits
owned profiles to supersede the deployment default, while strict BYOK cannot borrow it.
An unusable owner key is not treated as funding a local deployment pair.

The routing readout separates mode and source badges, Light/Heavy model cards,
labelled reasoning/output/timeout settings and shared turn ceilings. In hybrid
mode, ENV is a fallback: the workspace owner can use the prominent Add your own
model action and activate an owned profile without changing server ENV. Only an
explicit platform-only policy disables those overrides.

## Qualification and limits

Regression coverage includes atomic role selection, foreign/stale/missing assignments,
assigned-profile removal, fixed/heavy-only overrides, same-base-model profiles with
independent settings, platform funding isolation, physical retry limits, deadline
non-extension, durable route recovery, current admission pointers, wrapped budget
failures, safe role replay and owned SDK client cleanup on completion/cancellation.
Existing Harness adapter, typed output, checkpoint, approval and disclosure tests remain
required. PostgreSQL checks cover the nested run-metadata CAS and shared execution lane.

Timing distinguishes light pass duration, nonstreaming light response availability,
escalation, heavy-path start, first heavy answer text and user-visible delivery/settlement.
A nonstreaming light response timestamp is not a measured provider time-to-first-token.
Unknown provider token usage/cost remains unknown in durable accounting; request counts
still bound every attempt and mandatory host budget holds retain their normal semantics.

No paid-provider model pair has been used for this change. Offline contract tests and
synthetic browser fixtures establish routing and safety behavior, not model quality or
production latency gains. Live p50/p95 latency, escalation rate, task completion and
cost comparisons remain pending approved test credentials and bounded usage. Automatic
routing remains opt-in regardless of those results.

## Dependency qualification

The existing PR dependency gate identified an OpenTelemetry API advisory. The frozen
lock advances only `opentelemetry-api` from 1.45.0 to 1.45.1. The current frontend
audit also requires DOMPurify 3.4.17 instead of 3.4.16. These narrow security patches
do not change the pinned Pydantic AI, Harness or jvspatial versions. No advisory
suppression or additional policy exception is introduced.
