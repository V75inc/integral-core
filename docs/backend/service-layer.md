# Canonical service-layer writes

HTTP and agentive routes validate transport input and delegate persisted mutations to named services. They must not introduce competing Node creation, relationship wiring, or lifecycle paths.

Services perform current access and policy checks, validation, graph attachment, revision handling, and canonical event emission. Internal operations and approvals use the same mutation services where their contract requires equivalent behavior.

Graph participants attach to a rooted parent in the same transaction/unit of work. Use typed edges for relationship state and Object persistence for non-graph records. All change events use `emit_change_event`; no handler writes a parallel audit row.

App tools use the published facade rather than importing these private services. See [extension contracts](../platform/extension-contract-v1.md) and I-CRUD-01 in [invariants](../INVARIANTS.md). The service-layer guard inspects staged changes, so verify the intended index before committing.
