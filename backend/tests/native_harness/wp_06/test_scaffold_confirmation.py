from app.services.chat_providers.pydantic_ai_provider import (
    _SCAFFOLD_CONFIRMATION_INVITATION,
    _single_scaffold_confirmation,
)


def test_proposal_has_one_canonical_confirmation_after_later_explanation():
    proposal = (
        "The setup is ready. Confirm this design, or tell me what to change. "
        "If you'd prefer another view, tell me what to change."
    )

    normalized = _single_scaffold_confirmation(proposal)

    assert normalized.count("Confirm this design") == 0
    assert normalized.count(_SCAFFOLD_CONFIRMATION_INVITATION) == 1
    assert normalized.startswith("The setup is ready.")


def test_proposal_removes_repeated_design_and_setup_invitations():
    proposal = (
        "Confirm this design, or tell me what to change.\n"
        "Confirm this setup when you're ready, or tell me what to change."
    )

    normalized = _single_scaffold_confirmation(proposal)

    assert normalized == _SCAFFOLD_CONFIRMATION_INVITATION
