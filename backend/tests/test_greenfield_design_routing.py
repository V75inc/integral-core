"""Host greenfield routing — the model decides a new App; resumes never do."""

from __future__ import annotations

import pytest

from app.api.ai_chat import (
    _NEW_APP_SYSTEM,
    _is_explicit_greenfield_design_request,
    _is_prompt_sheet_resume,
    _requires_greenfield_proposal,
)


def test_new_app_judge_is_not_an_english_phrase_list() -> None:
    assert "any language" in _NEW_APP_SYSTEM
    assert "already exists" in _NEW_APP_SYSTEM


@pytest.mark.asyncio
async def test_prompt_sheet_resume_never_requires_greenfield_proposal(
    monkeypatch,
) -> None:
    called = {"n": 0}

    async def judge(text, **_kwargs):
        called["n"] += 1
        return True

    monkeypatch.setattr("app.api.ai_chat._user_wants_new_app", judge)
    resume = (
        "[PROMPT_SHEET]\n"
        "Resolved prompts\n"
        '* Approved — Create entry "Fabrikam Mobile App" in Project Proposals\n'
        "<!-- INTEGRAL_AGENT_DIRECTIVE\n"
        "The approved writes above have already been applied.\n"
        "-->"
    )
    assert _is_prompt_sheet_resume(resume)
    assert not await _is_explicit_greenfield_design_request(resume)
    assert not await _requires_greenfield_proposal(resume, None)
    assert not await _requires_greenfield_proposal(
        resume, {"approved": True, "build_receipt": None}
    )
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_model_yes_requires_a_proposal_until_the_design_is_affirmed(
    monkeypatch,
) -> None:
    async def yes(text, **_kwargs):
        return True

    monkeypatch.setattr("app.api.ai_chat._user_wants_new_app", yes)
    assert await _requires_greenfield_proposal("crea una aplicación", None)
    assert not await _requires_greenfield_proposal(
        "yes", {"approved": False, "proposed_at_user_turn": 1}
    )
