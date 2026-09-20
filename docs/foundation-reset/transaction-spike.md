# Transaction feasibility spike

**Status:** Partially qualified on 2026-09-20. This is implementation evidence,
not a claim that app commands are already atomic.

## Question

Can an Integral graph write include both node materialisation and a structural
edge in the same PostgreSQL transaction that will later hold an execution
receipt and outbox facts?

## Result

Yes, when the transaction is bound as the task-local jvspatial graph context.
`postgres_graph_transaction()` starts the public PostgreSQL transaction,
creates `GraphContext(transaction)`, and scopes it as the default context.
Normal `Node.create()` and `Node.connect()` calls then use the transaction
handle's public `save`, `find`, and edge persistence methods.

The live contract test proves both outcomes against PostgreSQL:

- successful exit commits two graph nodes and their structural edge;
- an injected exception rolls back all three rows.

The follow-on receipt contract also proves that a deterministic operation
claim, local graph effect, and completed result share that transaction:

- concurrent retries invoke one handler and replay one committed receipt;
- an injected handler failure leaves neither receipt nor graph effect; and
- reuse with a changed request hash fails before a handler can run.

Run it with:

```bash
cd backend
TESTING=1 INTEGRAL_TEST_DB=postgres JVSPATIAL_PG_GIN_INDEX=off \\
  JVSPATIAL_POSTGRES_MAX_POOL_SIZE=3 \\
  .venv/bin/python -m pytest -q \\
  tests/contract/test_operation_graph_transaction_postgres.py --tb=short
```

## What remains blocked

Mutating App operations with an explicit non-read policy action now require an
idempotency key and route through the receipt transaction. `OperationContext`
stages its entry-create ChangeEvent into an `OperationEventOutbox` document in
that same commit; the dispatcher delivers it only after commit and the
background recovery loop sweeps pending facts on startup and periodically.

This cross-database boundary is deliberately **at-least-once**. A crash after
the logging database accepts an event but before the primary record is marked
delivered can produce a duplicate audit row; it cannot lose a committed event.
The outbox identity is deterministic for consumer correlation.

The remaining WP-00 evidence is an independent-connection crash/recovery test
for that narrow delivery interval, then the wider WP-03 command consolidation.
External `unknown_outcome` remains a provider-reconciliation problem: never
silently rerun an external effect.
