"""The resident harness root is the checkout, then the packaged copy."""

from pathlib import Path

from app.agentive.resident_root import resolve_resident_agent_root


def test_checkout_agent_tree_wins(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "pyproject.toml").write_text("", encoding="utf-8")
    agent = repo / "agent"
    agent.mkdir()
    (agent / "app.yaml").write_text("app: integral_agent\n", encoding="utf-8")
    packaged = repo / "backend" / "app" / "resident_harness"
    packaged.mkdir(parents=True)
    (packaged / "app.yaml").write_text("app: stale\n", encoding="utf-8")
    start = repo / "backend" / "app" / "agentive" / "resident_root.py"
    start.parent.mkdir(parents=True, exist_ok=True)
    start.write_text("", encoding="utf-8")
    assert resolve_resident_agent_root(start) == agent.resolve()


def test_packaged_copy_when_there_is_no_checkout(tmp_path: Path) -> None:
    start = tmp_path / "site-packages" / "app" / "agentive" / "resident_root.py"
    start.parent.mkdir(parents=True)
    start.write_text("", encoding="utf-8")
    packaged = tmp_path / "site-packages" / "app" / "resident_harness"
    packaged.mkdir()
    (packaged / "app.yaml").write_text("app: integral_agent\n", encoding="utf-8")
    assert resolve_resident_agent_root(start) == packaged.resolve()


def test_env_override_wins(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom"
    override.mkdir()
    (override / "app.yaml").write_text("app: integral_agent\n", encoding="utf-8")
    monkeypatch.setenv("INTEGRAL_AGENT_ROOT", str(override))
    start = tmp_path / "site-packages" / "app" / "x.py"
    start.parent.mkdir(parents=True)
    assert resolve_resident_agent_root(start) == override.resolve()
