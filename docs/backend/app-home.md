# Package-owned App Home

`app.home` gives an installed App a package-defined entry experience under its active ApplicationDefinition. Home is the default App section unless an explicit preference selects another section. Dashboards remain separate, mutable instance projections.

A Home can declare one to six widgets and up to three actions. Data is resolved through the declared capability contract. A query-backed widget must provide the complete rows and totals its output contract requires. Failed sources become unavailable; they must not become fabricated zeros or empty lists.

Resolution checks definition ownership, active pointer, and status. Sources share a bounded five-second resolution budget. A stale, inaccessible, or mismatched definition cannot become execution authority.

Record summaries expose selected typed scalar fields and validated Entry identity. They cannot turn arbitrary query rows into unrestricted drill-through links. Actions prepare an editable chat draft rather than sending it to the model.

Test definition rejection, permission denial, total/row consistency, unavailable sources, time budget, browser rendering, and action draft behavior. See the [extension contract](../platform/extension-contract-v1.md).


### Resident starter payload

`integral_suggest_dashboard` returns `home` separately from its rich analytics
`widgets`. The Home starter contains only supported composition fields, at most
12 unique widgets with nonoverlapping placement, and currently authorized
sources. A null starter means none could be prepared. Suggestion does not save
anything: `integral_update_app` stages the composition for ordinary approval and
rechecks bindings, and `integral_read_app_home` verifies the saved result.

### Scaffolded App Home

When designing a new App, the scaffolding skill selects a layout from the
brief and names it in the proposal. The approved blueprint stores the
selection in `app.home.layout`.

| Layout | When to use it | Home content |
| --- | --- | --- |
| Quick Start (`quick_start`) | People mainly come to perform an action, or the brief is sparse. | Primary action, workflow guidance, and Track shortcuts. |
| Operations Overview (`operations_overview`) | People review work, deadlines, or exceptions regularly. | Track shortcuts with record counts alongside guidance on what to check. Filtered queues and KPIs belong in saved views or dashboards. |
| Resource Hub (`resource_hub`) | People mainly come to find information. | Described category links followed by guidance on where to start. |

During scaffolding, propose a dashboard when the brief calls for a recurring
decision, review, or comparison that the planned records can support. This
is the default for a workload review like Facilities Dispatch. A submission
app or reference hub can omit a dashboard if analytics would add little.
Build any proposed dashboard with the App and verify the saved widgets.

Write the introduction and each guidance item as a short paragraph. The
scaffold normalizes extra whitespace within each item and the UI wraps it
naturally. Use planned Track names for shortcuts and the same names as keys
in `shortcut_descriptions`. Actions open editable chat drafts. Existing
Home definitions can still omit the layout field.
