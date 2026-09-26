"""Resident skill instructions must agree with the dispatchable tool surface."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def test_workspace_skill_describes_scope_tools_as_dispatchable() -> None:
    root = Path(__file__).resolve().parents[2]
    skill_path = (
        root
        / "agent/agents/integral/integral_agent/actions/integral"
        / "embedded_integral_action/skills/integral_workspace/SKILL.md"
    )
    manifest_path = root / "backend/app/agentive/tool_manifest.yaml"
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(raw.split("---", 2)[1])
    tools = set(frontmatter["allowed-tools"])

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    catalogue = {
        row["name"]: row.get("status")
        for domain in manifest["domains"].values()
        for row in domain.get("tools", [])
    }

    for name in ("integral_get_scope", "integral_list_workspaces"):
        assert name in tools
        assert catalogue[name] == "existing"
    assert "not-yet-available" not in raw.lower()


def test_no_core_skill_marks_an_existing_manifest_tool_unavailable() -> None:
    """Fallback prose must move in lockstep with the public tool catalogue."""
    root = Path(__file__).resolve().parents[2]
    skills = root / "agent/agents/integral/integral_agent/actions/integral"
    skills = skills / "embedded_integral_action/skills"
    manifest = yaml.safe_load(
        (root / "backend/app/agentive/tool_manifest.yaml").read_text(encoding="utf-8")
    )
    catalogue = {
        row["name"]: row.get("status")
        for domain in manifest["domains"].values()
        for row in domain.get("tools", [])
    }

    for skill_path in skills.glob("integral_*/SKILL.md"):
        raw = skill_path.read_text(encoding="utf-8")
        for block in re.findall(
            r"(?is)(?:not[- ]yet[- ]available|not yet dispatchable).*?(?=\n#{1,3}\s|\Z)",
            raw,
        ):
            for name in re.findall(r"`(integral_[a-z0-9_]+)`", block):
                assert (
                    catalogue.get(name) != "existing"
                ), f"{skill_path.name} says live tool {name} is unavailable"


def test_scaffold_is_the_single_resident_delivery_owner() -> None:
    """The live SOP exposes the complete, receipt-honest delivery sequence."""
    from app.services.skill_compliance import (
        RESIDENT_DELIVERY_OWNER,
        RESIDENT_DELIVERY_PHASES,
    )

    root = Path(__file__).resolve().parents[2]
    path = (
        root
        / "agent/agents/integral/integral_agent/actions/integral"
        / f"embedded_integral_action/skills/{RESIDENT_DELIVERY_OWNER}/SKILL.md"
    )
    body = path.read_text(encoding="utf-8").lower()

    assert all(phase in body for phase in RESIDENT_DELIVERY_PHASES)
    assert "say “verified” only after" in body
    assert "must never be rendered as a saved result" in body
    assert "explicit design-only boundary" in body
    assert "proposed — nothing has been built." in body
    assert "do **not** call" in body


def test_resident_runtime_treats_an_explicit_greenfield_need_as_design_ready() -> None:
    """A stated app need must not be bounced back as a create-versus-search fork."""
    root = Path(__file__).resolve().parents[2]
    agent = yaml.safe_load(
        (root / "agent/agents/integral/integral_agent/agent.yaml").read_text(
            encoding="utf-8"
        )
    )
    role = str(agent["context"]["role"]).lower()

    assert "enough to propose a design" in role
    assert "do not ask whether to create or search" in role
    assert "design only" in role


@pytest.mark.asyncio
async def test_explicit_design_only_app_need_gets_a_host_scaffold_directive(
    monkeypatch,
) -> None:
    """The reliable path must not depend on the model choosing a skill unaided."""
    from app.api import ai_chat

    async def wants_new_app(text, **_kwargs):
        folded = text.casefold()
        if "existing" in folded:
            return False
        return "need an app" in folded

    monkeypatch.setattr(ai_chat, "_user_wants_new_app", wants_new_app)
    assert await ai_chat._is_explicit_greenfield_design_request(
        "I need an app to manage appliance service requests. "
        "Please propose a complete design only; do not build anything yet."
    )
    assert await ai_chat._is_explicit_greenfield_design_request(
        "I need an app to manage appliance service requests."
    )
    assert not await ai_chat._is_explicit_greenfield_design_request(
        "Show me existing apps and do not build anything."
    )
    assert not await ai_chat._is_explicit_greenfield_design_request(
        "I need to update the dashboard in my existing app."
    )
    # Host Prompt Sheet resumes are continuations, never greenfield design asks.
    resume = (
        "[PROMPT_SHEET]\n"
        "Resolved prompts\n"
        '* Approved — Create entry "Fabrikam Mobile App" in Project Proposals\n'
        "<!-- INTEGRAL_AGENT_DIRECTIVE\n"
        "The approved writes above have already been applied.\n"
        "-->"
    )
    assert not await ai_chat._is_explicit_greenfield_design_request(resume)


@pytest.mark.asyncio
async def test_approved_app_extension_retry_does_not_reenter_design_only_mode(
    monkeypatch,
) -> None:
    from app.api import ai_chat

    async def wants_new_app(text, **_kwargs):
        folded = text.casefold()
        return "payroll" in folded

    monkeypatch.setattr(ai_chat, "_user_wants_new_app", wants_new_app)
    marker = {"approved": True, "proposal": "Add Wiki track", "build_receipt": None}
    assert not await ai_chat._requires_greenfield_proposal(
        "Build the approved Wiki track in the existing Car Rental Manager app.",
        marker,
    )
    assert await ai_chat._requires_greenfield_proposal(
        "I need a new payroll app.", marker
    )


@pytest.mark.asyncio
async def test_affirmed_build_without_apply_receipt_fails_turn(monkeypatch) -> None:
    from app.api.ai_chat import _approved_build_receipt_error
    from app.services import chat_threads

    async def pending(_session_id):
        return True

    monkeypatch.setattr(chat_threads, "design_chat_affirmed_for_build", pending)
    claim = "Your app has been built."
    error = await _approved_build_receipt_error("thread-session", True, claim)
    assert error and error["code"] == "approved_build_not_applied"
    assert await _approved_build_receipt_error("thread-session", False, claim) is None
    assert await _approved_build_receipt_error("thread-session", True) is None


@pytest.mark.asyncio
async def test_approved_design_reply_is_routed_to_build(monkeypatch) -> None:
    """A go-ahead on a pending design builds; a new App still proposes."""
    from app.api import ai_chat

    async def wants_new_app(text, **_kwargs):
        return "app" in text.casefold()

    monkeypatch.setattr(ai_chat, "_user_wants_new_app", wants_new_app)
    reply = "Looks good. Build the app."
    assert await ai_chat._requires_greenfield_proposal(reply, None)
    assert not await ai_chat._requires_greenfield_proposal(
        reply, {"approved": False, "proposed_at_user_turn": 1}
    )
    assert not await ai_chat._requires_greenfield_proposal(
        "Build the app.", {"approved": False, "proposed_at_user_turn": 1}
    )
    assert await ai_chat._requires_greenfield_proposal(
        "I need another app to manage invoices.",
        {"approved": True, "proposed_at_user_turn": 1},
    )


def test_host_design_directive_does_not_trigger_harness_tool_steering() -> None:
    """Host guidance rides the utterance and must not name dispatch tools."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    from app.agentive.tooling import build_tool_catalogue
    from app.api.ai_chat import _GREENFIELD_DESIGN_DIRECTIVE

    names = {entry["name"] for entry in build_tool_catalogue()}
    assert not OrchestratorInteractAction._user_named_tools(
        _GREENFIELD_DESIGN_DIRECTIVE, names
    )


@pytest.mark.asyncio
async def test_greenfield_turn_requires_a_current_saved_proposal(monkeypatch) -> None:
    """A prose-only design cannot be recorded as a successful app proposal."""
    from app.api import ai_chat

    thread = SimpleNamespace(design_proposed=None)

    async def get_thread(_id):
        return thread

    async def count_user_turns(_thread):
        return 1

    monkeypatch.setattr(ai_chat.chat_store, "get_thread", get_thread)
    monkeypatch.setattr(ai_chat.chat_store, "count_user_turns", count_user_turns)
    error = await ai_chat._greenfield_proposal_error("thread-1", True)
    assert error["code"] == "design_proposal_missing"

    thread.design_proposed = {"proposed_at_user_turn": 0, "approved": False}
    assert await ai_chat._greenfield_proposal_error("thread-1", True)

    thread.design_proposed["proposed_at_user_turn"] = 1
    assert await ai_chat._greenfield_proposal_error("thread-1", True) is None
    assert await ai_chat._greenfield_proposal_error("thread-1", False) is None


def test_existing_track_field_request_gets_schema_revision_routing() -> None:
    """A field edit must not be routed to new-model or duplicate-type tools."""
    from app.api.ai_chat import _is_existing_schema_field_request

    assert _is_existing_schema_field_request(
        "Add a Priority field with Low, Normal, and High choices.",
        "n.Track.service-requests",
    )
    assert not _is_existing_schema_field_request(
        "Add a Priority field with Low, Normal, and High choices.", None
    )


def test_existing_track_field_request_treats_live_model_as_authoritative() -> None:
    """Old chat claims must not suppress a revision when the field is absent."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/app/api/ai_chat.py").read_text(encoding="utf-8")
    assert (
        "Past assistant messages, expired cards, and prior publication claims" in source
    )
    assert "authoritative: if the requested field is absent" in source


def test_scaffold_use_case_requires_preview_before_the_single_build_approval() -> None:
    """The deterministic resident journey cannot regress to create-first."""
    root = Path(__file__).resolve().parents[2]
    path = (
        root
        / "agent/agents/integral/integral_agent/use-cases/scaffold/app-one-batch.yaml"
    )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    turns = {turn["id"]: turn for turn in doc["turns"]}

    proposal = turns["request-crm-design"]["harness"]["decisions"]
    assert [step.get("tool") for step in proposal if step["action"] == "tool"] == [
        "integral_propose_design"
    ]
    assert "nothing has been built" in proposal[-1]["answer"].lower()

    build = turns["affirm-crm-design"]["harness"]["decisions"]
    assert [step.get("tool") for step in build if step["action"] == "tool"] == [
        "integral_begin_batch",
        "integral_create_app",
        "integral_create_app_track",
        "integral_create_app_track",
        "integral_commit_batch",
    ]


# ---------------------------------------------------------------------------
# W0.1 drift regressions — one test per row of the skill ↔ implementation
# drift table in docs/product/CORE_SUBSTRATE_IMPROVEMENT_PLAN.md §3.
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_SKILLS = (
    _ROOT
    / "agent/agents/integral/integral_agent/actions/integral"
    / "embedded_integral_action/skills"
)


def _skill(name: str) -> str:
    return (_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _manifest_tool(name: str) -> dict:
    from app.agentive.tooling.manifest import load_manifest

    return load_manifest()[name].model_dump()


def _manifest_domains() -> dict:
    return yaml.safe_load(
        (_ROOT / "backend/app/agentive/tool_manifest.yaml").read_text(encoding="utf-8")
    )["domains"]


def test_w01_d01_d02_builder_adds_only_requested_tables() -> None:
    """Builder behaviour is pinned by test_scaffold_build_plan; prose must match.

    Behaviour fixtures: test_omitted_views_do_not_invent_a_table_or_calendar,
    test_named_seed_blocks_invented_example_rows.
    """
    from app.agentive.tooling.scaffold_build import (
        _expand_track,
        _positively_requested,
    )

    track = {"name": "Tasks", "fields": [{"key": "due", "type": "date"}]}
    assert [tool for tool, _ in _expand_track(track)] == ["integral_create_app_track"]
    assert [tool for tool, _ in _expand_track(track, include_default_view=True)] == [
        "integral_create_app_track",
        "integral_save_view",
    ]
    assert _positively_requested("a table of tasks", "table")
    assert not _positively_requested("a board, no table", "table")

    scaffold = _skill("integral_scaffold").lower()
    build_desc = _manifest_tool("integral_build_approved_design")["params"][
        "operations"
    ]["desc"].lower()
    for text in (scaffold, build_desc):
        assert "every track should get one" not in text
        assert "fills omitted baseline" not in text
        assert "example records unless" not in text
        assert "never invents demo records" in text


def test_w01_d03_tags_are_part_of_the_build() -> None:
    """W1.1 replaced the interim post-build wording."""
    from app.agentive.tooling.scaffold_build import _PLAN_TOOLS

    assert "integral_create_tag" in _PLAN_TOOLS
    scaffold = _skill("integral_scaffold")
    assert "tags are a post-build step" not in scaffold.lower()
    assert "args.taxonomy" in scaffold
    build_desc = _manifest_tool("integral_build_approved_design")["params"][
        "operations"
    ]["desc"]
    assert "Tags are not a valid operation" not in build_desc
    assert "create_app_track.args.taxonomy" in build_desc
    assert "taxonomy" in _manifest_tool("integral_create_app_track")["params"]


def test_w12_anchors_are_part_of_the_build() -> None:
    """Behaviour: test_build_anchors.py (distinct detail Track per parent)."""
    from app.agentive.tooling.scaffold_build import _PLAN_TOOLS

    assert "integral_register_track_template" in _PLAN_TOOLS
    scaffold = _skill("integral_scaffold")
    assert "not buildable yet" not in scaffold
    assert "integral_register_track_template" in scaffold
    build_desc = _manifest_tool("integral_build_approved_design")["params"][
        "operations"
    ]["desc"]
    assert "integral_register_track_template" in build_desc
    assert "target_track_template" in build_desc


def test_w01_d04_insights_reads_custom_fields_from_rows() -> None:
    """Behaviour: test_w01_rows_carry_custom_fields_and_tag_filters_match_ids."""
    insights = _skill("integral_insights").lower()
    assert "do not carry" not in insights
    assert "summaries only" not in insights
    assert "call `integral_resolve_entry` on the top" not in insights
    assert "never rank over a truncated page" in insights


def test_w01_d05_saved_view_filters_are_operator_lists() -> None:
    from app.agentive.tooling.scaffold_build import _normalize_view
    from app.api.errors import BadRequestError
    from app.services.entry_listing import _view_filter_to_clause
    from app.services.operational_model_compile import normalize_view_config

    with pytest.raises(BadRequestError):
        normalize_view_config("table", {"filters": {"status": "open"}})
    listed = [{"field": "status", "operator": "eq", "value": "open"}]
    assert normalize_view_config("table", {"filters": listed})["filters"] == listed
    assert _view_filter_to_clause("custom_fields.due", "gte", "2026-01-01")
    assert _view_filter_to_clause("status", "in", ["open"]) is None
    assert _normalize_view(
        {"config": {"filters": [{"field": "status", "op": "neq", "value": "x"}]}}
    )["config"]["filters"] == [{"field": "status", "operator": "neq", "value": "x"}]

    for path in _SKILLS.glob("integral_*/SKILL.md"):
        body = path.read_text(encoding="utf-8")
        for call in re.findall(r"integral_save_view\((.*?)\)`", body, re.DOTALL):
            assert not re.search(
                r"filters\s*:\s*\{", call
            ), f"{path.parent.name} shows a map-form saved-view filter"
    insights = _skill("integral_insights")
    assert "is a **list** of `{field, operator, value}`" in insights
    assert "there is no `in` for saved views" in insights


def test_w01_d06_insights_documents_both_date_filter_paths() -> None:
    from app.schemas.query_spec import QuerySpec, validate_query_spec_semantics

    insights = _skill("integral_insights")
    assert "`integral_query_entries` also accepts `since` / `until`" in insights
    assert '{field: "custom_fields.due_date", op: "gte"' in insights
    validate_query_spec_semantics(
        QuerySpec.model_validate(
            {
                "resource": "entry",
                "select": ["id", "custom_fields.due_date"],
                "filters": [
                    {"field": "custom_fields.due_date", "op": "gte", "value": "2026"}
                ],
            }
        )
    )


def test_w01_d07_no_core_skill_forbids_its_own_live_tools() -> None:
    catalogue = {
        row["name"]: row.get("status")
        for domain in _manifest_domains().values()
        for row in domain.get("tools", [])
    }
    for path in _SKILLS.glob("integral_*/SKILL.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "gap tool" not in line.lower():
                continue
            for name in re.findall(r"`(integral_[a-z0-9_]+)`", line):
                assert (
                    catalogue.get(name) != "existing"
                ), f"{path.parent.name} calls live tool {name} a gap tool"


def test_w01_d08_get_related_is_described_as_inbound_only() -> None:
    source = (_ROOT / "backend/app/api/entry_relations.py").read_text(encoding="utf-8")
    assert 'edge=["REFERENCES"], direction="in"' in source
    assert "ANCHORS" not in source.split("related_in = await target.nodes", 1)[0][-400:]
    summary = _manifest_tool("integral_get_related")["summary"]
    assert "inbound REFERENCES only" in summary
    assert "REFERENCES/ANCHORS" not in summary


def test_w01_d09_count_group_by_enum_matches_service() -> None:
    from typing import get_args

    from app.services.agent_insights import GroupBy

    enum = _manifest_tool("integral_count_entries")["params"]["group_by"]["enum"]
    assert set(enum) == set(get_args(GroupBy))


def test_w01_d10_file_content_advertises_tags_as_ids() -> None:
    import inspect

    from app.agentive import staging_executors
    from app.agentive.tooling import stagers_filing

    assert 'a.get("tags")' in inspect.getsource(stagers_filing)
    assert 'payload.get("tags")' in inspect.getsource(staging_executors._x_file_content)
    for tool in ("integral_file_content", "integral_query_entries"):
        assert "IDS" in _manifest_tool(tool)["params"]["tags"]["desc"]
    assert "IDS" in _manifest_tool("integral_count_entries")["params"]["tags"]["desc"]


def test_w01_d11_attachments_domain_names_the_attach_tools() -> None:
    domain = next(
        d
        for d in _manifest_domains().values()
        if any(t["name"] == "integral_attach_file" for t in d.get("tools", []))
    )
    assert "via file_content staging" not in domain["description"]
    names = {t["name"] for t in domain["tools"]}
    for tool in re.findall(r"integral_[a-z_]+", domain["description"]):
        assert tool in names


def test_w01_d12_field_edits_route_to_the_draft_lifecycle() -> None:
    actions = _manifest_tool("integral_modify_model")["params"]["action"]["enum"]
    assert not [action for action in actions if "field" in action]
    assert "has no field actions" in _skill("integral_model")
    assert "It has no field\n  actions" in _skill("integral_models")
    assert "(`integral_modify_model` / revision)" not in _skill("integral_model")


def test_w01_d13_byoa_names_only_published_tools() -> None:
    from app.agentive.tooling.catalogue import build_tool_catalogue
    from app.services.skill_compliance import check_tool_call_examples

    schemas = {t["name"]: t["input_schema"] for t in build_tool_catalogue()}
    byoa = (_ROOT / "docs/product/BYOA.md").read_text(encoding="utf-8")
    unknown = sorted(set(re.findall(r"`(integral_[a-z0-9_]+)`", byoa)) - set(schemas))
    assert not unknown, f"BYOA.md names unpublished tools: {unknown}"
    assert check_tool_call_examples(byoa, schemas) == []


def test_w01_d14_staging_docstring_matches_durable_store() -> None:
    from app.agentive import staging

    doc = staging.__doc__ or ""
    assert "restart drops pending tokens" not in doc
    assert "staging_store" in doc and "_open_batches" in doc


def test_w01_d04_insights_ranks_custom_fields_with_a_valid_query_spec() -> None:
    from app.schemas.query_spec import QuerySpec, validate_query_spec_semantics

    insights = _skill("integral_insights")
    assert 'sort: [{field: "custom_fields.value", direction: "desc"}]' in insights
    validate_query_spec_semantics(
        QuerySpec.model_validate(
            {
                "resource": "entry",
                "select": ["id", "title", "custom_fields.value"],
                "filters": [{"field": "track_id", "op": "eq", "value": "t"}],
                "sort": [{"field": "custom_fields.value", "direction": "desc"}],
                "limit": 3,
            }
        )
    )


def test_w01_d17_insights_resolves_field_keys_before_ranking() -> None:
    """QuerySpec does not refuse an unknown custom-field key (G32), so the skill must.

    When W3.9 makes the validator refuse ``custom_fields.Value``, flip the
    second half of this test.
    """
    from app.schemas.query_spec import QuerySpec, validate_query_spec_semantics

    insights = _skill("integral_insights")
    frontmatter = yaml.safe_load(insights.split("---")[1])
    assert "integral_get_track_schema" in frontmatter["allowed-tools"]
    assert "never display labels" in insights
    assert "null on every row" in insights
    assert "never fill values in from memory" in insights

    validate_query_spec_semantics(
        QuerySpec.model_validate(
            {
                "resource": "entry",
                "select": ["id", "custom_fields.Value"],
                "sort": [{"field": "custom_fields.Value", "direction": "desc"}],
                "limit": 2,
            }
        )
    )
