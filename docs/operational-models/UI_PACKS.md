# UI Packs Standard (v1)

A **UI pack** names and completes a recipe that already exists: a content
Operational Model [code plugin](PLUGINS.md) that specifically adds one or more
track/entry **view types** to the [view palette](VIEW_PALETTE.md). Nothing
new is invented here — a UI pack is a `backend/app/plugins/<pack_name>/`
directory using the same `register(*, view_type_registry, ...)` entry point
`PLUGINS.md` already documents, that additionally ships a small pack
manifest, a namespaced view-type name, and (optionally) pack-scoped CSS.

Two live plugins (`backend/app/plugins/region_system/`,
`backend/app/plugins/payroll_register/`) already do most of this; this doc
formalizes the pattern and adds the two pieces they predate: view-type
namespacing and placement (`scope`) enforcement. `backend/app/plugins/
example_desk/` is a small, deliberately minimal reference pack proving the
recipe end to end — read it alongside this doc.

## UI Packs versus UI Complement Recipes

Use a **UI Pack** when Integral needs a new code-backed renderer or a new
generic wizard step kind. Use a [UI Complement Recipe](UI_COMPLEMENTS.md) when
an app only needs a reusable composition of Region System views and existing
wizard steps. Complement Recipes expand to ordinary manifest configuration;
they do not add React code, a new permission path, or runtime-loaded assets.

The distinction keeps plugins rare: most application-flow polish should be
recipe configuration, while UI Packs remain the escape hatch for a capability
the existing Region System cannot express.

## Pack manifest shape

A pack ships `pack.yaml` alongside its `__init__.py`, modeled on the same
`package:` block every operational-model `operational-model.yaml` already uses:

```yaml
integral_pack_version: 1
package:
  name: Example Desk
  slug: example-desk
  version: 1.0.0
  description: Reference UI pack demonstrating the standard end to end.
  trust_tier: trusted
  tags: [example]
assets:
  css: [style.css]
  # JS is implicit in v1 — the widget component files themselves,
  # same-origin bundled by Vite, same as every existing widget.
```

This is a distinct, smaller manifest than `PLUGINS.md`'s own
`plugins: [{id, version, signature}]` declaration block (which a *content
Operational Model* uses to declare a dependency on a plugin, and has zero real
consumers in `operational_model_compile.py` today) — `pack.yaml` describes the
pack itself, not a Operational Model's dependency on one.

## Namespacing

Every view type a pack registers is named `pack-name/view-name` (e.g.
`example-desk/desk-board`). Enforced at `register_view_type()`
(`backend/app/views/operational_model_view_types.py`) — a plugin-sourced spec
(`source="plugin"`) registering a bare, non-namespaced `type` raises there.
This is the one place every plugin spec passes through regardless of which
Operational Model later references it.

A handful of view types registered before this standard existed
(`action_bar`, `form_region`, `layout_container`, `summary_tiles`,
`reverse_relation_list`, `modal_region`, `popover_region`, `drawer_region`,
`static_content`, `tree_region`, `chart_region`, `editable_table`,
`payroll_register`) are grandfathered via an explicit allowlist in
`operational_model_view_types.py` so existing installed Operational Models keep
compiling. Do not add to that list — every new pack uses the namespaced
form from day one.

## Placement (`scope`)

Not every view type is meaningful in every placement. A track's `views[]`
is that operational model's full view registry — both views that appear as
track tabs *and* views that exist only to be referenced by key from an
entry type's `related_views[]` (see `RelatedViewsSection.tsx`'s own
docstring on the two `related_views[].view` reference forms). Some view
types (an action bar, a summary-tiles strip) only make sense bound to one
specific entry; others (a chart region, a tree region) are legitimately
used both ways depending on their own config. `scope` says which:

- `scope: str = "track"` (default) on `ViewTypeSpec`
  (`backend/app/views/operational_model_view_types.py`) — addressable as a
  track view/tab.
- `scope: "entry"` — only meaningful bound inside one entry's
  `related_views[]`.
- `scope: "both"` — valid in either placement.

This mirrors the frontend's own, older `WidgetRegistration.scope`
(`frontend/src/views/types.ts`), widened from `'track' | 'entry'` to
`'track' | 'entry' | 'both'` to match. Track pages already exclude
`scope: 'entry'` widgets from tab candidates
(`TrackDetailPage.tsx`) — that filter is unchanged by this doc.

**Enforcement.** `operational_model_compile.py` rejects, at manifest-compile
time, a `related_views[]` entry whose bare (same-track) view-key reference
resolves to a view type whose `scope` is `"track"` — a track-only widget
placed somewhere only one entry can bind to
(`_validate_related_view_scope_placement`). A resolver-prefixed reference
(`:anchored_track/…`) points at a different, not-yet-compiled track's CP
and is skipped — not resolvable at this compile, fail-soft like the
frontend's own handling of an unresolved resolver token.

The *inverse* direction — rejecting an entry-scoped view type from being
*declared* in `views[]` — is deliberately **not** enforced: `views[]` is
the shared registry both placements read from, so every existing
entry-scoped widget (`action_bar`, `summary_tiles`, …) is declared there
today specifically so `related_views[]` can find it by key. Enforcing that
direction would reject every existing Operational Model that uses one. Track-tab
*candidacy* (as opposed to registry declaration) is what the frontend's
`scope !== 'entry'` filter governs, and it already does that job
correctly.

The frontend adds a belt-and-suspenders check alongside the backend
rejection: `ComposableViewSlot`'s optional `allowedScopes` prop (set by
`RelatedViewsSection`, not by the other embedders that legitimately resolve
regions of any scope — `LayoutContainerWidget`, `ChartRegionWidget`,
`TreeRegionWidget`, …) renders nothing instead of the widget when a
resolved view's scope isn't in the allowed set — protection against a
track-only widget smuggled in via a hand-edited manifest that predates the
backend check.

## CSS/JS packaging — same-origin only

The current CSP (`frontend/nginx.conf` / `frontend/nginx.docker.conf`) is
`script-src 'self' <2 known hashes> https://cdn.jsdelivr.net` and
`style-src 'self' 'unsafe-inline' https://rsms.me`. `'self'` already covers
same-origin `<script src>` / `<link>` tags, so pack CSS/JS served from the
app's own origin needs **no CSP change** — only a cross-origin pack CDN
would, and this standard does not support one. A pack's CSS is a plain
stylesheet scoped by a wrapper class matching the pack's namespace (e.g.
`.example-desk-board`), imported directly by the widget's `.tsx` file —
`ExampleDeskBoardWidget.tsx` → `example-desk.css` is the reference. Do
**not** ship a pack asset from a cross-origin URL; there is no supported
path for the browser to load it under the current policy.

## Later — true hot-loading (not designed here)

Everything in the frontend view pipeline is static, Vite-build-time only —
`frontend/src/views/manifests/auto.ts` uses `import.meta.glob(...,
{ eager: true })`, and there is zero dynamic `import()` anywhere. A pack's
JS today is a hand-authored widget file compiled into the main bundle, same
as every built-in widget — not something that appears without a frontend
rebuild. A future phase could wire the currently-inert
`frontend/src/views/plugins/auto.ts` (`getDiscoveredPlugins()`, confirmed
zero callers today) into a real runtime resolver behind the existing
`ViewTypeSpec.hot_loadable` field (always `False` today). That is a
separate, larger design (signed bundle fetch, dynamic import, sandboxing)
and is explicitly out of scope for this v1 standard.

## Out of scope for v1

- **App-home pack views** — a pack replacing or extending an App's home
  surface is not designed here.
- **Dashboards** — a pack contributing a dashboard widget is not designed
  here.
- **Wizard steps** — `region_system`'s `create_wizard` step-kind registry
  is a related but separate extension point (see `REGION_SYSTEM.md`); a
  pack is not part of that surface in v1.
- **Settings forms** — a pack contributing to `app.settings_schema` is not
  designed here.
- **Full track/entry page-body replacement** — the most invasive form this
  standard could take, and explicitly cut: it would break the permissions
  UI, comments, and collaboration chrome every track/entry page wraps
  around its view content.

## Reference pack

`backend/app/plugins/example_desk/` registers two namespaced view types
proving every piece of this standard: `example-desk/desk-board`
(`scope="track"`, a card grid over a track's entries) and
`example-desk/desk-summary` (`scope="entry"`, a read-only tile bound to the
current entry). Frontend widgets:
`frontend/src/components/views/ExampleDeskBoardWidget.tsx` /
`ExampleDeskSummaryWidget.tsx`, manifests at
`frontend/src/views/manifests/example_desk_board.manifest.ts` /
`example_desk_summary.manifest.ts`, pack-scoped stylesheet at
`frontend/src/components/views/example-desk.css`. Deliberately small and
unopinionated — a worked example, not a feature-complete app.

## See also

- [PLUGINS.md](PLUGINS.md) — the underlying discovery/signing mechanism
  this standard builds on.
- [VIEW_PALETTE.md](VIEW_PALETTE.md) — the view-type contract catalogue and
  registry this standard's `scope` field extends.
- [COMPOSITES.md](COMPOSITES.md) — the no-code alternative for a "new type"
  that's really a configuration of an existing primitive; reach for a UI
  pack only when composites can't express it.
