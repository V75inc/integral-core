"""CRM bundle tool — normalize_email_for_match (Phase 30 Wave C Task C3).

Phase 31 (DR-31-01 §4) — the tool stays in the **crm** bundle (its
``connector.auto_link`` hook (``gmail_thread_to_contact``) targets Contact
entries owned by the CRM bundle post-decomposition).
"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_normalize_matches_lowercased_emails():
    from app.profiles.crm.tools.email_match import normalize
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u", workspace_id="w", scope="entry:t")
    ctx.find_entries = AsyncMock(
        return_value=[
            type(
                "E",
                (),
                {"id": "n.Entry.A", "custom_fields": {"email": "maya@contoso.example"}},
            )(),
            type(
                "E",
                (),
                {"id": "n.Entry.B", "custom_fields": {"email": "other@example.com"}},
            )(),
        ]
    )
    out = await normalize(
        {
            "participants": ["  Maya@Contoso.example  ", "unknown@nowhere"],
            "target_track_type": "contacts",
            "target_match_field": "custom_fields.email",
        },
        ctx,
    )
    assert out["matched_entry_ids"] == ["n.Entry.A"]
    assert "unknown@nowhere" in out["unmatched_participants"]
