"""Content profile field-type registry.

Pillar 1 of the agent-authorable substrate. Replaces the hardcoded
``VALID_FIELD_TYPES`` set in ``content_profile_runtime`` with an extensible
registry. Built-ins register at module import time; plugins call
``register_field_type()`` at startup; manifest-declared composites are resolved
per-compile via ``resolve()`` and never persisted in the global registry.

The registry today is metadata-first: validators and coercers stay inline in
``content_profile_runtime.validate_and_materialize_entry_custom_fields`` for
the primitives. The optional ``validator`` / ``coercer`` / ``materializer``
callables on :class:`FieldTypeSpec` reserve the contract for future plugin
field types that need to ship their own logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class FieldTypeSpec:
    """Declarative spec for a content-profile field type.

    ``base`` is set for composite types that decorate a primitive with extra
    config (e.g. ``currency = number + {currency: USD}``). Composite specs are
    constructed per-compile from manifest ``field_types[]`` and resolved via
    ``resolve(type, profile_composites=...)``; they are not registered
    globally.
    """

    type: str
    base: Optional[str] = None
    config_schema: Dict[str, Any] = field(default_factory=dict)
    default_value: Any = None
    coercer: Optional[Callable[[Any, Dict[str, Any]], Any]] = None
    validator: Optional[Callable[[Any, Dict[str, Any]], None]] = None
    materializer: Optional[Callable[..., Awaitable[None]]] = None
    source: str = "builtin"  # builtin | composite | plugin
    signed: bool = False
    label: str = ""
    description: str = ""
    composite_config: Dict[str, Any] = field(default_factory=dict)


_REGISTRY: Dict[str, FieldTypeSpec] = {}
_REGISTRY_VERSION: int = 0


def register_field_type(spec: FieldTypeSpec, *, override: bool = False) -> None:
    """Register a field-type spec globally.

    Built-ins call this at module import. Plugins call this at startup.
    Composites declared in a manifest must NOT call this — use
    :func:`make_composite_spec` and pass through ``profile_composites`` to
    :func:`resolve` instead.
    """
    global _REGISTRY_VERSION
    if not spec.type:
        raise ValueError("FieldTypeSpec.type required")
    if spec.type in _REGISTRY and not override:
        raise ValueError(f"field type '{spec.type}' already registered")
    _REGISTRY[spec.type] = spec
    _REGISTRY_VERSION += 1


def get(type_: str) -> Optional[FieldTypeSpec]:
    """Return the global registration for ``type_`` (no composite resolution)."""
    return _REGISTRY.get(type_)


def is_known(type_: str) -> bool:
    """True if ``type_`` is registered globally (not including composites)."""
    return type_ in _REGISTRY


def iter_specs() -> Iterable[FieldTypeSpec]:
    """Return all registered ``FieldTypeSpec`` instances."""
    return list(_REGISTRY.values())


def allowed_keys() -> List[str]:
    """Return the sorted list of registered field-type keys."""
    return sorted(_REGISTRY.keys())


def registry_version() -> int:
    """Monotonic counter for cache invalidation in manifest compilation."""
    return _REGISTRY_VERSION


def resolve(
    type_: str,
    *,
    profile_composites: Optional[Dict[str, FieldTypeSpec]] = None,
) -> Optional[FieldTypeSpec]:
    """Resolve a type name to its spec.

    Lookup order:
      1. ``profile_composites`` (manifest-declared composites for this compile)
      2. global registry (builtins + plugins)

    Returns None if the type is not known. Callers raise the appropriate
    BadRequestError with a helpful message.
    """
    if profile_composites and type_ in profile_composites:
        return profile_composites[type_]
    return _REGISTRY.get(type_)


def primitive_for(spec: FieldTypeSpec) -> str:
    """Return the underlying primitive type name for ``spec``.

    Walks the composite chain via ``base`` until a non-composite spec is
    found. Used by the runtime validator to dispatch validation against the
    underlying primitive's rules.
    """
    seen: List[str] = []
    cur = spec
    while cur.base:
        if cur.type in seen:
            raise ValueError(f"composite cycle detected for field type '{cur.type}'")
        seen.append(cur.type)
        nxt = _REGISTRY.get(cur.base)
        if nxt is None:
            # Resolved against profile composites externally; treat base as
            # the primitive name for fallback validation.
            return cur.base
        cur = nxt
    return cur.type


def make_composite_spec(
    *,
    key: str,
    base: str,
    config: Optional[Dict[str, Any]] = None,
    label: str = "",
    description: str = "",
) -> FieldTypeSpec:
    """Build a composite FieldTypeSpec for per-compile registration.

    Used by ``content_profile_runtime.compile_canonical_manifest`` to
    materialize ``manifest.field_types[]`` entries.
    """
    return FieldTypeSpec(
        type=key,
        base=base,
        config_schema={},
        composite_config=dict(config or {}),
        source="composite",
        label=label or key,
        description=description,
    )


# ---------------------------------------------------------------------------
# Built-in primitive registrations
# ---------------------------------------------------------------------------
#
# Validation/coercion stays inline in content_profile_runtime for the
# primitives; these registrations exist so that:
#   1. ``is_known()`` replaces the hardcoded ``VALID_FIELD_TYPES`` set.
#   2. Plugins and composites have a uniform registry to extend.
#   3. The introspection endpoint (``GET /api/content-profile-substrate``) and
#      the agent's ``integral_describe_substrate`` can enumerate types.

_BUILTIN_FIELD_TYPES: List[FieldTypeSpec] = [
    FieldTypeSpec(
        type="text",
        label="Text",
        description="Single-line free text.",
    ),
    FieldTypeSpec(
        type="number",
        label="Number",
        description="Numeric value (int or float).",
    ),
    FieldTypeSpec(
        type="boolean",
        label="Boolean",
        description="True/false toggle.",
    ),
    FieldTypeSpec(
        type="date",
        label="Date",
        description="Calendar date (YYYY-MM-DD).",
    ),
    FieldTypeSpec(
        type="datetime",
        label="Date + time",
        description="Timestamp (ISO 8601).",
    ),
    FieldTypeSpec(
        type="markdown",
        label="Markdown",
        description="Multi-line markdown text.",
    ),
    FieldTypeSpec(
        type="json",
        label="JSON",
        description="Arbitrary JSON value.",
    ),
    FieldTypeSpec(
        type="select",
        label="Select",
        description="Single choice from an enum list.",
    ),
    FieldTypeSpec(
        type="multi_select",
        label="Multi-select",
        description="Multiple choices from an enum list.",
    ),
    FieldTypeSpec(
        type="relation",
        label="Relation",
        description="Reference one or more entries by id.",
    ),
    FieldTypeSpec(
        type="computed",
        label="Computed",
        description="Read-only derived value.",
    ),
    FieldTypeSpec(
        type="file",
        label="File",
        description="Single attachment id.",
    ),
    FieldTypeSpec(
        type="files",
        label="Files",
        description="List of attachment ids (capped by config.max_count).",
    ),
    # Member reference. Targets a Workspace
    # member's User account (NOT an Entry or Track). Validator + edge
    # materialization wired below; ``base="relation"`` keeps the manifest
    # primitive routing on the same code path that handles entry/track
    # targets while the runtime dispatches the workspace-gate validator
    # and the HAS_MEMBER_REF edge write through the single-writer helper
    # ``_sync_member_ref_edges`` in ``content_profile_graph.py``.
    FieldTypeSpec(
        type="member",
        base="relation",
        label="Member",
        description=(
            "Links the entry to a workspace member's user account. The "
            "referenced user must belong to the same Workspace as the entry."
        ),
    ),
]

for _spec in _BUILTIN_FIELD_TYPES:
    register_field_type(_spec)
