# Operational Model Code Plugins

Pillar 1 escape hatch. Plugins ship genuinely-new primitive renderers
(Mermaid widget, 3D molecule view, regex-validated email field, etc.)
that cannot be expressed as declarative composites of existing
primitives. Plugins are **rare** by design — composites are the agent's
primary extension surface (see [COMPOSITES.md](COMPOSITES.md)).

A plugin that specifically adds a track/entry **view type** — the most
common case — is a **UI pack**: see [UI_PACKS.md](UI_PACKS.md) for the
full standard (pack manifest, view-type namespacing, placement `scope`,
CSS/JS packaging) built on top of the discovery mechanism this doc covers.

## Discovery

[`backend/app/services/operational_model_plugins.py`](../../backend/app/services/operational_model_plugins.py)
runs at server startup (`_startup` in `main.py`) and discovers plugins
from two sources:

1. **Python entry points** in group `integral.operational_model.plugins`.
2. **Directory scan** of `backend/app/plugins/` (each subdirectory is
   treated as a package).

Each plugin module exports a `register` function:

```python
# backend/app/plugins/my_widget/__init__.py

from app.services.operational_model_field_types import FieldTypeSpec
from app.views.operational_model_view_types import ViewTypeSpec


def register(*, field_type_registry, view_type_registry):
    field_type_registry.register_field_type(
        FieldTypeSpec(
            type="email",
            label="Email",
            description="Validated email address.",
            source="plugin",
        ),
    )
    view_type_registry.register_view_type(
        ViewTypeSpec(
            # Namespaced 'pack-name/view-name' — required for any NEW
            # plugin-sourced view type (see UI_PACKS.md). A handful of
            # view types registered before the standard existed are
            # grandfathered by an explicit allowlist in
            # operational_model_view_types.py; do not add to it.
            type="my-widget/mermaid",
            label="Mermaid diagram",
            description="Renders a Mermaid graph from a manifest field.",
            source="plugin",
        ),
    )
```

## Signature policy

- Dev (`INTEGRAL_PLUGIN_PUBKEY` unset): all discovered plugins load
  unconditionally with a logged warning. Useful for local development.
- Prod (`INTEGRAL_PLUGIN_PUBKEY` set): each plugin must ship a
  `signature.bin` file alongside its package; signatures are verified
  via Ed25519 against the configured pubkey. Unsigned plugins are
  skipped with an error.

The signing pipeline + bundle format are operationally minimal in v1
and reserved for hardening in a later iteration.

## Frontend plugin auto-loader

[`frontend/src/views/plugins/auto.ts`](../../frontend/src/views/plugins/auto.ts)
fetches `/api/operational-model-substrate` on app boot and caches it via
`getDiscoveredPlugins()` — reserved for the "Later" true hot-loading phase
(see [UI_PACKS.md](UI_PACKS.md)), not yet wired to anything: confirmed zero
callers today, so it does **not** currently feed `MissingWidget` /
`MissingFieldType` fallbacks. Real dynamic-import of signed JS bundles is
reserved until the signing infrastructure lands; today plugin frontends
must be bundled with the app build.

## Manifest declaration

A Operational Model that requires a code plugin declares the dependency so missing
installs surface clearly:

```yaml
plugins:
  - { id: timeline-widget, version: ">=1.2", signature: <base64> }
```

## Tests

- `backend/tests/test_operational_model_plugins.py` — directory discovery,
  duplicate guard, idempotent re-discovery, hidden-dir skip.
