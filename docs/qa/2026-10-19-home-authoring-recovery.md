# Home authoring recovery qualification

## Scope and evidence

Baseline: Core `b425ee4faeb49373a67b4fa181a31f818ab982a4` (`0.1.1rc24`).

The observed V75 turn made five App update calls, exhausted generation limits,
and left no Home or pending proposal. The public transcript did not retain the
original arguments/results. Its exact original validation error and the precise
budget counter therefore cannot be reconstructed from that transcript.

Two independently reproduced defects are repaired here:

- `integral_update_app.home` advertised an opaque object instead of the actual
  server composition schema. Invalid shapes passed the advertised schema.
- Pydantic shape errors echoed submitted values. Broker failures also appeared
  as completed tool calls, and durable chat discarded their useful error category.

The Home schema now derives from `AppHomeComposition`, including nested widgets,
grid and sources, while preserving `{}` as the package Home reset. The SOP shows
a structural example, focuses discovery, and repairs named validation failures.
Shape feedback gives bounded schema-owned field paths without submitted values.
Source denial remains a denial and creates no proposal.

Tool failures retain a fixed-vocabulary diagnostic through events and transcripts.
Raw arguments, tool outputs, exception text and reasoning remain excluded from
public persistence. The UI displays each tool's retained outcome, including after
reload; a completed call does not inherit a later turn error.

The last model request within the framework's live request allowance is reserved
for a tool-free handoff. A provider ignoring that choice cannot dispatch another
tool. Hard request/token/tool limits remain enforced. Exhaustion copy permits
continuation in the same chat and requires checking saved work/pending approvals.
No automatic continuation, write replay or approval is introduced.

## Companion HR package contract

The Business Human Resources package also lacked any declared query eligible
for Home widgets. Its companion 1.9.5 patch adds a cursor-only, bounded overview
query with explicit selected fields. Banking, identity, contact and document
fields are excluded. This stays in the owning Business App; Core's generic-read
boundary remains enforced. This independently confirmed package gap does not
establish the unrecoverable argument payload of the original production turn.

## Affected invariants

- I-GRAPH-01: existing App, thread and staging creation/attachment paths remain
  authoritative; the fix introduces no new persisted Node or edge.
- I-GRAPH-02 and I-HARNESS-01: private execution details stay in scoped encrypted
  execution storage; public event/transcript additions accept only fixed copy.
- I-ROLE-01: current source and App configuration authority still apply at
  proposal and approval. Approval is required before applying Home changes.
- I-CHAT-01: user-facing validation feedback contains schema labels, not raw
  record IDs or invalid submitted values.
- I-WORK-07: the response reserve reads the framework's live allowance; it does
  not modify admission, metering, budget holds or dispatch fencing.

## Local behavior checks

- Reproduction was RED on baseline: opaque schema and private-value echo.
- Scripted `FunctionModel` runs through the broker tool wrapper, capability
  adapter, dispatch and Home stager: malformed input, safe corrective feedback,
  then exactly one pending approval in three model responses. Authority/storage
  boundaries are mocked in this deterministic model test; no provider is called.
- Real HTTP/storage integration: proposal leaves Home absent, approval saves it,
  readback returns its widget, no analytics dashboard is created, repeat approval
  is refused.
- Source denial creates no approval; extra-key/private-value sentinels do not
  appear in shape feedback or public diagnostic projection.
- Both a compliant model and one ignoring final-response tool choice are tested;
  the latter cannot execute a fourth tool under a four-request allowance.
- Browser smoke uses isolated Chrome and disposable local JSON data: Home tab,
  description, widget and zero dashboards render. A seeded durable failure shows
  its validation category separately from a completed read and the turn error.
  Reload preserves these outcomes and the same-chat continuation guidance.
  This UI fixture is synthetic, not a replay of the production model.
- The local development server logged its existing JSON-store retention warning;
  the browser logged a development WebSocket connection warning. API reads and
  rendered Home/chat content succeeded. Transactional storage is qualified in the
  separate PostgreSQL lane.

## Verification

Final command results are recorded in the pull request. Regenerating
`make capability-map` produced no generated-file changes: the capability roster
and bindings are unchanged.

No live DeepSeek/Ollama provider replay, production data mutation, release,
version bump or deployment is part of this qualification.
