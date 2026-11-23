"""Verify connector-owned defaults do not contribute visitor or share routes."""

import pytest

from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.channel_runtime import register_channel_runtime
from app.web.web_chat_connector_runtime import compose_web_chat_connector


def test_management_only_default_contributes_exact_package_and_no_routes():
    """Generic registration never mounts or changes the management-only default."""
    runtime = compose_web_chat_connector()
    registry = ChannelPackageRegistry()
    routes = register_channel_runtime(registry, runtime.contribution)
    assert routes.management == routes.visitor == routes.isolated == ()
    with pytest.raises(ValueError):
        register_channel_runtime(registry, runtime.contribution)
    assert registry.resolve("public_web_chat") is runtime.contribution.package
    assert (
        runtime.contribution.management_routes
        == runtime.contribution.visitor_routes
        == ()
    )
    assert runtime.reconcile is None and runtime.page_context is None


def test_untyped_serving_configuration_does_not_infer_public_routes():
    """There is no partial or ambient configuration fallback to serving."""
    with pytest.raises(TypeError, match="explicit reviewed host"):
        compose_web_chat_connector({"served_origin": "https://example.invalid"})
