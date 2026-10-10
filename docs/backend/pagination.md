# Bounded graph reads and pagination

Use a bounded single-hop `node.nodes(..., limit=N)` for small neighbor reads. Use `nodes_page` for a page and `count_nodes` for a count. `connection_count` reads degree without hydrating neighbors.

Do not fetch an entire collection merely to slice it or call `len`. Use class/list-form edge and node filters supported by jvspatial. Agentive PascalCase edge classes must not be confused with their ALL_CAPS alias strings.

Multi-hop computation normally belongs in a Walker. A measured bulk-query alternative may be appropriate on a hot path, but must preserve scope, typed relationships, graph integrity, and documented evidence. Measure representative data and latency rather than arguing from hypothetical inefficiency.

The `nodes_len_drift_check` guard and [invariants](../INVARIANTS.md) enforce the relevant boundary. Test pagination stability, counts, permissions, and limits on the intended adapter.
