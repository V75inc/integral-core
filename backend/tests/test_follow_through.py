"""A turn must not end with the work it started left undone.

Nothing here matches the wording of a reply, so it holds in any language and
with any model. A tool refusal the resident can repair itself names the tool
to call next (jvagent refuses to let the model reply until it does); open
``update_plan`` steps keep the turn going; a turn whose last real step failed
gets one follow-up pass; and a turn that changed nothing gets the same pass
when the model itself judges its reply left work undone.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive.tooling.dispatch import ToolResult
from app.schemas.capability_broker import CapabilityResult
from app.services.chat_providers import jvagent_provider
from app.services.chat_providers.base import ChatTurnContext

pytestmark = pytest.mark.smoke


def _ctx() -> ChatTurnContext:
    return ChatTurnContext(
        user_id="user-1",
        user_email="u@example.com",
        text="build my bike shop app",
        thread_id="thread-1",
        session_id="session-1",
    )


def _turn(reply: str) -> List[Dict[str, Any]]:
    return [
        {"type": "text-delta", "delta": reply},
        {"type": "final-content", "content": reply, "payload": {}},
        {"type": "message-finish", "timing": {}},
    ]


def _step(name: str, *, failed: bool = False, **extra) -> Dict[str, Any]:
    ev = {"type": "tool-call", "name": name, "status": "complete", **extra}
    if failed:
        ev["result"] = '{"error": true, "error_code": "invalid_scaffold_plan"}'
    return ev


async def _run(
    monkeypatch,
    turns: List[tuple[List[Dict[str, Any]], str]],
    *,
    model_says_unfinished: bool = False,
    checked: List[str] | None = None,
) -> tuple[list, list]:
    """Stream one turn per ``(tool steps, reply)``; return utterances, events."""
    utterances: List[str] = []
    queue = list(turns)

    async def _self_check(**kwargs):
        if checked is not None:
            checked.append(kwargs["reply"])
        return model_says_unfinished

    monkeypatch.setattr(jvagent_provider, "_reply_leaves_work_undone", _self_check)

    async def _fake_embed_turn(**kwargs):
        utterances.append(kwargs["text"])
        steps, reply = queue.pop(0)
        for ev in steps:
            yield ev
        for ev in _turn(reply):
            yield ev

    async def _passthrough(_workspace_id, factory):
        async for ev in factory():
            yield ev

    from app.services import jvagent_harness

    monkeypatch.setattr(jvagent_provider, "stream_jvagent_embed_turn", _fake_embed_turn)
    monkeypatch.setattr(jvagent_harness, "stream_with_model_override", _passthrough)
    monkeypatch.setattr(
        jvagent_provider.JvagentProvider, "_embed_configured", lambda self: True
    )
    monkeypatch.setattr(
        jvagent_provider.JvagentProvider,
        "_reset_jvagent_turn_caches",
        staticmethod(lambda _agent_id: None),
    )
    provider = jvagent_provider.JvagentProvider()
    events = [ev async for ev in provider.stream_turn(_ctx())]
    return utterances, events


@pytest.mark.asyncio
async def test_turn_ending_on_a_failed_step_gets_one_follow_up_pass(monkeypatch):
    utterances, events = await _run(
        monkeypatch,
        [
            ([_step("integral_build_approved_design", failed=True)], "x"),
            ([_step("integral_build_approved_design")], "Your app is ready."),
        ],
    )
    assert utterances[0] == "build my bike shop app"
    assert utterances[1] == jvagent_provider.FOLLOW_THROUGH_UTTERANCE.replace(
        "{reply}", "x"
    )
    assert {"type": "message-boundary"} in events
    finals = [ev["content"] for ev in events if ev["type"] == "final-content"]
    assert finals[-1] == "Your app is ready."


@pytest.mark.asyncio
async def test_follow_up_pass_runs_at_most_once(monkeypatch):
    failed = [_step("integral_build_approved_design", failed=True)]
    utterances, _events = await _run(monkeypatch, [(failed, "x"), (failed, "y")])
    assert len(utterances) == 2


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [_step("integral_list_apps")],
        [
            _step("integral_build_approved_design", failed=True),
            _step("integral_create_entry"),
        ],
        [_step("integral_build_approved_design", status="running")],
    ],
)
@pytest.mark.asyncio
async def test_turn_whose_last_real_step_succeeded_is_left_alone(monkeypatch, steps):
    utterances, events = await _run(monkeypatch, [(steps, "Done."), ([], "unused")])
    assert len(utterances) == 1
    assert {"type": "message-boundary"} not in events


@pytest.mark.asyncio
async def test_planning_after_a_failed_step_does_not_hide_the_failure(monkeypatch):
    steps = [_step("integral_propose_design", failed=True), _step("update_plan")]
    utterances, _events = await _run(monkeypatch, [(steps, "x"), ([], "Done.")])
    assert len(utterances) == 2


@pytest.mark.asyncio
async def test_reply_wording_never_triggers_a_pass(monkeypatch):
    utterances, _events = await _run(
        monkeypatch,
        [([], "I'll fix that and retry. Voy a crear la app ahora."), ([], "unused")],
    )
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_model_judged_unfinished_reply_gets_one_pass(monkeypatch):
    checked: List[str] = []
    utterances, events = await _run(
        monkeypatch,
        [
            ([_step("integral_list_apps")], "Estoy creando la app ahora."),
            ([_step("integral_propose_design")], "Aquí está el plan."),
        ],
        model_says_unfinished=True,
        checked=checked,
    )
    assert "> Estoy creando la app ahora." in utterances[1]
    assert checked == ["Estoy creando la app ahora."]
    assert {"type": "message-boundary"} in events


@pytest.mark.asyncio
async def test_turn_that_changed_something_skips_the_self_check(monkeypatch):
    checked: List[str] = []
    utterances, _events = await _run(
        monkeypatch,
        [([_step("integral_create_entry")], "Added."), ([], "unused")],
        model_says_unfinished=True,
        checked=checked,
    )
    assert checked == []
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_self_check_that_cannot_run_lets_the_reply_stand():
    assert not await jvagent_provider._reply_leaves_work_undone(
        agent_id="", workspace_id=None, utterance="hola", reply="Lo hago ahora."
    )


def test_error_status_counts_as_a_failed_step():
    assert jvagent_provider._tool_call_failed({"status": "error"})
    assert jvagent_provider._tool_call_failed(
        {"status": "complete", "result": {"error": True}}
    )
    assert not jvagent_provider._tool_call_failed(
        {"status": "complete", "result": '{"ok": true}'}
    )


@pytest.mark.asyncio
async def test_repairable_refusal_names_the_tool_to_retry(monkeypatch):
    from app.agentive.tooling import dispatch, scaffold_build

    async def _refuse(*_args, **_kwargs):
        return ToolResult(
            is_error=True, error_code="plan_differs_from_design", message="x"
        )

    monkeypatch.setattr(scaffold_build, "build_approved_design", _refuse)
    result = await dispatch.dispatch_tool(
        "integral_build_approved_design",
        {"operations": []},
        principal_id="user-1",
        scope="workspace-1",
    )
    assert result.next_tool == "integral_build_approved_design"


@pytest.mark.asyncio
async def test_user_facing_refusal_does_not_force_a_retry(monkeypatch):
    from app.agentive.tooling import dispatch, scaffold_build

    async def _refuse(*_args, **_kwargs):
        return ToolResult(
            is_error=True, error_code="design_approval_required", message="x"
        )

    monkeypatch.setattr(scaffold_build, "build_approved_design", _refuse)
    result = await dispatch.dispatch_tool(
        "integral_build_approved_design",
        {"operations": []},
        principal_id="user-1",
        scope="workspace-1",
    )
    assert result.next_tool == ""


def test_model_payload_carries_next_tool_only_when_set():
    chained = CapabilityResult(
        ok=False, error_code="invalid_arguments", next_tool="integral_author_skill"
    )
    assert chained.for_model()["next_tool"] == "integral_author_skill"
    assert "next_tool" not in CapabilityResult(ok=False).for_model()


def _booking_track(relation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tool": "integral_create_app_track",
        "args": {
            "name": "Bookings",
            "entry_types": [
                {
                    "key": "booking",
                    "fields": [
                        {"key": "client", "type": "relation", "relation": relation}
                    ],
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "relation",
    [
        {"target": "entry_type", "target_entry_types": ["client"]},
        {"target": "track", "target_entry_types": ["client"]},
        {"target": "track", "target_track_name": "Clients"},
        {"target": "track", "target_track": "Clients"},
    ],
)
def test_record_link_near_misses_become_entry_links(relation):
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    item = _approved_plan_item(
        _booking_track(relation), plan_track_types={"clients": ["client"]}
    )
    field = item["args"]["entry_types"][0]["fields"][0]
    assert field["relation"]["target"] == "entry"
    assert field["relation"]["target_entry_types"] == ["client"]
    assert "target_track_name" not in field["relation"]
    assert "target_track" not in field["relation"]


def test_link_to_a_track_outside_the_plan_is_left_for_the_anchor_check():
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    relation = {"target": "track", "target_track_name": "Elsewhere"}
    item = _approved_plan_item(
        _booking_track(relation), plan_track_types={"clients": ["client"]}
    )
    assert item["args"]["entry_types"][0]["fields"][0]["relation"] == relation


def test_view_naming_its_track_resolves_to_the_planned_track():
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    item = _approved_plan_item(
        {
            "tool": "integral_save_view",
            "args": {"config": {"track": "Clients", "name": "All", "type": "table"}},
        }
    )
    assert item["args"]["track_id"] == "{{track.id:Clients}}"
    assert "track" not in item["args"]["config"]


def test_view_with_a_track_hint_resolves_to_the_planned_track():
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    item = _approved_plan_item(
        {
            "tool": "integral_save_view",
            "args": {"track_hint": "Lessons", "name": "All", "type": "table"},
        }
    )
    assert item["args"]["track_id"] == "{{track.id:Lessons}}"
    assert "track_hint" not in item["args"]


def test_typed_seed_naming_an_earlier_seed_links_to_it():
    from app.agentive.tooling.scaffold_build import _structured_seed

    entry = _structured_seed(
        {
            "track_id": "{{track.id:Lessons}}",
            "title": "Lesson for Alice",
            "fields": {"student": "Alice Smith", "notes": "Alice Smith"},
        },
        {
            "{{track.id:Lessons}}": [
                {"key": "student", "type": "relation"},
                {"key": "notes", "type": "text"},
            ]
        },
        {"alice smith": "Alice Smith"},
    )
    assert entry["fields"] == {
        "student": "{{entry.id:Alice Smith}}",
        "notes": "Alice Smith",
    }


def test_design_fixes_the_model_can_make_force_a_retry():
    from app.agentive.tooling import dispatch

    for code in ("invalid_blueprint", "unsupported_design", "code_needs_unlisted"):
        assert code in dispatch._RETRY_SAME_TOOL_CODES
    assert (
        dispatch._REPAIR_TOOL_BY_CODE["affirm_build_instead"]
        == "integral_build_approved_design"
    )


@pytest.mark.asyncio
async def test_build_without_a_saved_design_chains_to_saving_it(monkeypatch):
    from app.agentive.tooling import scaffold_build

    async def _not_affirmed(_session_id):
        return False

    async def _no_thread(_session_id):
        return None

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", _not_affirmed)
    monkeypatch.setattr(scaffold_build, "get_thread_by_session", _no_thread)
    result = await scaffold_build.build_approved_design(
        {"operations": []},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "design_approval_required"
    assert result.next_tool == "integral_propose_design"


@pytest.mark.asyncio
async def test_build_awaiting_the_users_confirmation_does_not_chain(monkeypatch):
    from types import SimpleNamespace

    from app.agentive.tooling import scaffold_build

    async def _not_affirmed(_session_id):
        return False

    async def _pending_thread(_session_id):
        return SimpleNamespace(design_proposed={"proposal": "Dog Walking"})

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", _not_affirmed)
    monkeypatch.setattr(scaffold_build, "get_thread_by_session", _pending_thread)
    result = await scaffold_build.build_approved_design(
        {"operations": []},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "design_approval_required"
    assert result.next_tool == ""
