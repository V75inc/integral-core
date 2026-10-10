# Evolve extension contracts

A public contract change affects independent App authors. Treat its information shapes, policy requirements, lifecycle, and failure semantics as a reviewed interface.

Describe compatibility, source and wire changes, migration needs, and effects on existing packages. Add behavior tests using an external package that does not import private Core modules. Preserve Core-only boot and the [invariants](../INVARIANTS.md).

Additive fields must retain deliberate defaults and validation. A change that broadens authority, permits executable code, or changes effect replay requires explicit architecture review. Do not disguise it as a renamed helper.

Update the [extension contract](extension-contract-v1.md), SDK types, runnable reference package, generated capability artifacts where affected, and authoring guidance together. A checked-in example is not proof of production qualification; record the actual artifact and acceptance evidence separately.
