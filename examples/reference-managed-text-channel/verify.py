"""Run the installed sample's synthetic lifecycle; never starts a server."""

import asyncio

from app.connectors.channel_package_discovery import (
    ApprovedChannelDistribution,
    load_approved_channel_package,
)
from app.connectors.channel_packages import ChannelPackageRegistry, ChannelScope


async def verify() -> None:
    """Prove discovery, disabled Add, enable, disable and removal in one process."""
    registry = ChannelPackageRegistry()
    approval = ApprovedChannelDistribution(
        distribution="integral-external-channel-fixture",
        version="1.0.0",
        slug="external_test_channel",
        factory="integral_external_channel_fixture:build_channel",
    )
    package = load_approved_channel_package(registry, approval)
    # Only a test context; real Core API derives both fields from authentication.
    scope = ChannelScope("synthetic-manager", "synthetic-workspace")

    async def expect(state: str) -> None:
        (row,) = await registry.discover(scope)
        if row.state != state:
            raise RuntimeError(f"Expected {state}, got {row.state}")
        print(f"{row.action}: {row.state}")

    await expect("registered")
    installed = await package.lifecycle.install(scope)
    await expect("inactive")
    await package.lifecycle.set_enabled(scope, installed, True)
    await expect("active")
    current = await package.lifecycle.observe(scope)
    if current is None or current.route_epoch == installed.route_epoch:
        raise RuntimeError("Enablement must observe a fresh epoch")
    await package.lifecycle.set_enabled(scope, current, False)
    await expect("inactive")
    current = await package.lifecycle.observe(scope)
    if current is None:
        raise RuntimeError("Disablement must retain the installed record")
    await package.lifecycle.remove(scope, current)
    await expect("registered")


if __name__ == "__main__":
    asyncio.run(verify())
