"""Schemas for cross-App relations + dependencies (Phase 10 Plan 10-06).

Wire shapes for:
  - Restricted-relation stub (Architectural Decision 8 Option A — strict).
  - Cross-App uninstall 409 ``details`` payload.
  - Cross-App relation reference set (for /entries/* write surfaces).

The restricted stub is the data-leak vector mitigation (Risk 4). Its shape
is intentionally minimal — three keys, no source-field content:

    {
      "kind": "restricted_relation",
      "relation_field_key": "<field key from source manifest>",
      "target_resource_kind": "entry"
    }

NO label, NO target entry id, NO target App id, NO target App name. Anything
else here is a data leak. The frontend's ``RestrictedRelationStub`` consumes
this shape and renders a generic "Restricted" label with a tooltip — the
relation_field_key is used ONLY in the accessibility tooltip, not in the
visual surface (Architectural Decision 8).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class RestrictedRelationStub(BaseModel):
    """Strict restricted-relation shape per Architectural Decision 8 Option A.

    Returned by ``read_cross_app_label`` when ``policy_engine.evaluate`` denies
    the viewer's ``entry.read`` request on the cross-App target. This is the
    SINGLE permitted shape for restricted cross-App relation rendering.

    Three keys, no source-field content. The grep-gate test in
    ``tests/test_cross_app_relations.py`` asserts ``label_field`` is never read
    outside ``relation_runtime.py`` to prevent inline-bypass leaks.
    """

    kind: Literal["restricted_relation"] = "restricted_relation"
    relation_field_key: str
    target_resource_kind: Literal["entry"] = "entry"

    model_config = {"extra": "forbid"}


class CrossAppRelationLabel(BaseModel):
    """Permitted shape when viewer has access to the cross-App target.

    Returned by ``read_cross_app_label`` on allowed reads. Carries the rendered
    label plus pointers the frontend needs to deep-link. NOT used on denial —
    denial returns ``RestrictedRelationStub`` with NONE of these fields.
    """

    kind: Literal["relation"] = "relation"
    relation_field_key: str
    target_entry_id: str
    target_app_id: str
    target_app_name: str
    label: str

    model_config = {"extra": "forbid"}


class BlockingDependent(BaseModel):
    """One row in the uninstall-blocked 409 ``details.blocking_dependents`` list.

    Surfaced when manifest-level walk detects another App in the workspace
    declares this App via ``requires_apps[]`` with ``optional: false`` (Pitfall
    6 manifest-level walk).
    """

    app_id: str
    app_name: str
    dep_key: str

    model_config = {"extra": "forbid"}


class BlockingReference(BaseModel):
    """One row in the uninstall-blocked 409 ``details.blocking_references`` list.

    Surfaced when edge-level walk finds REFERENCES edges with
    ``target_app_id == this_app.id`` whose source field declares
    ``on_target_uninstall: block`` (Pitfall 6 edge-level walk).
    """

    source_app_id: str
    source_app_name: str
    source_entry_id: str
    source_track_id: str
    relation_field_key: str

    model_config = {"extra": "forbid"}


class UninstallBlockedDetails(BaseModel):
    """``AppUninstallBlockedError.details`` payload shape (Phase 10 Plan 10-06).

    Both lists are surfaced so the frontend uninstall modal can show
    "Uninstalling HR is blocked by: Payroll App, 12 Payroll-Run entries
    reference 5 HR-Employee entries".
    """

    blocking_dependents: List[BlockingDependent] = []
    blocking_references: List[BlockingReference] = []
    force_url: Optional[str] = None

    model_config = {"extra": "forbid"}


# Manifest schema mirrors (compile-time normalized shape — not authoritative).
# Kept as plain dicts in compile_canonical_manifest; this class exists only as
# a typing aid for code that consumes the cross-App relation surface.


class CrossAppRelationSpec(BaseModel):
    """Normalized cross-App relation spec (compile_canonical_manifest output).

    Subset of the relation dict produced by the compiler. Other fields
    (target_entry_types, governance, etc.) are present in the runtime dict but
    elided here — this class is the cross-App-specific lens.
    """

    target_app: str
    allow_cross_app: bool
    label_field: Optional[str] = None
    on_target_uninstall: Literal["block", "null", "archive_self"] = "block"
    resolution: str  # "workspace" | "instance:<app_id>"

    model_config = {"extra": "forbid"}


class RequiresAppsEntry(BaseModel):
    """One entry in ``app.requires_apps[]`` post-normalization."""

    key: str
    min_version: str = "0.0.0"
    optional: bool = False
    reason: str = ""

    model_config = {"extra": "forbid"}


__all__ = [
    "BlockingDependent",
    "BlockingReference",
    "CrossAppRelationLabel",
    "CrossAppRelationSpec",
    "RequiresAppsEntry",
    "RestrictedRelationStub",
    "UninstallBlockedDetails",
]
