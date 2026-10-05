"""email.sent hook dispatch after outbound mail."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.hooks.email_sent_runtime import run_email_sent_hooks
from app.services.hooks.registry import (
    clear_workspace_registrations,
    register_workspace_hooks,
    register_workspace_tools,
)


@pytest.mark.asyncio
async def test_run_email_sent_hooks_invokes_registered_tool():
    ws = "n.Workspace.emailtest"
    clear_workspace_registrations(ws)
    register_workspace_tools(
        ws,
        "email_log",
        [
            {
                "key": "email_log_record",
                "handler_ref": "tools.log_outbound:record",
                "parameters_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effects": "write",
            }
        ],
    )
    register_workspace_hooks(
        ws,
        "email_log",
        [
            {
                "point": "email.sent",
                "key": "email_log_on_sent",
                "mode": "tool",
                "tool": "email_log_record",
                "match": {},
            }
        ],
    )
    with patch(
        "app.services.hooks.tool_dispatch.run_tool",
        new=AsyncMock(return_value={"ok": True}),
    ) as run_tool:
        await run_email_sent_hooks(
            workspace_id=ws,
            actor_id="o.User.admin",
            payload={"to": "a@b.com", "subject": "Hi", "status": "sent"},
        )
    run_tool.assert_awaited_once()
    clear_workspace_registrations(ws)
