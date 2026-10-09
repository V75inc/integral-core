# Declarative composite views

A package can define a reusable view type from a registered base and supported configuration. The reference App declares `hello_board` with base `composable_board`, then uses that key in a Track view.

Declare package `view_types` in authoring YAML version 3. Compilation resolves package-owned keys and validates the resulting runtime schema version 2. Unknown bases, incompatible configuration, and circular composition must fail rather than silently degrading into an unrelated view.

A composite can supply structure and defaults without a Core frontend registry edit. It cannot inject arbitrary code, broaden query access, or manufacture a new security boundary.

Use the runnable reference manifest for exact syntax. Test compilation, package-key resolution, record-type selection, fields, layout, and authorized browser results. See [palette](VIEW_PALETTE.md) and [UI components](UI_COMPONENTS_GUIDE.md).
