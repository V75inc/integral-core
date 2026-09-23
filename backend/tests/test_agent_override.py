"""Measured agent.override.yaml merges onto the shipped agent.yaml."""

from pathlib import Path

import pytest
import yaml

from app.agentive.agent_override import apply_agent_override, prepare_resident_app_root


def _base() -> dict:
    return {
        "agent": "integral/integral_agent",
        "context": {
            "alias": "Integral Assistant",
            "role": "Help.",
            "interaction_limit": 20,
        },
        "actions": [
            {
                "action": "jvagent/orchestrator",
                "context": {
                    "model": "openai/gpt-4.1",
                    "activation_budget": 20,
                    "enabled": True,
                },
            },
            {"action": "jvagent/reply", "context": {"model": "openai/gpt-4.1"}},
        ],
    }


def test_overlay_changes_persona_and_orchestrator_budget() -> None:
    merged = apply_agent_override(
        _base(),
        {
            "context": {"alias": "Desk Assistant", "interaction_limit": 12},
            "actions": [
                {
                    "action": "jvagent/orchestrator",
                    "context": {"model": "openai/gpt-4o", "activation_budget": 30},
                }
            ],
        },
    )
    assert merged["context"]["alias"] == "Desk Assistant"
    assert merged["context"]["role"] == "Help."
    orch = merged["actions"][0]["context"]
    assert orch["model"] == "openai/gpt-4o"
    assert orch["activation_budget"] == 30
    assert orch["enabled"] is True
    assert merged["actions"][1]["context"]["model"] == "openai/gpt-4.1"


def test_unknown_key_and_new_action_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        apply_agent_override(_base(), {"skills": []})
    with pytest.raises(ValueError, match="only adjust"):
        apply_agent_override(
            _base(), {"actions": [{"action": "jvagent/reply", "context": {}}]}
        )
    with pytest.raises(ValueError, match="20..40"):
        apply_agent_override(
            _base(),
            {
                "actions": [
                    {
                        "action": "jvagent/orchestrator",
                        "context": {"activation_budget": 10},
                    }
                ]
            },
        )


def test_shipped_agent_yaml_keeps_its_actions() -> None:
    from app.agentive.resident_root import resident_agent_root

    path = resident_agent_root() / "agents/integral/integral_agent/agent.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8"))
    merged = apply_agent_override(base, {"context": {"alias": "Desk"}})
    assert [item["action"] for item in merged["actions"]] == [
        item["action"] for item in base["actions"]
    ]
    assert merged["context"]["alias"] == "Desk"


def test_prepare_writes_a_runtime_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = tmp_path / "shipped"
    agent_dir = shipped / "agents" / "integral" / "integral_agent"
    agent_dir.mkdir(parents=True)
    (shipped / "app.yaml").write_text("app: integral_agent\n", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text(yaml.safe_dump(_base()), encoding="utf-8")
    distro = tmp_path / "my-integral"
    distro.mkdir()
    (distro / "agent.override.yaml").write_text(
        yaml.safe_dump({"context": {"alias": "Desk"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(distro)
    root = prepare_resident_app_root(shipped)
    written = yaml.safe_load(
        (root / "agents/integral/integral_agent/agent.yaml").read_text()
    )
    assert root == (distro / ".integral" / "agent-runtime").resolve()
    assert written["context"]["alias"] == "Desk"
    assert (root / "app.yaml").is_file()
