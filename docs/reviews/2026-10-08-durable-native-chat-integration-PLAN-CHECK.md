# Durable native chat integration: candidate qualification

This is an implementation candidate on top of Core PR #121 / merged #119.
The deployed API remains the previously qualified final-readiness image.
WP-03.3.3's rollout gate remains: HTTP acceptance, committed public replay,
cancellation, recovery, browser reconnect and the full repository gate must
pass before resident default promotion or presentation as the completed path.
An isolated local qualification deployment may enable the explicit candidate
flag after the repository gate passes to obtain the required browser evidence;
that trial does not promote the default or qualify unsupported input paths.

## Implemented candidate

- Register `chat_turn` in the static worker table and route it to its dedicated
  handler. Chat failures use atomic transcript/run/admission terminalization;
  generic WorkItem error transitions must not strand a principal slot.
- Exercise queued accepted submission through the normal claim/dispatch entry,
  not only direct private handler calls. The same committed result cannot be
  claimed again. Failure releases both the slot and thread pointer, allowing
  another accepted turn.
- Compare restored canonical message parts, metadata, parent and encrypted
  typed host context against the accepted request fingerprint before provider
  preparation. A changed message previously passed scope authentication; it
  now fails closed before the provider. Historical omitted context is accepted
  as equivalent to typed empty context only if the context is entirely empty.
- Route the existing PostgreSQL worker completion, stream failure and recovery
  tests through `execute_claimed_work`, including committed transcript/event
  reconciliation and process-crash/unsettled-request cases.

## Preserved invariants

- I-EXT-01 / I-SUBSTRATE-01: generic Core chat contract, no Business import or
  App-name branch.
- I-WORK-01/02/03: accepted scoped identity, deterministic run/effect identities,
  current lease fencing and shared-store transactions remain authoritative.
- I-HARNESS-01: thread containment, tenant scope, encrypted capsule and private
  model receipts remain separate from public replay.
- I-WORK-05/07: ordinary chat does not confer mandate approval or budget
  authority; each physical dispatch still uses its native boundary checks.
- New I-HARNESS-02: accepted input content is authenticated on restoration.

## Evidence and remaining gates

Targeted unit selection: 49 passed. Expanded actual PostgreSQL/unit selection
initially passed 86 cases after the content-binding correction. Its first run
exposed that changed accepted message content still reached the provider;
that regression is retained and corrected. The updated dispatcher-wide recovery
rerun passes **86 cases in 54.00 seconds**, terminal exit 0, in
`/tmp/venture-durable-chat-dispatch-postgres.log`. Policies/provider responses are
synthetic, not live default-model or browser durable execution evidence.

Additional candidate qualification:

- Every shared-store terminal chat transition now releases matching admission
  slots and the active thread pointer in its own transaction. Queued cancel,
  expiry and failure permit another accepted turn; injected cleanup failure
  rolls back the WorkItem, outbox and admission changes together. Reads use
  the caller transaction rather than cached graph entities.
- Authenticated SSE reconnect delivers committed sequence IDs only, checks
  current thread/workspace authority before headers and before each page, and
  never dispatches or requeues work. Sequence gaps fail closed. Disconnect
  closes delivery only; the native thread Stop route uses its active WorkItem.
- The shared SSE encoder is dependency-free, removing an import cycle exposed
  by isolated replay-service collection while preserving its existing import.
- Cleanup/dispatcher/recovery PostgreSQL selection: **80 passed in 28.27s**,
  terminal exit 0, `/tmp/venture-durable-chat-cleanup-postgres.log`.
- Committed stream and authenticated route selection: **13 passed in 4.20s**,
  terminal exit 0, `/tmp/venture-durable-chat-stream-unit.log`. Includes foreign
  WorkItem rejection before SSE headers and exact durable thread cancellation.
- Module import-cycle and module-boundary guards pass; `git diff --check` passes.

Receipt retry and frontend reconnect follow-up:

- Duplicate accepted submissions now validate their saved canonical message and
  containment and return the original receipt before capsule encryption or
  admission changes. Queued, claimed and completed retries preserve accepted
  input and timestamps. A completed retry preserves a newer active turn;
  tampered canonical input is rejected. The regression initially failed because
  receipt recovery called encryption again, and passes with the correction.
- PostgreSQL submission/worker plus idempotency, route and stream selection:
  **72 passed in 25.55s**, terminal exit 0,
  `/tmp/venture-chat-receipts-postgres.log`.
- Native frontend replay activates only when the authenticated response supplies
  `X-Integral-Work-Item`. It reads committed SSE IDs, reconnects with scoped GET
  requests from its delivered cursor, suppresses duplicates, and rejects missing,
  invalid or skipped sequence IDs. Delivery retries are bounded to three;
  exhausted delivery reports saved work rather than sending another POST.
  Legacy jvagent and receipt-less native responses retain existing behavior.
- Native replay and legacy auth/error frontend selection: **14 passed**,
  `/tmp/venture-native-replay-frontend.log`. This is simulated HTTP evidence,
  not browser recovery evidence. The deployed synchronous API does not yet
  supply a durable acceptance receipt header.

Gated HTTP producer follow-up:

- `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED` defaults false. When enabled, the
  existing native `/messages` route requires a stable request ID and atomically
  accepts the canonical user message, encrypted typed context, WorkItem, outbox
  and admission slot instead of creating an inline run. The response returns
  WorkItem/message receipt headers and uses committed event delivery. Header
  exposure supports authenticated cross-origin clients.
- Current attachment/image and host-action inputs are explicitly rejected
  before acceptance in this candidate. This is a temporary WP-03 admission
  limitation; the existing default synchronous path remains available while
  secure reconstruction and host-control contracts are implemented. Those
  contracts remain required for the complete Venture Journey experience.
- The HTTP producer hashes its validated client payload on the server. This
  digest and canonical message content bind retry identity while the accepted
  capsule preserves the first host snapshot. A changed client payload or
  canonical message conflicts; a later host-state change does not overwrite
  accepted instructions. Bearer-like approval tokens are omitted and rebuilt
  by the claimed worker.
- First-message title, pending-question cleanup and last page context are
  written only inside successful initial acceptance. Shared title derivation
  preserves the synchronous path's presentation. Receipt retries do not
  mutate these fields.
- Latest PostgreSQL submission/worker plus route/idempotency/stream selection:
  **74 passed in 29.60s**, `/tmp/venture-chat-receipts-postgres.log`.
- Latest focused HTTP/unit selection including missing request ID and rejected
  unqualified inputs: **34 passed in 4.68s**,
  `/tmp/venture-durable-http-unit.log`.
- Legacy attachment/image/draft/JV translation selection: **37 passed in
  6.20s**, `/tmp/venture-durable-http-legacy.log`. Configured backend lint,
  module-cycle and boundary guards and diff whitespace checks pass.

Still required: independent process recovery through the complete HTTP path,
browser receipt/reload/reconnect integration, secure attachment/host-control
input contracts,
default-model browser verification and full `make verify`. No account tariff, provider bounds policy, public mandate,
version change or production rollout is enabled by this candidate.
