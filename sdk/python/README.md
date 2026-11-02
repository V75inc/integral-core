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

A declared `query` returns a `QueryResult`, whose `rows` can be an empty page,
one record, many records or one aggregate object according to the declaration.
Do not equate a one-row result with success or a multi-row result with failure.
Preserve its opaque `cursor`, references and warning/degradation evidence. A
page length or `total_estimate` is not an exact record count.

Declared queries preserve the source collection name in `row_collection` and
its sibling fields in `result_metadata`. Report totals are not automatically
record-count estimates. The SDK still returns a mapping; its `.source_output`
attribute reconstructs the validated App envelope for adapters, without adding a
duplicate payload to JSON. REST, MCP and resident queries carry the same normalized
rows and report metadata once. This confers no additional permission and does not
bypass the declared query.
