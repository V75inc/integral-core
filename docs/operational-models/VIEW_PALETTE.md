# The view palette

The palette defines registered presentation primitives and their supported configuration. Frontend manifests live in `frontend/src/views/manifests/`; backend compatibility lives in the view subsystem. Registry and contract artifacts must agree.

Core includes familiar record presentations such as feed, table, kanban, calendar, gallery, and wiki, along with composable and region primitives. Discover the actual registered palette rather than maintaining a second hardcoded list in an App.

A view selects record types and declares filters, grouping, sorting, or field presentation according to its contract. It does not create copies of records or grant access. Data requests preserve workspace and resource authority.

Saved default selection is scoped to the Track's named `HAS_VIEW` ownership.
For a dedicated model, view writes synchronize the visible default's stable key
and hidden flags into its manifest. A model shared through multiple named
attachments, or declared as an anchor template, is not rewritten by a local
Track view edit. Navigation projects the sole visible local default over a copy
of the shared defaults; an unkeyed local default omits the template default key.
Schema resolution continues to use the shared manifest without fetching views.

Lazy shared-model view repair creates missing template views while preserving
existing local names, types, configurations, constraints and visibility choices.
It does not update the shared manifest or materialize schema and taxonomy.
Explicit model/package updates remain separate governed operations. Hiding a
default chooses a visible replacement; making a hidden view default unhides it.

App-owned composite keys resolve to registered bases. Iframe extension views use their distinct asset/handshake contract. A code plugin requires reviewed registry and backend changes rather than executable code hidden inside a manifest.

Test empty, loading, denied, unavailable, populated, and narrow-screen states. Named relation/member/file values use shared authorized presentation. See [composites](COMPOSITES.md), [regions](REGION_SYSTEM.md), and [plugins](PLUGINS.md).
