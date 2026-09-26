"""W0.4 — the generated capability map is current and reconciles its sources."""

from __future__ import annotations

import textwrap

import pytest

from app.agentive.tooling.catalogue import build_tool_catalogue
from app.services.capability_map import (
    MAP_JSON_PATH,
    MAP_MD_PATH,
    build_capability_map,
    render_json,
    render_markdown,
)

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def cap_map():
    return build_capability_map()


def test_committed_map_is_current(cap_map):
    stale = [
        str(path)
        for path, text in (
            (MAP_JSON_PATH, render_json(cap_map)),
            (MAP_MD_PATH, render_markdown(cap_map)),
        )
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]
    assert not stale, (
        f"stale capability map {stale}; run "
        "`backend/.venv/bin/python backend/scripts/generate_capability_map.py`"
    )


def test_map_reconciles_catalogue_editor_and_skills(cap_map):
    advertised = {t["name"] for t in cap_map["tools"] if t["advertised"]}
    assert advertised == {t["name"] for t in build_tool_catalogue()}
    diagnostics = cap_map["diagnostics"]
    assert diagnostics["editor_catalogue_mismatch"] == []
    assert diagnostics["existing_tools_not_dispatchable"] == []
    assert diagnostics["skill_tools_not_advertised"] == []
    assert diagnostics["skill_unresolved_refs"] == []
    assert diagnostics["delegation_to_unknown_skill"] == []
    assert diagnostics["app_skill_unresolved_calls"] == []
    for tool in cap_map["tools"]:
        if tool["advertised"]:
            assert tool["dispatch"]["targets"], tool["name"]
            assert tool["mcp_min_scope"], tool["name"]


def test_dependency_report_classifies_app_skill_calls(tmp_path):
    bundle = tmp_path / "people-app"
    (bundle / "skills" / "find_person").mkdir(parents=True)
    (bundle / "operational-model.yaml").write_text(
        textwrap.dedent(
            """
            integral_operational_model_version: 3
            scope: app
            package: {name: People, slug: people-app, version: 2.0.0}
            app:
              tracks:
                - {key: people, name: People}
              queries:
                - {key: active_people, policy_action: app.read}
              skills:
                - {key: find_person, private: true}
            """
        ),
        encoding="utf-8",
    )
    (bundle / "skills" / "find_person" / "SKILL.md").write_text(
        "---\nname: find_person\n---\n"
        "Call `active_people`, then `integral_query_entries(track_id=...)` "
        "over the People track, then `lookup_directory(name)`.\n",
        encoding="utf-8",
    )

    [app] = build_capability_map([tmp_path])["apps"]
    [skill] = app["skills"]
    assert (app["slug"], app["version"]) == ("people-app", "2.0.0")
    assert skill["same_app_focus"] == "required"
    assert skill["app_capabilities"] == ["active_people"]
    assert skill["core_generic_reads"] == ["integral_query_entries"]
    assert skill["target_tracks"] == ["people"]
    assert skill["unresolved_calls"] == ["lookup_directory"]
