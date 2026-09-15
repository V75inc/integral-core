"""Content-profile plugin: ``example-desk`` — the reference UI pack for the
UI Packs Standard (docs/content-profiles/UI_PACKS.md). Registers two
namespaced view types proving the standard end to end: a track-scoped
``example-desk/desk-board`` and an entry-scoped ``example-desk/desk-summary``.

Discovered and loaded automatically by
``app.services.content_profile_plugins.discover_and_register_plugins`` — a
directory scan of ``backend/app/plugins/`` at server startup (see
``app/main.py``). No existing registry file is edited to wire this in.

Both view types are generic, config-driven primitives — deliberately small
and unopinionated (this is a worked example, not a feature-complete app):

  - ``example-desk/desk-board``: a card grid over a track's entries, grouped
    by a select field. Config: ``group_by`` (field key), ``title?``.
  - ``example-desk/desk-summary``: a read-only single-value tile bound to
    the current entry. Config: ``field`` (field key to display), ``label?``.
"""

from __future__ import annotations

from typing import Any


def register(*, field_type_registry: Any, view_type_registry: Any) -> None:
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="example-desk/desk-board",
            label="Desk Board",
            description=(
                "Reference UI pack widget — a card grid over a track's "
                "entries, grouped by a select field."
            ),
            config_schema={
                "group_by": {
                    "type": "string",
                    "description": "Field key to group cards by.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional board heading.",
                },
            },
            source="plugin",
            scope="track",
            palette_group="core",
        )
    )
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="example-desk/desk-summary",
            label="Desk Summary",
            description=(
                "Reference UI pack widget — a read-only single-value tile "
                "bound to the current entry."
            ),
            config_schema={
                "field": {
                    "type": "string",
                    "description": "Field key on the bound entry to display.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional tile label (defaults to the field key).",
                },
            },
            source="plugin",
            scope="entry",
            palette_group="core",
        )
    )
