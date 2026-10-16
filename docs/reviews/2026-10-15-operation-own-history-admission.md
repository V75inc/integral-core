# Own operation history admission — 2026-10-15

Status: source qualified locally; internal admission, not public recovery integration. Worktree:
`/private/tmp/core-operation-own-history-admission`, branch
`codex/operation-own-history-admission`, base `4d2e0bf1` plus the frozen receipt
lookup candidate. The lookup's full and CI suites completed and its source was saved locally as
`5fd97b8d`. These seven admission files were then reconciled onto that commit in
`/private/tmp/core-hardening-continuation`. The isolated development source is
preserved. The user authorized pushing qualified changes to the existing Core branch; no merge or deployment is included.

## Contract and threat boundary

Recorded execution, readable saved output and current domain state are separate
facts. This service admits only the current authenticated subject's own journal
namespace in the selected workspace; it never accepts a separate caller-selected
receipt principal. A human uses its freshly resolved Auth/profile identity. A
service keeps its own principal with no issuer aliases.

Human membership and current identity are fenced with the canonical identity and
workspace admission seams. Service/issuer lifecycle rows and structural roots
are rechecked. Services additionally require the exact new authorization-only
action `operation.history.read_own`, scoped exactly to the workspace or the
service's canonical App and usable directly. Existing wildcards, generic read
and App function grants do not gain this permission. A wildcard or review-only
candidate is skipped rather than masking a later valid exact grant.

A domain-read exclusion does not erase the otherwise admitted caller's own past
execution status. It does withhold output and present-state assertions. An
expired/revoked authentication credential must still be refused by the transport;
this internal service does not authenticate a token. Current identity/issuer
revocation, membership loss, invalid scope/subject binding, inactive history
policy and unavailable facts deny admission.

The service accepts a server-bound `ExecutionScope` and `Subject`. Creating an
ExecutionScope from arbitrary strings is not proof of authentication or selected
transport scope. Public producers must establish that binding from their actual
authenticated request, immutable broker/run scope or owned approval before this
path can be exposed. No HTTP endpoint, SDK fallback, model tool or UI currently
uses this new service.

## Qualification

- Initial native candidate: 16 passed (`/tmp/core-own-history-native-first.xml`).
- Expanded native identity/delegation matrix: 30 passed, no skips/failures/errors
  (`/tmp/core-own-history-expanded-native.xml`).
- Exact-action/read-class/wildcard compatibility units: seven passed
  (`/tmp/core-own-history-policy-units.xml`).
- Pinned formatting/imports/lint/convention/mypy passed
  (`/tmp/core-own-history-final-hooks.log`).
- Membership ordering probes were then strengthened: the independent actor must
  reach the conflicting native Auth-row CAS before a blocked result is asserted.
  The fresh combined native run passed 91 cases, zero skips/failures/errors
  (`/tmp/core-own-history-qualified-native.xml`), including the strengthened
  probes, receipt/alias tests and existing delegation contracts.
- Full selected policy/input units: 58 passed, zero skips/failures/errors
  (`/tmp/core-own-history-qualified-units.xml`).
- The App-revocation and offboarding probes were then strengthened to assert
  actual `resolve_role == None` and `can_access_workspace == none` facts and
  require explicit permission denial for invalid grants. All twelve selected
  cases passed (`/tmp/core-own-history-authority-proof.xml`). These cases overlap
  the preceding native set and are not added to its total.
- Staged substrate/module guards passed (`/tmp/core-own-history-staged-guards.log`).
  The new policy action/admission candidate still needs whole-source, CI and
  producer/consumer qualification; receipt-only full tests do not cover it.

## Concrete next producer/consumer work

1. Bind HTTP to authenticated request identity and validated request workspace;
   bind resident/MCP/broker calls to their immutable admitted principal/scope.
   No payload, saved preference, issuer substitution or constructor-origin string
   can establish this authority. Preserve current credential/work lease checks.
2. Define the typed opaque response: original `operation_receipt`, explicit
   `output_disposition: withheld` and `current_state_verified: false`. Omit saved
   output, private keys/hashes, resource references/titles and policy reasons.
   Admit this as receipt evidence, not an arbitrary readable Core Object.
3. Consult known execution before current App/function/input-schema admission
   where needed. Absence still enters the existing fresh execution path; conflict,
   incomplete history, identity/scope/grant failure and outages remain explicit.
   Never manufacture a new approval or handler call from a withheld result.
4. Reconcile a staged/policy approval under its own immutable decision/actor
   contract. A decider's approval authority is not a general right to query a
   service issuer/delegate's journal, and an issuer must not become an alias.
   Keep the original canonical operation journal and stable command identity.
5. Carry status versus readable output through HTTP/SDK, broker/MCP/resident,
   worker, staging and transcript/reload paths. Repeat authorization at release
   boundaries; do not hold identity/workspace locks across a network send.
6. Qualify removed/changed schemas and handlers, withheld/oversized/legacy output,
   unavailable admission, stale credentials, scope mismatch, all subject kinds,
   post-commit transport loss, independent revocation, cancellation, replay and
   approval state/readback. The full hardening goal remains open.

Native Policy history grants follow the existing active/revoked Policy lifecycle;
this candidate introduces no token authentication or new expiry mechanism.
Function-grant expiry and transport-credential expiry are independent gates and
must be exercised in the producer/consumer matrix rather than inferred here.

## Final combined source qualification

These admission files and the recorded-result coordinator were qualified together
on `5fd97b8d` in `/private/tmp/core-hardening-continuation`. The 15-file source
seal was rechecked after terminal suite completion, before these documentation-only
qualification updates. The source checks and evidence below apply to that exact
combined candidate; no public consumer integration is included.

- Full default backend: 7,286 cases, **6,508 passed**, 778 skipped, zero failures
  or errors, 1,490.703 seconds (`/tmp/core-recorded-results-full.xml`).
- CI-faithful selection with TESTING=1, DEBUG=false, no backend dotenv and two
  workers: **1,776 passed**, 22 skipped, zero failures or errors, 1,254.840 seconds
  (`/tmp/core-recorded-results-ci.xml`).
- PostgreSQL coordinator, current own-history admission, observation, transaction
  and alias contracts: **103 passed**, no skips/failures/errors, 333.875 seconds
  (`/tmp/core-recorded-results-final-native.xml`).
- Focused unit neighbors: **58 passed**, one PostgreSQL-only skip; overlaps the
  broad suite (`/tmp/core-recorded-results-expanded-units-final.xml`).
- Pinned format/lint/type hooks and staged architecture guards passed.

The full default suite's skips are not relabeled as native acceptance. The native
run covers the listed contracts, not every skipped restore/session/provider
journey. No browser/deployment or whole-goal completion is claimed.
