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


def test_explicit_design_only_app_need_gets_a_host_scaffold_directive() -> None:
    """The reliable path must not depend on the model choosing a skill unaided."""
    from app.api.ai_chat import _is_explicit_greenfield_design_request

    assert _is_explicit_greenfield_design_request(
        "I need an app to manage appliance service requests. "
        "Please propose a complete design only; do not build anything yet."
    )
    assert _is_explicit_greenfield_design_request(
        "I need an app to manage appliance service requests."
    )
    assert not _is_explicit_greenfield_design_request(
        "Show me existing apps and do not build anything."
    )
    assert not _is_explicit_greenfield_design_request(
        "I need to update the dashboard in my existing app."
    )
    assert not _is_explicit_greenfield_design_request(
        "Please complete the already approved Wiki addition to the existing "
        "Car Rental Manager app. Do not create another App."
    )
    # Entry titles that contain "App" must not hit create…app greenfield routing.
    assert not _is_explicit_greenfield_design_request(
        'Approved — Create entry "Fabrikam Mobile App" in Project Proposals'
    )
    # Host Prompt Sheet resumes are continuations, never greenfield design asks.
    assert not _is_explicit_greenfield_design_request(
        "[PROMPT_SHEET]\n"
        "Resolved prompts\n"
        '* Approved — Create entry "Contoso Platform v2 Proposal" '
        "in Project Proposals\n"
        '* Approved — Create entry "Fabrikam Mobile App" in Project Proposals\n'
        "<!-- INTEGRAL_AGENT_DIRECTIVE\n"
        "The approved writes above have already been applied. Do not "
        "repeat, re-stage, or cancel them. First read back the affected "
        "resource using the appropriate Integral read tool. Continue only "
        "with a separate, still-unfulfilled part of the user's request.\n"
        "-->"
    )


def test_approved_app_extension_retry_does_not_reenter_design_only_mode() -> None:
    from app.api.ai_chat import _requires_greenfield_proposal

    marker = {"approved": True, "proposal": "Add Wiki track", "build_receipt": None}
    assert not _requires_greenfield_proposal(
        "Build the approved Wiki track in the existing Car Rental Manager app.",
        marker,
    )
    assert _requires_greenfield_proposal("I need a new payroll app.", marker)


@pytest.mark.asyncio
async def test_affirmed_build_without_apply_receipt_fails_turn(monkeypatch) -> None:
    from app.api.ai_chat import _approved_build_receipt_error
    from app.services import chat_threads

    async def pending(_session_id):
        return True

    monkeypatch.setattr(chat_threads, "design_chat_affirmed_for_build", pending)
    error = await _approved_build_receipt_error("thread-session", True)
    assert error and error["code"] == "approved_build_not_applied"
    assert await _approved_build_receipt_error("thread-session", False) is None


def test_approved_design_reply_is_routed_to_build() -> None:
    """'Build the app' must not be mistaken for a fresh design request."""
    from app.api.ai_chat import _requires_greenfield_proposal

    reply = "Looks good. Build the app."
    assert _requires_greenfield_proposal(reply, None)
    assert not _requires_greenfield_proposal(
        reply, {"approved": False, "proposed_at_user_turn": 1}
    )
    assert not _requires_greenfield_proposal(
        "Build the app.", {"approved": False, "proposed_at_user_turn": 1}
    )
    assert _requires_greenfield_proposal(
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
