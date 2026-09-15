"""Compatibility shim for moved backend view-type registry."""

from app.views.content_profile_view_types import (
    ViewTypeSpec,
    allowed_keys,
    get,
    is_known,
    iter_specs,
    make_composite_spec,
    primitive_for,
    register_view_type,
    registry_version,
    resolve,
)

__all__ = [
    "ViewTypeSpec",
    "allowed_keys",
    "get",
    "is_known",
    "iter_specs",
    "make_composite_spec",
    "primitive_for",
    "register_view_type",
    "registry_version",
    "resolve",
]
