"""Patched MCP stays on the supported 1.x API and stateless transport."""

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

pytestmark = pytest.mark.unit


def mcp_requirement():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    return next(
        Requirement(value)
        for value in project["project"]["dependencies"]
        if Requirement(value).name == "mcp"
    )


@pytest.mark.parametrize(
    "candidate,allowed",
    [
        ("1.27.0", False),
        ("1.28.1", False),
        ("1.29.1", False),
        ("1.30.0", True),
        ("2.0.0", False),
    ],
)
def test_patched_dependency_floor_and_existing_major_boundary(candidate, allowed):
    assert (Version(candidate) in mcp_requirement().specifier) is allowed


def test_frozen_and_installed_mcp_meet_the_supported_security_range():
    requirement = mcp_requirement()
    lock = tomllib.loads((Path(__file__).parents[1] / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "mcp")
    assert Version(package["version"]) in requirement.specifier
    assert Version(version("mcp")) in requirement.specifier


def test_supported_server_factory_keeps_stateless_transport():
    from app.agentive.mcp.server import build_session_manager

    server, manager = build_session_manager()
    assert callable(server.list_tools)
    assert manager.stateless is True
