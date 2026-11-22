"""Distribution selection does not import unauthorized channel packages."""

import asyncio
import sys
from dataclasses import replace
from importlib import metadata, util
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    VerifiedExternalChatMessage,
)
from app.connectors.channel_package_discovery import (
    ApprovedChannelDistribution,
    ChannelPackageDiscoveryError,
    load_approved_channel_package,
)
from app.connectors.channel_packages import ChannelPackageRegistry, ChannelScope

pytestmark = pytest.mark.unit
MODULE = "integral_external_channel_fixture"
APPROVAL = ApprovedChannelDistribution(
    "integral-external-channel-fixture",
    "1.0.0",
    "external_test_channel",
    f"{MODULE}:build_channel",
)
FIXTURE = Path(__file__).parent / "fixtures" / "managed_channel_distribution"


@pytest.fixture
def installed_wheel(tmp_path, monkeypatch):
    """Materialize a real external wheel and import only via distribution metadata."""
    builder_path = FIXTURE / "build_fixture_wheel.py"
    spec = util.spec_from_file_location("test_wheel_builder", builder_path)
    builder = util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    wheel = builder.build(tmp_path)
    site_packages = tmp_path / "external-site"
    with ZipFile(wheel) as archive:
        archive.extractall(site_packages)  # Created here from first-party test fixture.
    previous = sys.modules.pop(MODULE, None)
    monkeypatch.syspath_prepend(str(site_packages))
    try:
        yield wheel, site_packages
    finally:
        sys.modules.pop(MODULE, None)
        if previous is not None:
            sys.modules[MODULE] = previous


@pytest.mark.parametrize(
    "approval",
    [
        None,
        replace(APPROVAL, distribution="unapproved-fixture-dist"),
        replace(APPROVAL, version="2.0.0"),
        replace(APPROVAL, version=">=1.0.0"),
        replace(APPROVAL, slug="another_channel"),
        replace(APPROVAL, factory=f"{MODULE}:another_factory"),
        replace(APPROVAL, factory=f"{MODULE}.nested:run"),
    ],
)
def test_not_approved_means_no_import_or_registration(installed_wheel, approval):
    """Reject missing or mismatched approval before loading any code."""
    registry = ChannelPackageRegistry()
    with pytest.raises(ChannelPackageDiscoveryError):
        load_approved_channel_package(registry, approval)
    assert MODULE not in sys.modules
    assert registry.resolve(APPROVAL.slug) is None


def test_external_distribution_registers_and_uses_its_own_lifecycle(installed_wheel):
    """A separately installed module provides both the catalogue and hooks."""
    _, site = installed_wheel
    registry = ChannelPackageRegistry()
    package = load_approved_channel_package(registry, APPROVAL)
    assert registry.resolve(APPROVAL.slug) is package
    assert Path(sys.modules[MODULE].__file__).resolve() == (site / f"{MODULE}.py")
    with pytest.raises(ChannelPackageDiscoveryError, match="already registered"):
        load_approved_channel_package(registry, APPROVAL)

    async def lifecycle():
        scope = ChannelScope("manager", "workspace-a")
        unrelated = ChannelScope("manager", "workspace-b")
        (available,) = await registry.discover(scope)
        assert available.state == "registered"
        installed = await package.lifecycle.install(scope)
        assert (await registry.discover(scope))[0].state == "inactive"
        assert (await registry.discover(unrelated))[0].state == "registered"
        assert await package.lifecycle.observe(unrelated) is None
        key = ExternalChatEventKey(
            ExternalChatConversation(
                ExternalChatProvision(
                    scope.workspace_id, APPROVAL.slug, installed.provision_id
                ),
                "test-conversation",
            ),
            "test-event",
        )
        event = VerifiedExternalChatMessage(key, "test-sender", "synthetic text")
        with pytest.raises(ValueError):
            await package.serving.receive(event)
        await package.lifecycle.set_enabled(scope, installed, True)
        active = await package.lifecycle.observe(scope)
        assert active.route_epoch != installed.route_epoch
        assert (await registry.discover(scope))[0].state == "active"
        assert await package.serving.receive(event) is None
        with pytest.raises(ValueError):
            await package.lifecycle.remove(scope, installed)
        await package.lifecycle.set_enabled(scope, active, False)
        disabled = await package.lifecycle.observe(scope)
        assert disabled.route_epoch != active.route_epoch
        with pytest.raises(ValueError):
            await package.serving.receive(event)
        await package.lifecycle.remove(scope, disabled)
        assert (await registry.discover(scope))[0].state == "registered"

    asyncio.run(lifecycle())


def test_duplicate_distribution_or_entry_points_fail_before_import(
    installed_wheel, tmp_path, monkeypatch
):
    """A single exact target is required even if ambiguous choices look approved."""
    wheel, site = installed_wheel
    duplicate = tmp_path / "second-site"
    with ZipFile(wheel) as archive:
        archive.extractall(duplicate)
    with monkeypatch.context() as patch:
        patch.syspath_prepend(str(duplicate))
        registry = ChannelPackageRegistry()
        with pytest.raises(ChannelPackageDiscoveryError, match="missing or ambiguous"):
            load_approved_channel_package(registry, APPROVAL)
        assert MODULE not in sys.modules
    metadata_dir = next(site.glob("*.dist-info"))
    entry_points = metadata_dir / "entry_points.txt"
    entry_points.write_text(
        entry_points.read_text() + f"another_channel = {MODULE}:build_channel\n"
    )
    with pytest.raises(ChannelPackageDiscoveryError, match="declare one"):
        load_approved_channel_package(registry, APPROVAL)
    assert MODULE not in sys.modules
    assert registry.resolve(APPROVAL.slug) is None


def test_module_origin_must_match_distribution_even_if_already_imported(
    installed_wheel, tmp_path, monkeypatch
):
    """The same module name outside the approved wheel cannot supply the factory."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / f"{MODULE}.py").write_text("raise AssertionError('never import')\n")
    monkeypatch.syspath_prepend(str(shadow))
    registry = ChannelPackageRegistry()
    with pytest.raises(ChannelPackageDiscoveryError, match="shadowed"):
        load_approved_channel_package(registry, APPROVAL)
    assert MODULE not in sys.modules
    spec = util.spec_from_file_location(MODULE, shadow / f"{MODULE}.py")
    monkeypatch.setitem(sys.modules, MODULE, util.module_from_spec(spec))
    with pytest.raises(ChannelPackageDiscoveryError, match="shadowed"):
        load_approved_channel_package(registry, APPROVAL)
    assert registry.resolve(APPROVAL.slug) is None


def test_factory_return_and_completeness_still_gate_final_registration(
    installed_wheel, monkeypatch
):
    """An approved wheel still cannot reserve its slug with a bad package object."""
    original_load = metadata.EntryPoint.load

    def broken_factory(entry):
        factory = original_load(entry)
        return lambda: replace(factory(), serving=None)

    registry = ChannelPackageRegistry()
    with monkeypatch.context() as patch:
        patch.setattr(metadata.EntryPoint, "load", broken_factory)
        with pytest.raises(
            ChannelPackageDiscoveryError, match="could not be registered"
        ):
            load_approved_channel_package(registry, APPROVAL)
        assert registry.resolve(APPROVAL.slug) is None
    assert (
        load_approved_channel_package(registry, APPROVAL).definition.slug
        == APPROVAL.slug
    )
