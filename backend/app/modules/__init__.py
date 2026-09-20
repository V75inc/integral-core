"""Public module seams for Integral Core's modular monolith.

Modules are composition boundaries, not replacement domain models.  Existing
services remain in place while their public adapters become the only new
cross-module entry points.
"""

from app.modules.intelligence import intelligence_runtime_status
from app.modules.policy import PolicyModule, policy_module

__all__ = ["PolicyModule", "intelligence_runtime_status", "policy_module"]
