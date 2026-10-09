# The view palette

The palette defines registered presentation primitives and their supported configuration. Frontend manifests live in `frontend/src/views/manifests/`; backend compatibility lives in the view subsystem. Registry and contract artifacts must agree.

Core includes familiar record presentations such as feed, table, kanban, calendar, gallery, and wiki, along with composable and region primitives. Discover the actual registered palette rather than maintaining a second hardcoded list in an App.

A view selects record types and declares filters, grouping, sorting, or field presentation according to its contract. It does not create copies of records or grant access. Data requests preserve workspace and resource authority.

App-owned composite keys resolve to registered bases. Iframe extension views use their distinct asset/handshake contract. A code plugin requires reviewed registry and backend changes rather than executable code hidden inside a manifest.

Test empty, loading, denied, unavailable, populated, and narrow-screen states. Named relation/member/file values use shared authorized presentation. See [composites](COMPOSITES.md), [regions](REGION_SYSTEM.md), and [plugins](PLUGINS.md).
