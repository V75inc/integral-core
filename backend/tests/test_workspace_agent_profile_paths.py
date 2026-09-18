from app.agentive.workspace_agent_profile import locate_skill_disk_path
from app.models.nodes import Skill


def test_locate_skill_disk_path_uses_configured_package_roots(tmp_path, monkeypatch):
    package_root = tmp_path / "packages"
    skill_path = package_root / "external-app" / "skills" / "summarize" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Summarize\n", encoding="utf-8")
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(package_root))

    assert locate_skill_disk_path(Skill(key="summarize")) == skill_path
