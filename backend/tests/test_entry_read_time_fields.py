"""Read-time projections are scoped, read-only and separate from saved fields."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import entry_read_time_fields as live
from app.services.operational_model_compile import _parse_manifest_hooks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["success", "failure", "write", "other_bundle", "privileged", "duplicate"]
)
async def test_projection_contract(monkeypatch, mode):
    entry = SimpleNamespace(
        id="entry", track_id="track", type_id="type", custom_fields={"fresh": False}
    )
    owner = SimpleNamespace(installed_package_slug="own", lifecycle_state="active")
    track = SimpleNamespace(workspace_id="ws", nodes=AsyncMock(return_value=[owner]))
    monkeypatch.setattr(live.Track, "get", AsyncMock(return_value=track))
    monkeypatch.setattr(
        live.EntryType, "get", AsyncMock(return_value=SimpleNamespace(name="Record"))
    )
    monkeypatch.setattr(
        live, "resolve_track_runtime_profile", AsyncMock(return_value=(None, {}, None))
    )
    monkeypatch.setattr(
        live,
        "resolve_entry_type_spec",
        lambda *_: {"key": "record", "fields": [{"key": "fresh"}]},
    )
    binding = {
        "read_time_fields": ["fresh"],
        "_bundle_slug": "own",
        "mode": "tool",
        "tool": "check",
    }
    monkeypatch.setattr(
        live,
        "find_matching_bindings",
        lambda *_: [binding, binding] if mode == "duplicate" else [binding],
    )
    tool = {
        "privileged": mode == "privileged",
        "_bundle_slug": "other" if mode == "other_bundle" else "own",
        "side_effects": "write" if mode == "write" else "read_only",
    }
    monkeypatch.setattr(live, "get_workspace_tools", lambda *_: {"check": tool})
    run = AsyncMock(
        side_effect=RuntimeError("private error") if mode == "failure" else None,
        return_value={"fresh": True, "undeclared": "secret"},
    )
    monkeypatch.setattr(live, "run_tool", run)
    result = await live.resolve_entry_read_time_fields(entry, "user")
    assert entry.custom_fields == {"fresh": False}
    assert "undeclared" not in result["values"]
    if mode == "success":
        assert result == {"values": {"fresh": True}, "status": {"fresh": "current"}}
        assert run.call_args.args[2].read_only is True
    else:
        assert result == {"values": {"fresh": None}, "status": {"fresh": "unavailable"}}
    if mode in ("write", "other_bundle", "privileged", "duplicate"):
        run.assert_not_called()


def test_compile_preserves_opt_in_projection():
    hooks = _parse_manifest_hooks(
        [
            {
                "key": "check",
                "point": "entry.precompute",
                "mode": "tool",
                "tool": "check",
                "read_time_fields": ["fresh"],
            }
        ],
        declared_tool_keys={"check"},
    )
    assert hooks[0]["read_time_fields"] == ["fresh"]


@pytest.mark.parametrize("keys", [[], ["x", "x"], [1], "x"])
def test_compile_rejects_invalid_projection_keys(keys):
    with pytest.raises(Exception, match="read_time_fields"):
        _parse_manifest_hooks(
            [
                {
                    "key": "check",
                    "point": "entry.precompute",
                    "mode": "tool",
                    "tool": "check",
                    "read_time_fields": keys,
                }
            ],
            declared_tool_keys={"check"},
        )


@pytest.mark.parametrize(
    "method",
    [
        "notify",
        "put_attachment",
        "document_render",
        "rollup_plan",
        "create_entry_in_own_bundle_track",
        "call_app_tool",
        "call_connector_tool",
        "send_workspace_transactional_email",
        "upsert_workspace_email_delivery",
        "esign_vault_save",
    ],
)
def test_automatic_read_facade_rejects_writer_methods(method):
    ctx = live.ReadTimeToolContext(
        user_id="user", workspace_id="ws", scope="entry:entry", read_only=True
    )
    with pytest.raises(PermissionError, match="read-time projection"):
        getattr(ctx, method)
