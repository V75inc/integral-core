# Integral Python SDK

App-specific settings UI MUST use the standard App settings dialog, declared
through `app.settings_schema`; handlers must not introduce a parallel settings
store/editor. Use the injected App settings facade and canonical policy. See
the [settings UI contract](../../docs/platform/app-settings-ui.md).

The SDK provides public typing contracts for App handlers: OperationContext, ToolContextV2, query modes, field/relation shapes, and revisions. These protocols describe Core-injected capabilities; importing a type does not grant access.

Prefer scoped get/query/invoke. Use the validated mutation helpers and inspect their result, including stale revision failure. Do not import private backend services or accept payload identity as authority.

See the [App quickstart](../../docs/developer/quickstart.md) and [extension contract](../../docs/platform/extension-contract-v1.md).

`ToolContextV2` includes `job_id`, `enqueue_job(..., idempotency_key=...)`,
`report_progress` and `job_status`. These match the existing runtime facade; job
progress does not authorize extra effects or bypass operation receipts.
