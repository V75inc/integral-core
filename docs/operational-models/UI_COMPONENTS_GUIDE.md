# Choose UI components

Start with a core view for the collection, then use supported regions for focused interaction. Keep the record model understandable before adding presentation complexity.

| Need | Useful direction |
|---|---|
| Read recent records | Feed or supported list |
| Compare typed values | Table or supported grid |
| Group by workflow state | Board/kanban with declared state field |
| Place dated records | Calendar with supported date fields |
| Inspect related work | Authorized relation collection |
| Summarize an App | Declared Home widget or dashboard projection |
| Run a domain action | Declared operation with policy/staging |
| Configure an App | Standard App settings dialog via `app.settings_schema` |
| Package a special interface | Declared iframe extension view |

Use the live manifests for exact keys and configuration; this table describes intent rather than a substitute schema. Compose only supported primitives, and reject cycles or invalid configuration.

Use shared typography and surfaces from the UI system. App-specific
settings controls MUST live in the standard App settings dialog, never in a
Home, dashboard, Track, Entry or extension settings panel. See the
[settings UI contract](../platform/app-settings-ui.md).

Typed reference fields resolve names and destinations consistently. Handle narrow
screens, keyboard interaction, empty results, errors, and restricted records.

Validate browser results against underlying queries and records. Compilation alone does not prove the interface works. See [palette](VIEW_PALETTE.md), [regions](REGION_SYSTEM.md), and [qualification](../ops/QUALIFICATION.md).
