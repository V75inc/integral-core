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

`invoke_app_operation()` is intentionally not yet routed through this scope.
It still performs lookup → handler → receipt storage, and its process-memory
fallback remains insufficient for a logical command. The new receipt primitive
eliminates the local duplicate-execution race, but routing the dispatcher now
would still let synchronous change-event delivery escape the transaction.

The next slice must introduce one command-execution record with a deterministic
identity and state machine:

1. commit an operation outbox fact with the local receipt and graph effects;
2. deliver change/audit notifications from that committed outbox;
3. route the dispatcher through the receipt primitive and remove its
   process-memory fallback; and
4. recover external `unknown_outcome` only through provider reconciliation,
   never by silently rerunning an effect.

That slice needs independent-connection race and injected-crash tests. It is
the remaining WP-00 gate before the wider WP-03 command consolidation starts.
