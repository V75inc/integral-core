# WP-05.2 — Usage reconciliation evidence

`ModelUsageObservation.provider_cost_usd` and run summaries use `Decimal`; missing cost remains `None`. `summarize_model_usage` groups transition records by one Core request ID, validates that a request's route and execution scope remain stable, and counts a response once. Unanswered dispatch intents, interrupted outcomes, missing usage, and partial token reports reduce completeness flags. Only source-reported token quantities and LiteLLM response cost are summed. No price estimate is synthesized.

This is a run-local observation summary, not an invoice or a settled usage ledger. It deliberately reports `provider_cost_complete=False` when any completed request lacks returned cost or any provider result is unresolved. Integral Business must define tariff/rounding/entitlement policy separately.

Validation on Python 3.14.3:

- WP-05 observation tests cover encrypted append-only persistence, idempotency/conflict, scope readback, deduplication, and unresolved usage aggregation.
- WP-04 and WP-05 focused suites pass with mocked SDK/Object persistence only.
- Black, isort, and flake8 passed on the touched Harness files.

No external requests or provider callback captures were used; PostgreSQL reconciliation durability remains unqualified.
