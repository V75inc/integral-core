# Physical native model admission

## Contract and invariant review

This change moves the existing retained price binding to the actual LiteLLM
SDK boundary. Core supplies the exact final SDK argument projection after
Ollama output/context/reasoning mapping. Credential values and endpoint URLs
are excluded from the transient bounds input; the endpoint digest and route
generation remain bound. Plaintext input is neither logged nor retained in the
reservation. Registered trusted host bounds and price policies must attest all
billed tokens, including tool schemas, reasoning and supported modalities.
Core does not use a tokenizer estimate or public price table as that authority.

The run adapter distinguishes ordinary chat work from durable mandate lineage.
Every mandate request receives a deterministic logical ordinal and a new
physical ID. It reserves a bounded price under the shared root, then marks the
fenced intent before the SDK is invoked. Resolver awaits are followed by
permission/route and execution rechecks. The host provider checks current
workspace role and resolves the current route again. The existing ledger checks
approval lineage, lease, revision and reviewed route in its transactions.
Required intent receipt storage is followed by a single-use final readiness
callback. It rechecks current host authority and transactional ledger authority;
the adapter checks retained bounds and quote clocks again after transaction
close. No await intervenes between these final clocks and entering the SDK.
Ordinary durable chat also rechecks its current lease and authority after
receipt storage. Missing callbacks and reused callbacks fail closed.
Persisted terminal observations precede settlement. Only definitive provider
cost can release the hold through settlement; missing or estimated cost keeps
it. A restart cannot silently replay a previously dispatched ordinal.

Preserved invariants:

- I-EXT-01 / I-SUBSTRATE-01: no App identity branches or Business import; host
  policy uses generic boot registration.
- I-WORK-01: current owner, attempt, run, lease and cancellation fence before
  admission; the ledger rechecks the leased operation context.
- I-WORK-02: per-request logical effect keys are deterministic and separate
  from transport UUIDs.
- I-WORK-03: existing shared-store reservation/intent transactions retain
  atomic lineage and accounting; no provider call holds a database transaction.
- I-WORK-05: no public approval bypass or new executable producer is exposed.
- I-HARNESS-01 / workspace scope: authenticated execution scope and encrypted
  terminal evidence remain bound to the original run and route.
- New I-WORK-07 records the physical model boundary contract.

## Verification and remaining work

The updated native/unit/price selection passes 462 cases with 28 infrastructure
skips. Two actual PostgreSQL cases inspect committed holds and intent inside a
mock SDK call, confirm provider-priced settlement versus unknown-cost holds,
reject a fourth request at the shared count limit, and reject a restarted
adapter replay. Their approvals, prices and provider responses are synthetic.
The updated PostgreSQL/native/price selection passes 264 cases in 49.27
seconds (`/tmp/venture-final-model-readiness-postgres.log`). These
tests prove internal physical ordering and accounting, not public executable
approval or real billing. Logs: `/tmp/venture-physical-model-admission-native.log`
and `/tmp/venture-physical-model-admission-postgres.log`.

New delayed-receipt unit cases cover bounds and quote expiry, permission and
route revocation, ordinary work lease loss, and single-use callback rejection.
The actual PostgreSQL selection additionally changes root cancellation,
approval status, the leaf lease fence and price expiry after intent persistence,
then verifies that the SDK is never called and the existing hold is retained.
Current logs: `/tmp/venture-final-model-readiness-native.log` and
`/tmp/venture-final-model-readiness-postgres.log`.

No deployment host bounds or applicable account tariff is activated. No live
mandate is enabled. Current resource/capability policy for mandate tools, tool
admission, executable review/child production, public controls, process-kill
reconciliation and end-to-end founder acceptance remain necessary. A denial
after reservation preserves the hold conservatively; automatic verified
no-dispatch recovery is still separate work. Full Core verification and hooks
must pass before committing this change.
