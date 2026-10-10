# Mongo retrieval and performance boundaries

Mongo-backed retrieval and graph behavior must be qualified against the actual indexes and adapter. This guide does not authorize Mongo as a production durable-work store; that runtime currently fails closed for Mongo.

Measure permission-filtered reads, bounded neighbor pages, indexed record lookups, connector deduplication, and retrieval scope prefilters on representative data. Record row counts, latency distribution, query shape, and indexes. Do not compare a small in-memory fixture to a production database and present the result as a benchmark.

Semantic retrieval depends on the configured vector backend. Inspect the retrieval adapter and index configuration rather than assuming all stores implement the same behavior. Permission and workspace filtering remain required before results can be exposed.

Use pagination and counts rather than hydrating a collection to count or slice it. Any procedural or denormalized deviation from the object-spatial default needs measured evidence and an inline explanation. See [pagination](../backend/pagination.md) and [invariants](../INVARIANTS.md).
