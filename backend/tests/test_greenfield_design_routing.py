"""Host greenfield routing — App as verb object, never entry work / Prompt Sheet."""

from __future__ import annotations

import pytest

from app.api.ai_chat import (
    _is_explicit_greenfield_design_request,
    _is_prompt_sheet_resume,
    _requires_greenfield_proposal,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I need an app to manage appliance service requests.", True),
        ("I need a new payroll app.", True),
        ("I need another app to manage invoices.", True),
        ("Build the app.", True),
        ("Create an app called Field Ops.", True),
        ("Please set up an operational app for job tickets.", True),
        (
            'Approved — Create entry "Fabrikam Mobile App" in Project Proposals',
            False,
        ),
        (
            "Hey can you populate CRM with some dummy data, no more than 5 - 10 records",
            False,
        ),
        ("What else can you do", False),
        ("Show me existing apps and do not build anything.", False),
        # Record work under an existing app — not greenfield scaffold.
        ("Let's create dummy entries under contacts for the CRM app", False),
        (
            "Hey can you remove all the entries from project proposals "
            "then create some dummy entries under the CRM",
            False,
        ),
        ("Create entries in the Sales app", False),
        ("Add records under my CRM app", False),
    ],
)
def test_greenfield_app_need_is_product_noun_not_title_token(
    text: str, expected: bool
) -> None:
    assert _is_explicit_greenfield_design_request(text) is expected


def test_prompt_sheet_resume_never_requires_greenfield_proposal() -> None:
    resume = (
        "[PROMPT_SHEET]\n"
        "Resolved prompts\n"
        '* Approved — Create entry "Fabrikam Mobile App" in Project Proposals\n'
        "<!-- INTEGRAL_AGENT_DIRECTIVE\n"
        "The approved writes above have already been applied.\n"
        "-->"
    )
    assert _is_prompt_sheet_resume(resume)
    assert not _is_explicit_greenfield_design_request(resume)
    assert not _requires_greenfield_proposal(resume, None)
    assert not _requires_greenfield_proposal(
        resume, {"approved": True, "build_receipt": None}
    )
