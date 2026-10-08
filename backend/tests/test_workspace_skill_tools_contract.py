"""Standard App skill declarations survive native overlay materialization."""

from pathlib import Path
from types import SimpleNamespace


def test_app_overlay_keeps_standard_allowed_tools_without_registered_list(tmp_path):
    from app.agentive.workspace_agent_profile import _skill_to_overlay_doc

    path = tmp_path / "skills" / "read_artifact" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: read-artifact\ndescription: Read an App artifact.\nallowed-tools: integral_list_apps integral_governed_query\n---\nRead through the declared query.\n"
    )
    skill = SimpleNamespace(
        key="read_artifact",
        name="Read artifact",
        enabled=True,
        kind="declarative",
        prompt_template_ref="skills/read_artifact/SKILL.md",
        tools_required=[],
        body_override=None,
        origin="bundle",
        app_id="app1",
        description="Read an App artifact.",
    )
    doc = _skill_to_overlay_doc(skill, app_slug="artifacts", bundle_dir=tmp_path)
    assert doc.requires_tools == ("integral_list_apps", "integral_governed_query")
    skill.body_override = "allowed-tools: integral_delete_entry\nCustom instructions."
    skill.tools_required = ["integral_resolve_entry"]
    overridden = _skill_to_overlay_doc(skill, app_slug="artifacts", bundle_dir=tmp_path)
    assert overridden.requires_tools == (
        "integral_list_apps",
        "integral_governed_query",
    )
    assert "integral_delete_entry" not in overridden.requires_tools
    path.unlink()
    fallback = _skill_to_overlay_doc(skill, app_slug="artifacts", bundle_dir=tmp_path)
    assert fallback.requires_tools == ("integral_resolve_entry",)
