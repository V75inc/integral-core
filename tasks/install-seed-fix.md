# Install seed relation authority

Goal: seeded App installation completes without granting normal reads to an installing App.

- Bind a temporary relation-read admission to the installing App, its current human installer and the exact asyncio task.
- Admit only canonical same-App source/target containment, current workspace membership/configuration rights, current pre-activation lifecycle (`installing` or authorized settings finalization), and no access blocks/exclusions.
- Keep ordinary entry reads, cross-App/cross-workspace relations, other subjects and child tasks denied. Do not activate incomplete Apps or substitute a system subject.
- Retain normal relation schema/type/containment validation and rollback on required seed failures.
- Qualify full real inline and deferred-settings seeded installs, persisted relation links and idempotency plus adversarial admission tests on JSON and PostgreSQL; run full local and CI release gates.

Preserved invariants: I-GRAPH-01/02 canonical containment; tenant and principal authority from current facts; existing denial semantics outside the narrow installer effect; I-CRUD-01 canonical field writer and relation edges; lifecycle active only after completed installation. No Business domain keys or UI enter Core.
