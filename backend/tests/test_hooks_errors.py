"""Hook framework error types — all are JVSpatialAPIException subclasses."""

import pytest

from app.api.errors import JVSpatialAPIException


def test_hook_not_configured_is_jvspatial_exception():
    from app.services.hooks.errors import HookNotConfiguredError

    e = HookNotConfiguredError(
        message="no binding matched", details={"point": "entry.transform"}
    )
    assert isinstance(e, JVSpatialAPIException)


def test_ambiguous_hook_is_jvspatial_exception():
    from app.services.hooks.errors import AmbiguousHookError

    e = AmbiguousHookError(
        message="multiple bindings", details={"candidates": ["a", "b"]}
    )
    assert isinstance(e, JVSpatialAPIException)


def test_tool_validation_failed_is_jvspatial_exception():
    from app.services.hooks.errors import ToolValidationFailedError

    e = ToolValidationFailedError(message="schema mismatch")
    assert isinstance(e, JVSpatialAPIException)


def test_hook_misconfigured_is_jvspatial_exception():
    from app.services.hooks.errors import HookMisconfiguredError

    e = HookMisconfiguredError(message="declarative block missing required key")
    assert isinstance(e, JVSpatialAPIException)


def test_trust_tier_denied_is_jvspatial_exception():
    from app.services.hooks.errors import ToolTrustTierDeniedError

    e = ToolTrustTierDeniedError(message="bundle is untrusted; tools[] forbidden")
    assert isinstance(e, JVSpatialAPIException)
