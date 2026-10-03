"""Keep Core's LiteLLM install compatible with jvagent's runtime action."""

from importlib import metadata, resources
from pathlib import Path
import tomllib

import yaml
from packaging.requirements import Requirement


def test_resolved_litellm_satisfies_core_and_jvagent_action() -> None:
    """The action installer must see LiteLLM as satisfied and leave it alone."""
    core_project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    core_requirement = next(
        Requirement(item)
        for item in core_project["project"]["dependencies"]
        if Requirement(item).name.lower() == "litellm"
    )

    action_manifest = yaml.safe_load(
        resources.files("jvagent")
        .joinpath("action/model/language/litellm/info.yaml")
        .read_text()
    )
    action_dependencies = action_manifest["package"]["dependencies"]["pip"]
    action_requirement = next(
        Requirement(item)
        for item in action_dependencies
        if Requirement(item).name.lower() == "litellm"
    )

    installed_version = metadata.version("litellm")
    assert installed_version in core_requirement.specifier
    assert installed_version in action_requirement.specifier
