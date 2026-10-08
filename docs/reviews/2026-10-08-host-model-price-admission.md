# Host model price admission contract

Status: internal source implementation; not deployed or an enforced live dollar cap.

## Contract

A trusted host registers an asynchronous model price resolver at boot. Core accepts
strict request bounds and host evidence, checks the exact execution digest and route,
checks the bounds and tariff validity windows, and computes a conservative USD
ceiling from input/output token upper bounds plus a mandatory request fee. It
rounds upward to 1e-8 USD, regardless of ambient decimal precision. Cache and
scheduled discounts are not inferred. Missing evidence, malformed evidence,
nonfinite rates, stale bounds, route mismatch and a resolver exceeding ten seconds
fail closed. Cancellation propagates rather than becoming permission to dispatch.

The request digest includes tenant/workspace/principal, thread/session/run,
permission/capability versions, credential source/generation, payload fingerprint,
physical request identity, bounds and expiry. The quote identity hashes this digest
and normalized price evidence. Equivalent decimal/timezone representations keep
one identity. Host account applicability is an attestation, not authenticated by
Core from a public rate table.

## Preserved substrate invariants

- I-SUBSTRATE-01 and I-EXT-01: no App names, domain branches or App imports.
- I-CRUD-01: service-owned calculation; no graph writes or API mutation path.
- I-GRAPH-01/02: no new persistent graph entities or unrooted nodes.
- Existing authorization, reservation and dispatch fences remain separate checks.
  A computed quote confers no execution permission.

## Evidence

Targeted tests: 34 passed in 3.05 seconds, recorded in
`/tmp/venture-host-model-price-tests-expanded.log`. The initial run failed collection
because pytest reserves the fixture name `request`; the fixture was renamed.
Synthetic prices test exact fees, upward rounding, scope/route/request replay,
missing/negative/nonfinite prices, explicit complete zero evidence, expired bounds,
expiry during resolution, invalid unchecked copies, sanitized host errors and a
stalled resolver. These tests do not prove live pricing, billing or browser behavior.

## Required next work

The host must establish bounded actual payload and SDK output limits, retain the
request/evidence binding for dispatch, resolve applicable account terms, and reserve
against the shared mandate budget before every physical request. Provider receipts
remain distinct from admission ceilings; unavailable provider costs remain
unavailable. No resolver is activated for this deployment by this change. Executable
mandate producer, controls and restart/reconciliation qualification remain open.
