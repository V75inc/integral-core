"""Regression tests for adversarial review security fixes."""

import pytest

from app.services.ws_ticket import _reset_for_tests, mint_ws_ticket, redeem_ws_ticket


@pytest.mark.asyncio
async def test_conversation_context_ownership_gate_unit():
    """Only the conversation owner may use its context."""
    from app.agentive.api.conversations import _require_context_owner
    from app.api.errors import InsufficientPermissionsError

    class FakeCtx:
        user_id = "owner-a"

    _require_context_owner(FakeCtx(), "owner-a")

    with pytest.raises(InsufficientPermissionsError):
        _require_context_owner(FakeCtx(), "owner-b")


@pytest.mark.asyncio
async def test_proactive_users_needing_digest_requires_service_auth(
    authenticated_client,
):
    """The proactive service route refuses ordinary authenticated callers."""
    r = await authenticated_client.get("/api/agentive/proactive/users-needing-digest")
    assert r.status_code == 403


def test_ws_ticket_single_use():
    """A websocket ticket cannot be redeemed twice."""
    _reset_for_tests()
    ticket = mint_ws_ticket("user-1")
    assert redeem_ws_ticket(ticket) == "user-1"
    assert redeem_ws_ticket(ticket) is None


@pytest.mark.asyncio
async def test_dispatch_strips_cross_workspace():
    """Tool arguments cannot widen the bound workspace."""
    from app.agentive.tooling.policy_gate import sanitize_tool_args

    args = sanitize_tool_args(
        "integral_get_feed",
        {"cross_workspace": True, "limit": 5},
        scope="ws-abc",
    )
    assert "cross_workspace" not in args
    assert args["limit"] == 5


@pytest.mark.asyncio
async def test_dispatch_binds_retrieve_workspace_scope():
    """Retrieval receives the dispatch workspace when no scope is supplied."""
    from app.agentive.tooling.policy_gate import sanitize_tool_args

    args = sanitize_tool_args("integral_query", {"query": "hello"}, scope="ws-abc")
    assert args["scope"] == "workspace:ws-abc"


@pytest.mark.asyncio
async def test_dispatch_rejects_foreign_workspace_scope():
    """Retrieval identifies a requested workspace outside dispatch scope."""
    from app.agentive.tooling.policy_gate import sanitize_tool_args

    args = sanitize_tool_args(
        "integral_query",
        {"query": "hello", "scope": "workspace:other-ws"},
        scope="ws-abc",
    )
    assert args.get("_scope_violation") is True


@pytest.mark.asyncio
async def test_entry_read_collection_defers_when_only_track_id():
    """query_entries-style calls must not gate on track_id + entry.read."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "entry.read"

    result = await enforce_tool_policy(
        Spec(),
        {"track_id": "records", "sort_by": "created_at", "limit": 10},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_entry_read_point_check_still_enforced():
    """A resolvable record read is denied when policy denies it."""
    from unittest.mock import AsyncMock, patch

    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "entry.read"

    with patch(
        "app.agentive.tooling.policy_gate.policy_evaluate",
        new_callable=AsyncMock,
    ) as mock_eval:
        from app.services.policy_engine import Decision

        mock_eval.return_value = Decision(allowed=False, reason="denied")
        result = await enforce_tool_policy(
            Spec(),
            {"entry_id": "n.Entry.abc123"},
            principal_id="user-1",
            workspace_id="workspace-1",
        )
    assert result is not None
    assert result.is_error is True
    assert result.error_code == "forbidden"


@pytest.mark.asyncio
async def test_app_read_defers_when_app_id_is_name_not_object_id():
    """integral_get_app-style calls must not gate on an unresolved app name."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "app.read"

    result = await enforce_tool_policy(
        Spec(),
        {"app_id": "demo_app"},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_track_read_defers_when_track_id_is_name():
    """Track aliases are resolved by handlers before policy evaluation."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "track.read"

    result = await enforce_tool_policy(
        Spec(),
        {"track_id": "records"},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_entry_read_defers_when_entry_id_is_not_object_id():
    """Garbage/alias entry ids should reach the handler (404), not dispatch deny."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "entry.read"

    result = await enforce_tool_policy(
        Spec(),
        {"entry_id": "sample-entry-title"},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_event_feed_subscribe_defers_on_track_name():
    """Feed tools carry track_id but policy_action is event_feed.subscribe."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "event_feed.subscribe"

    result = await enforce_tool_policy(
        Spec(),
        {"track_id": "records", "limit": 20},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_profile_author_defers_on_operational_model_id():
    """Profile tools use profile.* actions against operational_model resources."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "operational_model.author"

    result = await enforce_tool_policy(
        Spec(),
        {"operational_model_id": "n.OperationalModel.abc123"},
        principal_id="user-1",
    )
    assert result is not None
    assert result.is_error is True
    assert result.error_code == "invalid_execution_scope"


@pytest.mark.asyncio
async def test_activity_digest_defers_without_resource_arg():
    """activity_digest scopes via scope/scope_id, not track_id — no dispatch gate."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "entry.read"

    result = await enforce_tool_policy(
        Spec(),
        {"scope": "track", "scope_id": "records", "period": "today"},
        principal_id="user-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_templated_resource_read_defers_on_unresolved_id():
    """Templated actions defer until the handler resolves an object id."""
    from app.agentive.tooling.policy_gate import enforce_tool_policy

    class Spec:
        policy_action = "{resource_type}.read"

    result = await enforce_tool_policy(
        Spec(),
        {"resource_type": "app", "resource_id": "demo_app"},
        principal_id="user-1",
    )
    assert result is None
