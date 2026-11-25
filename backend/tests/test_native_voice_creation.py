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
    review = SimpleNamespace(proposal_id="one-proposal", creation=creation)
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
