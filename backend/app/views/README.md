# Backend view contracts

This subsystem validates supported view types and configuration against the shared palette. Keep it aligned with frontend manifests and generated contracts.

A view is a projection of authorized records. It cannot grant access, change record identity, or embed arbitrary executable behavior. Package composite keys resolve to registered primitives; iframe extension views have a separate declared host contract.

See the [palette guide](../../../docs/operational-models/VIEW_PALETTE.md) and [plugin boundary](../../../docs/operational-models/PLUGINS.md).
