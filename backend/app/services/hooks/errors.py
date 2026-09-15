"""Hook framework error types (DR-30-02).

All inherit JVSpatialAPIException so the standard error_handler emits
consistent JSON {error_code, message, details, timestamp, path}.
"""

from typing import Any, Dict, Optional

# Import the canonical base directly. Importing app.api.errors here would
# execute app.api's eager endpoint catalogue while the hook package is still
# initializing, creating a registry -> errors -> api -> precompute ->
# tool_dispatch -> errors cycle.
from jvspatial.api.exceptions import JVSpatialAPIException


class HookNotConfiguredError(JVSpatialAPIException):
    error_code = "hook_not_configured"
    status_code = 404

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details or {})


class AmbiguousHookError(JVSpatialAPIException):
    error_code = "ambiguous_hook"
    status_code = 400

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details or {})


class HookMisconfiguredError(JVSpatialAPIException):
    error_code = "hook_misconfigured"
    status_code = 500

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details or {})


class ToolValidationFailedError(JVSpatialAPIException):
    error_code = "tool_validation_failed"
    status_code = 400

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details or {})


class ToolTrustTierDeniedError(JVSpatialAPIException):
    error_code = "tool_trust_tier_denied"
    status_code = 403

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, details=details or {})
