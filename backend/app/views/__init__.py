"""Backend view subsystem: contracts + content-profile view type registry."""

from app.views.content_profile_view_types import (  # noqa: F401
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
from app.views.view_contract_catalog import load_view_contract_catalog  # noqa: F401

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
    "load_view_contract_catalog",
]
