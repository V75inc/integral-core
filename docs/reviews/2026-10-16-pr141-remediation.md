# PR 141 remediation and qualification

Target: PR 136's `codex/core-rc18-jvspatial-022` branch, including PR 138.
Core remains generic: no employment workflow, billing overlay or App package is
introduced by this remediation.

## Credential delivery

- Alternate delivery remains limited to newly provisioned accounts. Admission is
  checked again inside the account/membership transaction to close the lookup race.
- Production requires shared transactional storage and configured credential
  encryption. Single-process development/test stores retain local fencing only.
- Platform authentication mail bodies never dispatch to workspace/App hooks,
  including when the request carries an ambient workspace scope.
- Pending and released delivery slots are server-owned. Profile exports and event
  projections scrub them; profile writes cannot forge, clear or replace them.
- Confirmation uses a leased, persisted delivery intent. Profile reads after
  acquiring the database lock bypass graph identity caches. Wrong-code counters
  therefore serialize across tasks and workers.
- Verification stages a single-use password-setup token, encrypted with intent-
  and account-bound associated data. It never changes the password or emails a
  temporary password. The existing reset flow owns password selection, atomic
  token consumption and session revocation.
- Mail failure leaves the SAME encrypted setup link retryable. Provider success
  is reported only after delivery; duplicate confirmations are idempotent.
  A worker crash permits retry after the 60-second lease expires. At-least-once
  email delivery can resend the same link, never a competing password/token.
- Account password changes and replacement reset requests invalidate pending
  deliveries. Unversioned legacy intents fail closed and require reissue.
- Challenge mail can be retried from the public confirmation page. The random
  intent fixes the recipient; retries preserve code expiry and wrong-code counts,
  permit at most three sends and require thirty seconds between attempts.
- `/credentials-delivery-verify` is shipped in Core, with the old `.html` URL as
  an alias. The unauthenticated verify/resend APIs accept no recipient override.

## Framework and template boundary

Template providers remain explicit, neutral package identities. Canonical kebab
and legacy underscore identities normalize on both sides of matching. Listing,
generation and mutation helpers carry `template_provider_slug` to lookup.
Callers selecting a provider must pass that declaration through; Core does not
fall back to a named commercial App.

SharedTrackPage no longer generates a contract on step advance. Preparation and
review belong to the host/App review component lifecycle. The default Core host
stub reports an unavailable capability and never accepts the step. Host/App
implementations must supply that workflow and their own API clients; the newly
introduced contract metadata API client was removed from Core.

The public Python SDK update signature includes the runtime's additive optional
`title` argument, preserving keyword-only optimistic revision checks.

## Validation

Regression coverage includes profile/event isolation, mail false/exception,
restart/lease recovery, concurrent attempts, three independent PostgreSQL worker
processes competing for one delivery, setup-token redemption/replay, stale
credential invalidation, canonical/legacy template identities and provider
propagation. UI coverage exercises confirmation, pending delivery, retry and the
unsupported Core review stub. Exact final suite/CI results are recorded in the
PR description after execution, rather than asserted in advance here.

The credential-delivery, member-provision authority, relation-admission and
template-resolution regression modules also run in the PostgreSQL CI lane and
the matching `make test-postgres-ci` target.
