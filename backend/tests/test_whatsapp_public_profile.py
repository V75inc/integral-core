"""Public-profile selection for a WhatsApp line. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agentive.services.web_chat_profile_inference import public_text_model_input
from app.agentive.services.whatsapp_public_profile import (
    UNAVAILABLE_NOTICE,
    PublicLineGrant,
    choose_public_grant,
    unknown_sender_reply,
    visitor_session_key,
)
from app.connectors.bundled_packages.whatsapp.adapter import grants


def _grant(workspace_id: str, grant_id: str) -> PublicLineGrant:
    return PublicLineGrant(
        workspace_id=workspace_id,
        grant_id=grant_id,
        revision=2,
        profile_key="greeter",
        published_version=1,
        persona="Be brief.",
        principal_id="n.User.profile",
    )


@pytest.mark.unit
def test_session_key_hides_the_phone_and_changes_with_revision():
    """The visitor session is a hash of grant, revision, and sender."""
    first = visitor_session_key("grant-1", 2, "+15551212")
    again = visitor_session_key("grant-1", 2, "+15551212")
    revised = visitor_session_key("grant-1", 3, "+15551212")

    assert first == again
    assert first != revised
    assert len(first) == 64
    assert "+15551212" not in first
    assert "15551212" not in first


@pytest.mark.unit
def test_public_text_input_has_no_tools():
    """The shared public prompt is plain text."""
    prompt = public_text_model_input("Be brief.", (), "hello")

    assert "no tools" in prompt.instructions
    assert prompt.persona == "Be brief."
    assert prompt.visitor_text == "hello"
    assert not hasattr(prompt, "tools")


@pytest.mark.unit
def test_choose_public_grant_requires_exactly_one():
    """Zero or several live grants do not select a workspace."""
    assert choose_public_grant([]) is None
    assert choose_public_grant([_grant("ws-1", "g1"), _grant("ws-2", "g2")]) is None
    chosen = choose_public_grant([_grant("ws-1", "g1")])
    assert chosen is not None and chosen.grant_id == "g1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reply_stops_when_the_public_grant_is_no_longer_live(monkeypatch):
    """A grant that disappears before the model call is not answered."""
    grant = _grant("ws-1", "g1")
    reads = {"n": 0}
    completed: list[str] = []

    async def grants(_binding):
        reads["n"] += 1
        return [grant] if reads["n"] == 1 else []

    async def complete(_prompt):
        completed.append("called")
        return "hello"

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_public_profile.live_public_profile_grants",
        grants,
    )
    reply = await unknown_sender_reply(
        object(),
        "+15551212000",
        "hello",
        event_id="evt-1",
        complete=complete,
    )

    assert reply == UNAVAILABLE_NOTICE
    assert completed == []
    assert reads["n"] == 2


@pytest.mark.unit
def test_whatsapp_profile_setup_is_not_a_visitor_surface():
    """The phone line can assign a public profile without publishing an embed."""
    package = grants.package()

    assert package.profile_setup is not None
    assert package.definition.visitor_surface is False
    source = Path(grants.__file__).read_text(encoding="utf-8")
    assert "visitor_surface=True" not in source


@pytest.mark.unit
def test_public_profile_module_does_not_open_native_voice():
    """Unknown callers stay out of member voice and staged writes."""
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "services"
        / "whatsapp_public_profile.py"
    ).read_text(encoding="utf-8")
    assert "native_voice" not in source
    assert "decide_staged_write" not in source
    assert "admit_owner_whatsapp_text" not in source
    assert "tools=" not in source
