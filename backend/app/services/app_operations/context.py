"""OperationContext — ToolContext + app-scoped operation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.hooks.registry import ToolContext


@dataclass
class OperationContext(ToolContext):
    """Runtime context injected into app operation handlers."""

    app_id: str = ""
    operation_key: str = ""
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None
