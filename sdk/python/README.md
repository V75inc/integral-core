# Integral Python SDK

The SDK provides public typing contracts for App handlers: OperationContext, ToolContextV2, query modes, field/relation shapes, and revisions. These protocols describe Core-injected capabilities; importing a type does not grant access.

Prefer scoped get/query/invoke. Use the validated mutation helpers and inspect their result, including stale revision failure. Do not import private backend services or accept payload identity as authority.

See the [App quickstart](../../docs/developer/quickstart.md) and [extension contract](../../docs/platform/extension-contract-v1.md).
