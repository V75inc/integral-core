# Retained model price binding

Status: source implementation, not live SDK admission or an enabled tariff.

## Contract and preserved invariants

The host resolver can produce a reservation request retaining its exact bounded
physical request and trusted pricing evidence. Core recomputes and validates the
quote from that binding. Durable reservation storage already retains the validated
request; no new graph entity or API is introduced. Shared exact money primitives
move to `work_money.py` and remain re-exported through the existing budget module.

A priced request belongs to one physical provider request ID. Its logical effect
key remains the deterministic leased step identity, a separate retry/accounting
concept. Bound reservation admission requires owning principal/workspace/thread
and run with a lease context. Dispatch marking requires fresh physical request
bounds and exact execution scope, including permission and capability versions;
it rejects changed payload fingerprints, limits, route, request identity or expiry.
The dispatch reference must equal the physical request ID used to find receipts.
Hydration rechecks retained price/quote/fingerprint consistency and marked scope.

Historical unbound reservations preserve their fingerprints and remain readable.
This compatibility does not qualify them as priced physical SDK admission. The
future executable adapter must require a complete binding for its model calls.

I-SUBSTRATE-01/I-EXT-01 are preserved: no App identities, domain branching or App
imports. I-CRUD-01 is preserved: the existing service owns accounting transactions.
I-GRAPH-01/02 are preserved: no new Node or graph participation. The existing
shared-root budget, approval/lineage, row locks, lease/effect fences, stop handling
and accounting receipt rules remain required. Pricing alone gives no permission.

## Evidence and remaining work

Targeted final source tests: 125 passed, 5 PostgreSQL-only skips in 3.39 seconds
(`/tmp/venture-price-binding-final-unit.log`). These include 47 price/binding cases
plus the existing mandate contract selection. The final PostgreSQL/native selection passed all 350 cases in 68.71 seconds
(`/tmp/venture-price-binding-final-postgres.log`). The earlier 348-case
run passed before the final physical-reference check; it is not qualification of
the final delta. The first full gate stopped on a missing docstring and dictionary style lint;
both were corrected. Full verification and commit hooks are still required.

The synthetic database case stores/rehydrates one priced hold, rejects missing or
changed physical evidence without marking intent, then marks the exact request.
No provider call or real billing is exercised. Physical payload/token bounding,
applicable account policy registration and per-SDK admission wiring remain open,
as do executable mandate review, controls and restart/reconciliation acceptance.
