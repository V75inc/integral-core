"""Content-profile plugin: ``grouped-list/by-relation`` — a UI pack
(docs/content-profiles/UI_PACKS.md) registering one namespaced view type:
a flat track's entries grouped into sections by any field, with correct
label resolution when that field is a relation.

Discovered and loaded automatically by
``app.services.content_profile_plugins.discover_and_register_plugins`` — a
directory scan of ``backend/app/plugins/`` at server startup (see
``app/main.py``). No existing registry file is edited to wire this in.

Why a new view type instead of extending ``composable_list`` (which already
has a ``group_by`` config): confirmed by reading ``ComposableList.tsx`` that
its group headers render the raw grouped field value — fine for a plain
select field, but a ``relation``-typed ``group_by`` would show the related
Entry's raw id as the section heading instead of its actual label (an
employee's name, say). ``composable_list`` is a shared primitive several
other apps' manifests already reference; broadening its contract is a
bigger, riskier change than shipping this as its own small, additive
widget purpose-built for the relation-grouped case. Same reasoning
``payroll_register`` already documents for not extending ``editable_table``.

Generic and app-agnostic — any content-profile manifest can declare a
track view with ``view_type: grouped-list/by-relation``, not just Payroll's.
Guyana/Aruba/BVI Payroll are the first callers (Compensation Records /
Payslips grouped by their ``employee`` relation field), not the only
intended ones.

``grouped-list/by-relation``: entries from a track grouped by any field.
Config:
  - ``group_by``: field key to group entries by. Works for any field type;
    when it's ``relation``-typed, each group's heading/row resolves and
    displays the related Entry's label instead of its raw id.
  - ``mode``: ``"sections"`` (default) — every group's entries listed
    inline underneath its own heading, all groups on screen at once.
    ``"directory"`` — a master/detail drill-down, in place (no navigating
    away): a directory of groups first (one row + entry count each);
    clicking a row swaps the same view to that one group's entries, with
    a "Back" affordance to return to the directory (e.g. Compensation
    Records' "By Employee" tab: pick an employee, see just their records,
    go back, pick another — never leaving the Compensation Records
    track). Only meaningful when ``group_by`` is relation-typed (its
    label is what rows/headings show); falls back to ``sections``
    otherwise.
  - ``directory_layout``: ``"list"`` (default) or ``"folders"`` — only
    affects the directory step of ``mode: "directory"``. ``"list"``:
    stacked rows, one per group. ``"folders"``: a grid of folder-icon
    tiles instead of rows; same click-to-drill-down behavior either way.
    (Named ``directory_layout``, not ``layout`` — the compiler already
    reserves a top-level ``view.layout`` / ``view.config.layout`` object
    for something else; colliding with it broke compilation the first
    time this shipped.)
  - ``title``: optional heading above the whole list.
  - ``empty_group_label``: label for entries whose ``group_by`` value is
    empty (default: "Unassigned").
"""

from __future__ import annotations

from typing import Any


def register(*, field_type_registry: Any, view_type_registry: Any) -> None:
    """Plugin entry point — called by ``discover_and_register_plugins``."""
    view_type_registry.register_view_type(
        view_type_registry.ViewTypeSpec(
            type="grouped-list/by-relation",
            label="Grouped List",
            description=(
                "Entries grouped into sections by any field — when that "
                "field is a relation, each section heading shows the "
                "related entry's label (e.g. an employee's name) rather "
                "than its raw id."
            ),
            config_schema={
                "group_by": {
                    "type": "string",
                    "description": "Field key to group entries by.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["sections", "directory"],
                    "description": (
                        "'sections' (default): groups with entries listed "
                        "inline. 'directory': master/detail drill-down in "
                        "place — a directory of groups, click one to see "
                        "just its entries, Back to return — relation-typed "
                        "group_by only."
                    ),
                },
                "directory_layout": {
                    "type": "string",
                    "enum": ["list", "folders"],
                    "description": (
                        "Directory step only ('mode: directory'). 'list' "
                        "(default): stacked rows. 'folders': a grid of "
                        "folder-icon tiles instead of rows."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional heading above the list.",
                },
                "empty_group_label": {
                    "type": "string",
                    "description": (
                        "Label for entries whose group_by value is empty "
                        "(default: 'Unassigned')."
                    ),
                },
            },
            source="plugin",
            palette_group="core",
            scope="track",
        )
    )
