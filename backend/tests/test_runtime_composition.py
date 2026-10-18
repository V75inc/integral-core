"""Runtime limits are additional to identity, resource and workspace authority."""

from dataclasses import FrozenInstanceError
from typing import get_args
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.config import Settings, settings
from app.runtime.composition import (
    HOSTED,
    LOCAL,
    admits_human,
    capabilities,
    owner_ids,
    require_capability,
    runtime,
)
from app.runtime.identity import AccountIdentity, InstallationIdentity, identity_adapter
from app.runtime.operations import OPERATION_GROUPS
from app.schemas.policy import PolicyAction, Resource, Subject
from app.services.policy_engine import evaluate

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.fixture
def select(monkeypatch):
    def apply(preset, legacy="server"):
        monkeypatch.setattr(settings, "INTEGRAL_RUNTIME_PRESET", preset)
        monkeypatch.setattr(settings, "INTEGRAL_DEPLOYMENT_PROFILE", legacy)
        monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_INSTALLATION_ID", "d" * 32)

    return apply


@pytest.mark.parametrize(
    "preset,legacy,expected",
    [
        (None, "server", HOSTED),
        (None, "desktop_single_user", LOCAL),
        ("hosted", "server", HOSTED),
        ("local", "server", LOCAL),
        ("local", "desktop_single_user", LOCAL),
    ],
)
def test_presets_and_existing_profile_aliases(select, preset, legacy, expected):
    select(preset, legacy)
    assert runtime() is expected
    assert capabilities()["profile"] == expected.legacy_profile
    assert capabilities()["runtime"]["preset"] == expected.preset
    with pytest.raises(FrozenInstanceError):
        expected.preset = "other"


def test_invalid_and_conflicting_presets_fail_closed(select):
    select("hosted", "desktop_single_user")
    with pytest.raises(RuntimeError, match="composition"):
        runtime()
    with pytest.raises(ValueError):
        Settings(_env_file=None, INTEGRAL_RUNTIME_PRESET="anything")


def test_every_declared_policy_action_has_explicit_runtime_metadata():
    assert set(get_args(PolicyAction)) <= OPERATION_GROUPS.keys()
    with pytest.raises(TypeError):
        OPERATION_GROUPS["future.invitation_accept"] = "records"


@pytest.mark.parametrize("preset", ["local", "hosted"])
@pytest.mark.parametrize("kind", ["human", "agent", "connector", "system"])
@pytest.mark.parametrize("internal", [False, True])
async def test_unknown_actions_cannot_inherit_policy_or_system_bypass(
    select, preset, kind, internal
):
    select(preset)
    decision = await evaluate(
        subject=Subject(kind=kind, id=owner_ids()[1]),
        action="future.collaboration_grant",
        resource=Resource(kind="workspace", id="missing", scope="ws:missing"),
        _dry_run=True,
        _internal_actor=(
            Subject(kind="system", id="policy_engine") if internal else None
        ),
    )
    assert not decision.allowed
    assert decision.reason == "deployment_profile_denied"


def test_local_identity_is_distinct_from_product_signup_and_collaboration(select):
    select("local")
    assert admits_human(owner_ids()[0]) and admits_human(owner_ids()[1])
    assert not admits_human("another-account")
    assert runtime().workspace_kinds == {"personal"}
    assert runtime().supports_action("entry.create")
    assert runtime().supports_action("app:installed-app:custom-operation")
    from app.api.errors import InsufficientPermissionsError

    for capability in [
        "identity_admission",
        "collaboration",
        "public_sharing",
        "unknown",
    ]:
        with pytest.raises(InsufficientPermissionsError):
            require_capability(capability)


@pytest.mark.parametrize(
    "preset,adapter", [("local", InstallationIdentity), ("hosted", AccountIdentity)]
)
def test_identity_adapter_is_selected_from_the_validated_preset(
    select, preset, adapter
):
    select(preset)
    assert isinstance(identity_adapter(), adapter)


async def test_hosted_never_exposes_the_controller_session(select):
    from app.api.errors import ResourceNotFoundError

    select("hosted")
    with pytest.raises(ResourceNotFoundError):
        await identity_adapter().controller_session(
            Request({"type": "http", "headers": []})
        )


async def test_each_identity_adapter_invokes_its_own_bootstrap(select, monkeypatch):
    from app import bootstrap_admin
    from app.runtime import local_identity

    local_bootstrap, hosted_bootstrap = AsyncMock(), AsyncMock()
    monkeypatch.setattr(local_identity, "bootstrap_owner", local_bootstrap)
    monkeypatch.setattr(bootstrap_admin, "bootstrap_admin_if_needed", hosted_bootstrap)
    select("local")
    await identity_adapter().bootstrap()
    local_bootstrap.assert_awaited_once()
    hosted_bootstrap.assert_not_awaited()
    select("hosted")
    await identity_adapter().bootstrap()
    hosted_bootstrap.assert_awaited_once()


async def test_supported_hosted_operations_still_need_resource_authority(select):
    select("hosted")
    require_capability("collaboration")
    decision = await evaluate(
        subject=Subject(kind="agent", id="untrusted-agent"),
        action="entry.create",
        resource=Resource(kind="entry", id="missing", scope="ws:foreign"),
        _dry_run=True,
    )
    assert not decision.allowed


def test_managed_launcher_overrides_ambient_runtime_preset(tmp_path, monkeypatch):
    from app import local_runtime

    monkeypatch.setenv("INTEGRAL_RUNTIME_PRESET", "hosted")
    config = local_runtime.initialize(tmp_path, profile="desktop_single_user")
    before = (tmp_path / "installation.json").read_bytes()
    env = local_runtime._environment(
        tmp_path, config, "postgresql://unused", 14000, 19006, None
    )
    assert env["INTEGRAL_RUNTIME_PRESET"] == "local"
    assert (tmp_path / "installation.json").read_bytes() == before


def test_local_transport_prerequisites_and_hosted_independence(select, monkeypatch):
    """Local controller requirements cannot accidentally restrict hosted boot."""
    from app.runtime.transport import validate_process

    select("hosted")
    validate_process(2)
    select("local")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("JVSPATIAL_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_CONTROL_TOKEN", "c" * 64)
    monkeypatch.setattr(
        settings, "INTEGRAL_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:19006"
    )
    validate_process(1)
    with pytest.raises(RuntimeError, match="managed loopback"):
        validate_process(2)
    monkeypatch.setattr(
        settings, "INTEGRAL_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:19006/path"
    )
    with pytest.raises(RuntimeError, match="managed loopback"):
        validate_process(1)
