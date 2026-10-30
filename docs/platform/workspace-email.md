# Workspace email

Core owns outbound provider configuration and delivery metadata independently of
Business Apps. Owners and admins use **Settings → Workspace → Email**. Personal
workspace owners have the same configuration capability. Resend, SendGrid and
SMTP are supported. Native configuration and durable workspace sending require
PostgreSQL transactions; unsupported stores expose an unavailable state rather
than weakening enqueue atomicity. Platform immediate email remains available.
Inbox receiving, campaigns and open/click tracking are out
of scope.

## Configuration and authority

The workspace roots a `WorkspaceEmailConfiguration` through
`HAS_EMAIL_CONFIGURATION`. Draft and active snapshots are separate. Writes use
transactions, a workspace creation lock and an expected revision. Credentials
use the existing `INTEGRAL_CREDENTIAL_ENC_KEY` facilities, bound to workspace,
connection and credential field. API responses expose configured flags only,
including whether platform sending credentials are configured. Settings changes
retain secret-free audit intents in the existing transactional event outbox.
Missing encryption prevents credential writes. Secrets never enter browser
storage, AI page context, generic WorkItem payloads or provider error messages.

Save a draft, send a test to the administrator's account email or an explicitly chosen recipient,
then activate the tested revision. Edits invalidate the test. Testing uses the
exact draft directly and cannot inherit a platform provider. Activation does
not require webhooks; acceptance alone does not prove recipient delivery.

A custom provider has no automatic fallback. Disabling it selects platform
inheritance. Queue records retain the original connection identity; changing
or disabling that connection blocks pending messages instead of changing their
sender. An already dispatched request can still complete. Platform environment
changes also require operational care for messages already queued.

Password reset, email verification and alternate-credential verification remain
immediate platform-only sends. They never appear in workspace history or App
email hooks. Immediate legacy boolean APIs retain provider-acceptance semantics.
The durable enqueue API returns `message_id` and `status: queued`, without
claiming successful delivery. A retry returns the original enqueue receipt,
not current delivery state; retained WorkItems bind its content hash after
history expires. Invitations and scoped notifications use it on transactional stores; on other
supported stores they retain immediate delivery through the configured
workspace/platform resolver. Storage capability selects the path before any
transport call; provider failures never trigger a fallback or second send.
existing synchronous App methods remain compatibility paths. New Apps should
use `enqueue_workspace_transactional_email` with a stable key for each logical
message; reused keys with changed content are refused. Authorization and normal
App operation approvals remain the caller's responsibility; enqueue and the
worker require current workspace membership. Notification recipients may be
guests with resource access; that does not grant permission to originate App
emails. Notification ownership and resource access are rechecked both when
accepting the intent and immediately before dispatch. Canonical resource
locations are retained in the encrypted payload; revoked, deleted, moved or
foreign resources withhold delivery, including when membership remains valid.
Non-resource system notices still require the current recipient and workspace.

## HTTP and extension contracts

All settings endpoints require authentication and current workspace owner/admin
authority. They use the canonical `X-Integral-Scope: ws:<id>` resolver.

| Endpoint | Contract |
| --- | --- |
| `GET /api/workspace/email` | Redacted draft, active snapshot, revision, tested revision and mode. |
| `PUT /api/workspace/email/draft` | `{expected_revision, configuration}`; provider-specific configuration; null secret preserves the same provider's credential, empty string removes it. |
| `POST /api/workspace/email/test` | `{expected_revision, to?}`; test the saved draft; optional `to` defaults to the caller's authentication email. |
| `POST /api/workspace/email/activate` | `{expected_revision}`; requires successful test evidence for that revision. |
| `POST /api/workspace/email/disable` | `{expected_revision}`; resume platform inheritance. |
| `GET /api/workspace/email/history` | Bounded `limit` and opaque `cursor`; workspace-only metadata. |
| `POST /api/workspace/email/webhooks/{connection_id}` | Public transport with provider signature verification; no product token grants webhook authority. |

The SDK's enqueue method is additive. App operation handlers must explicitly
opt into `authorization.effects: [email.enqueue]` alongside their normal roles,
targets and conditions. Enqueue requires the current exact operation permit,
unchanged definition, live action authority, any required approval replay and
the operation's task-owned PostgreSQL transaction. Read operations and inherited
child tasks cannot enqueue. The intent and worker command roll back with the
originating operation; dispatch performs the accepted intent rather than
replaying that App operation. A stable caller retry key is namespaced by App
and operation. Existing declarations grant no email effect by default.
If a final state transition consumes an authorization condition, enqueue the
intent before that transition, while the condition is still true. Delivery
still waits for the whole operation to commit; a later failure rolls it back.
 Legacy configuration upsert saves a draft
and directs administrators to Core for test/activation; it cannot bypass these
steps. Legacy send-test now tests the draft, rather than accidentally testing
platform delivery. Business Email Log remains an optional App-owned projection
of send-attempt hooks and retains its historical records.

## Durability and tracking

Scalar-keyed message intents, attempt metadata and verified events use the Object
collection. The encrypted message and email-dispatch WorkItem enqueue commit in
one graph transaction, joining an originating Core transaction when present.
The worker uses existing lease fencing and heartbeat supervision. Dispatch
intent commits before network I/O. Only definitive temporary rejections or
pre-dispatch connection failures are retried, with up to five attempts within
24 hours. Provider retry delays are honored. Unknown outcomes are never blindly
resent; Resend also receives the stable intent as its idempotency key.

History distinguishes queued, accepted, delivered, delayed, bounced, complaint,
failed and unknown. SMTP displays relay acceptance only. Resend and SendGrid
webhooks verify original raw bytes and timestamps before event persistence.
Verified events are deduplicated per connection, correlated within that
connection's workspace, and retained even when they precede the send response.
Terminal failure/bounce/complaint evidence outranks earlier acceptance/delivery.
No opens/clicks are ingested. Retired connection verification authority remains
available for late events, without retaining retired API keys or SMTP passwords.

`EMAIL_HISTORY_RETENTION_DAYS` defaults to 90. Recovery maintenance deletes old
metadata and clears queued ciphertext after seven days; terminal dispatch
resolution clears it immediately. Authentication attempts contain only provider,
status and timestamp. Workspace history omits message bodies, raw provider
responses and secrets.

## Operators and migration

Platform configuration: `EMAIL_PROVIDER` (`resend`, `sendgrid`, `smtp`, or
DEBUG-only `console`), `EMAIL_FROM`, `EMAIL_FROM_NAME`, `RESEND_API_KEY`,
`SENDGRID_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_TLS` (`starttls` or `implicit`). SMTP defaults to port 587; 465 is also
supported. TLS verification cannot be disabled. Private relay destinations need
an exact hostname in the operator-managed `INTEGRAL_SMTP_TRUSTED_HOSTS` list.
Validated destination addresses are pinned for connection establishment.

Set `EMAIL_WEBHOOK_BASE_URL` to the public API origin, including any deployment
prefix before `/api`. Save the webhook verification credential in the draft,
then copy the resulting saved URL to the provider dashboard. Saving a new draft
creates a new connection URL; update the provider URL before activating it.
Enable only delivery, delay, failure, bounce and complaint events. Resend uses
its signing secret; SendGrid uses the signed Event Webhook public key.
Loopback-only deployments can send email but need an externally reachable API
endpoint for provider tracking. Do not expose the desktop API to enable tracking.

Inspect worker availability, queued age, failed attempts and unknown outcomes.
An unknown outcome calls for provider reconciliation; creating a fresh message
can duplicate an earlier send. Do not infer current business state from an email
receipt or automatically replay the originating App operation.

For existing Business Email Log installs, schedule a maintenance cutover: stop
API/worker traffic, install the paired Core source/artifact, run the migration
from `backend`, then restart processes to discard legacy settings caches:

```sh
.venv/bin/python -m scripts.migrate_workspace_email --workspace-id ID --actor-user-id ID
# After reviewing the redacted inspection result:
.venv/bin/python -m scripts.migrate_workspace_email --workspace-id ID --actor-user-id ID --apply
```

This is privileged operator maintenance, not a public API. It requires a current
workspace administrator, refuses ambiguous/multiple installs and existing native
drafts, preserves the enabled sender/provider, encrypts plaintext legacy keys,
and removes legacy secrets only after native credential readback in the same
transaction. Previously enabled configuration is preserved without fabricating
a successful test. Re-running a completed migration does not overwrite native
settings. Migrate before upgrading Email Log's settings UI and before relying on
native workspace routing. Do not run migrations against live data as a test.

Validation must include PostgreSQL concurrency/recovery, Core-only startup,
Business package qualification against the intended Core artifact and authorized
real-provider sends with webhook readback. Automated mocked-provider tests do
not establish live delivery.
