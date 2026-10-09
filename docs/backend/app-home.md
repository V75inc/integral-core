# Package-owned App Home

`app.home` gives an installed App a package-defined entry experience under its active ApplicationDefinition. Home is the default App section unless an explicit preference selects another section. Dashboards remain separate, mutable instance projections.

A Home can declare one to six widgets and up to three actions. Data is resolved through the declared capability contract. A query-backed widget must provide the complete rows and totals its output contract requires. Failed sources become unavailable; they must not become fabricated zeros or empty lists.

Resolution checks definition ownership, active pointer, and status. Sources share a bounded five-second resolution budget. A stale, inaccessible, or mismatched definition cannot become execution authority.

Record summaries expose selected typed scalar fields and validated Entry identity. They cannot turn arbitrary query rows into unrestricted drill-through links. Actions prepare an editable chat draft rather than sending it to the model.

Test definition rejection, permission denial, total/row consistency, unavailable sources, time budget, browser rendering, and action draft behavior. See the [extension contract](../platform/extension-contract-v1.md).
