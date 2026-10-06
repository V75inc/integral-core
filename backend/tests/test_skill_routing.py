"""Routing cases have one skill owner."""

from app.services.skill_routing import ROUTING, owner_for
from app.services.turn_binding import (
    is_improve_this,
    is_rate_dashboard,
    rename_tag_pair,
)


def test_each_routing_case_has_one_owner():
    """Each routing case names one skill."""
    assert owner_for("follow_up") == "integral_insights"
    assert owner_for("correction") == "integral_scaffold"
    assert owner_for("rejection") == "integral_scaffold"
    assert owner_for("resume") == "integral_scaffold"
    owners = {owner_for(case) for case in ROUTING}
    assert owners == {"integral_insights", "integral_scaffold"}


def test_tag_rename_is_not_a_field_rename():
    """A tag rename sentence is not parsed as a field key pair."""
    assert rename_tag_pair("Rename the Economy tag to Compact") == (
        "Economy",
        "Compact",
    )


def test_user_sentence_ignores_the_host_note():
    """A host note above the divider is not the person's request."""
    from app.services.turn_binding import user_sentence

    wrapped = (
        "[SYSTEM:EXISTING-SCHEMA-FIELD-REQUEST]\n"
        "asking to add or change a field on the existing track n.Track.x.\n\n"
        "---\n\n"
        "Improve this"
    )
    assert user_sentence(wrapped) == "Improve this"
    assert is_improve_this(user_sentence(wrapped))
    assert is_improve_this("Improve this")
    assert is_rate_dashboard("Show a dashboard of the vehicle daily rates")
