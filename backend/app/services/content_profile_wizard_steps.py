"""Content profile create_wizard step-kind registry.

Same registry shape as ``content_profile_field_types.py`` /
``app.views.content_profile_view_types`` (Pillar 1 + Pillar 4 of the
agent-authorable substrate) — the registry lives here in core so
``content_profile_compile.py`` can validate against it, but the registry
starts EMPTY at import: every step kind, including the four originally
shipped with the ``create_wizard`` primitive (``form``, ``entry_checklist``,
``period_picker``, ``summary``), is registered by ``app/plugins/
region_system/__init__.py`` at plugin discovery, the same way region_system
registers its view types. A wizard step is conceptually a region rendered
one-at-a-time with Next/Back navigation instead of a fixed layout — this
registry is what makes that ownership explicit instead of the four kinds
being hardcoded inline in ``_normalize_create_wizard`` forever, and it's
what makes the whole ``create_wizard`` primitive genuinely part of the same
bundle as ``layout_container``/``form_region``/etc., not a separate
substrate feature that happens to sit in the same repo.

No built-ins register here (unlike ``content_profile_view_types.py``,
which seeds feed/table/kanban/etc. as true framework primitives) — every
current step kind was introduced alongside region_system in the same body
of work, so there is no "always been core" kind to seed ahead of a plugin.
If region_system is ever not discovered, ``create_wizard`` correctly fails
to validate any step, the same way disabling region_system already makes
``layout_container`` an "Unsupported view type."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class WizardStepKindSpec:
    """Declarative spec for a create_wizard step kind.

    ``config_schema`` documents the step-level config keys this kind reads
    (beyond the universal ``key``/``title`` every step carries) — same
    contract role ``ViewTypeSpec.config_schema`` plays for view types.
    """

    kind: str
    label: str = ""
    description: str = ""
    config_schema: Dict[str, Any] = field(default_factory=dict)
    source: str = "plugin"  # builtin | plugin


_REGISTRY: Dict[str, WizardStepKindSpec] = {}


def register_step_kind(spec: WizardStepKindSpec, *, override: bool = False) -> None:
    """Register a ``WizardStepKindSpec`` in the global registry."""
    if not spec.kind:
        raise ValueError("WizardStepKindSpec.kind required")
    if spec.kind in _REGISTRY and not override:
        raise ValueError(f"wizard step kind '{spec.kind}' already registered")
    _REGISTRY[spec.kind] = spec


def get(kind: str) -> Optional[WizardStepKindSpec]:
    """Return the ``WizardStepKindSpec`` for ``kind`` or ``None`` if unknown."""
    return _REGISTRY.get(kind)


def is_known(kind: str) -> bool:
    """Return True if ``kind`` is registered globally."""
    return kind in _REGISTRY


def list_step_kinds() -> Dict[str, WizardStepKindSpec]:
    """Return a shallow copy of the registry — for introspection/palette use."""
    return dict(_REGISTRY)
