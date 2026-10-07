"""E-sign dispatch and contract helpers."""

import jsonschema
import pytest

from app.services.esign.contract_decision import CONTRACT_REJECTED, reject_contract
from app.services.esign.dispatch import (
    _omit_null_tool_fields,
    dispatch_contract_decision,
)

_E_SIGN_CONTRACT_PARAMETERS_SCHEMA = {
    "type": "object",
    "required": ["action", "form_entry_id"],
    "properties": {
        "action": {"type": "string"},
        "form_entry_id": {"type": "string"},
        "track_id": {"type": "string"},
        "signature_png": {"type": "string"},
        "reason": {"type": "string"},
        "return_url": {"type": "string"},
        "actor_id": {"type": "string"},
        "use_docusign": {"type": "boolean"},
    },
}


def test_accept_contract_payload_omits_null_optional_fields():
    raw = {
        "action": "accept",
        "form_entry_id": "n.Entry.form",
        "track_id": "n.Track.t",
        "signature_png": "data:image/png;base64,abc",
        "reason": None,
        "return_url": "",
        "actor_id": "n.User.u",
        "use_docusign": True,
    }
    cleaned = _omit_null_tool_fields(raw)
    jsonschema.validate(instance=cleaned, schema=_E_SIGN_CONTRACT_PARAMETERS_SCHEMA)
    assert "reason" not in cleaned
    assert cleaned["signature_png"].startswith("data:image/png")


@pytest.mark.asyncio
async def test_dispatch_contract_decision_without_e_sign_app():
    result = await dispatch_contract_decision(
        workspace_id="ws-no-e-sign-installed",
        actor_user_id="u.test",
        payload={
            "action": "reject",
            "form_entry_id": "n.Entry.test",
            "track_id": "n.Track.test",
        },
    )
    assert result is None


@pytest.mark.asyncio
async def test_reject_contract_updates_entry():
    from unittest.mock import AsyncMock, MagicMock

    entry = MagicMock()
    entry.custom_fields = {"contract_status": "pending"}
    entry.save = AsyncMock()
    out = await reject_contract(form_entry=entry, reason="declined")
    assert out["contract_status"] == CONTRACT_REJECTED
    assert entry.custom_fields["contract_reject_reason"] == "declined"
    entry.save.assert_awaited_once()
