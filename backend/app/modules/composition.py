"""Core module composition for the modular-monolith boundary.

This is intentionally small: it assembles public module interfaces while
their existing service implementations remain in place.  HTTP, resident, and
application dispatch paths can depend on this object without importing a
particular module's singleton directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.intelligence import (
    IntelligenceRuntimeStatus,
    intelligence_runtime_status,
)
from app.modules.policy import PolicyModule, policy_module


@dataclass(frozen=True)
class CoreModules:
    """Public interfaces composed for one Integral Core process."""

    policy: PolicyModule

    def intelligence_status(self) -> IntelligenceRuntimeStatus:
        """Read optional intelligence availability without coupling callers."""
        return intelligence_runtime_status()


_core_modules = CoreModules(policy=policy_module)


def core_modules() -> CoreModules:
    """Return the process-wide Core module composition."""
    return _core_modules


__all__ = ["CoreModules", "core_modules"]
