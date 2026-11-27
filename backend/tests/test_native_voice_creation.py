"""Voice creation cannot widen the exact independently reviewed write."""

from types import SimpleNamespace

import pytest

from app.agentive.staging import StagingError
from app.services.native_voice_creation import (
    check_creation_schema,
    confirmed_creation,
    permits_creation,
    validate_confirmed_creation,
)


def test_creation_requires_exact_scope_schema_values_and_no_hooks(monkeypatch):
    from app.services.hooks import registry

    hooks = []
    monkeypatch.setattr(registry, "get_workspace_hooks", lambda *_: hooks)
    track = SimpleNamespace(id="track", workspace_id="workspace")
    entry_type = SimpleNamespace(id="type")
    spec = {"fields": [{"key": "due", "type": "date"}]}
    fields = {"due": "2026-11-25"}
    creation = {
        "schema": check_creation_schema(track, entry_type, spec),
        "track": track.id,
        "type": entry_type.id,
        "title": "Water plants",
        "body": "",
        "fields": fields,
    }
    review = SimpleNamespace(proposal_id="one-proposal", creation=creation, children=())
    assert not permits_creation("one-proposal")

    with confirmed_creation(review):
        assert permits_creation("one-proposal") and not permits_creation("other")
        validate_confirmed_creation(
            track, entry_type, spec, "Water plants", "", fields, [], []
        )
        for title, body, changed, changed_spec in [
            ("Another task", "", fields, spec),
            ("Water plants", "Hidden content", fields, spec),
            ("Water plants", "", {"due": "2026-11-26"}, spec),
            ("Water plants", "", fields, {"fields": [{"key": "due", "type": "text"}]}),
        ]:
            with pytest.raises(StagingError):
                validate_confirmed_creation(
                    track, entry_type, changed_spec, title, body, changed, [], []
                )
        hooks.append("new-app-hook")
        with pytest.raises(StagingError):
            validate_confirmed_creation(
                track, entry_type, spec, "Water plants", "", fields, [], []
            )
    assert not permits_creation("one-proposal")


@pytest.mark.parametrize(
    ("definition", "values"),
    [
        ({}, {"lead": "someone"}),
        ({"required": True}, {}),
        ({"required_on_create": True}, {}),
        ({"default": "someone"}, {}),
        ({"default_from_setting": "default_lead"}, {}),
        ({"validation": {"preview_default_tool": "assign"}}, {}),
    ],
)
def test_member_assignment_cannot_enter_simple_voice_creation(
    monkeypatch, definition, values
):
    from app.services.hooks import registry

    monkeypatch.setattr(registry, "get_workspace_hooks", lambda *_: [])
    track = SimpleNamespace(id="track", workspace_id="workspace")
    field = {"key": "lead", "type": "member"}
    kind = SimpleNamespace(id="type")
    check_creation_schema(track, kind, {"fields": [field]}, {})
    with pytest.raises(StagingError):
        check_creation_schema(
            track, kind, {"fields": [{**field, **definition}]}, values
        )


def test_fast_card_outcome_and_failed_retry_are_each_announced_once():
    import hashlib

    from app.services.native_voice import VoiceRelay, VoiceScope
    from app.services.native_voice_thread_status import VoiceThreadStatus, _ReviewProof
    from tests.test_native_voice import Browser, Provider

    relay = VoiceRelay(
        Browser(), Provider(), VoiceScope("user", "workspace", "", "thread")
    )
    proof = _ReviewProof(
        ("user", "workspace", "thread"),
        hashlib.sha256(b"one").hexdigest(),
        "update_entry",
        "intent",
        "request",
    )
    relay._observe_review_outcomes(VoiceThreadStatus())
    # The card may be clicked and applied between successive observations.
    applied = VoiceThreadStatus(outcomes=((proof, "applied"),))
    relay._observe_review_outcomes(applied)
    assert len(relay.peer_readouts) == 1
    relay._observe_review_outcomes(applied)
    assert len(relay.peer_readouts) == 1
    other = _ReviewProof(
        proof.scope, "other-digest", proof.kind, proof.intent, proof.request_hash
    )
    relay._observe_review_outcomes(VoiceThreadStatus(outcomes=((other, "failed"),)))
    relay._observe_review_outcomes(VoiceThreadStatus(outcomes=((other, "applied"),)))
    assert len(relay.peer_readouts) == 3
    relay._mark_review_spoken("one", "applied")
    assert len(relay.peer_readouts) == 2
    relay._observe_review_outcomes(applied)
    assert len(relay.peer_readouts) == 2


@pytest.mark.parametrize(
    "note,allowed",
    [
        ("8:00", True),
        ("8 AM — phone Sam & Dee.", True),
        ("**different text**", False),
        ("[label](https://example.org)", False),
        ("<em>HTML</em>", False),
        ("~~obsolete~~", False),
        ("www.example.com", False),
        ("1. first", False),
        ("line\nline", False),
        ({"text": "structured"}, False),
        ("x" * 201, False),
        ("call\u202e now", False),
    ],
)
def test_can_read_literal_note_in_full(note, allowed):
    from app.services.native_voice_creation import is_spoken_note

    assert is_spoken_note(note) is allowed


def test_numeric_text_is_read_as_digits_without_changing_quantities():
    from app.services.native_voice_creation import _spoken_value

    assert _spoken_value("088442") == "zero, eight, eight, four, four, two"
    assert _spoken_value(88442) == 88442
    assert _spoken_value("ZX-4") == "ZX-4"


def test_spoken_review_names_each_field_but_reads_identical_text_once():
    from app.services.native_voice_creation import _spoken_fields

    assert _spoken_fields(
        [("Description", "Blue"), ("Notes", "Blue"), ("Count", 1), ("Priority", True)]
    ) == ["Description and Notes set to Blue", "Count set to 1", "Priority set to True"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("A short note", "A short note."),
        (
            "Plan follows.\n\n- Check the estimate.\n- Confirm delivery on Thursday.",
            "Plan follows. Check the estimate. Confirm delivery on Thursday.",
        ),
        ("**formatted**", None),
        ("- item\n  - nested", None),
        ("Look at [this](https://example.org)", None),
        ("<script>bad</script>", None),
        ("x" * 801, None),
    ],
)
def test_bounded_spoken_description_preserves_every_plain_clause(body, expected):
    from app.services.native_voice_creation import spoken_description

    assert spoken_description(body) == expected
