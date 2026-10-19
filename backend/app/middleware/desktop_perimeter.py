"""Compatibility import for the v1 local transport middleware."""

from app.runtime.transport import (  # noqa: F401
    LocalTransport as DesktopPerimeterMiddleware,
)
from app.runtime.transport import blocked_path  # noqa: F401
